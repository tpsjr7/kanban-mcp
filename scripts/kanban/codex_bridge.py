#!/usr/bin/env python3
"""Read or message an existing Codex chat through the local daemon's Unix WebSocket."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


class BridgeError(Exception):
    pass


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


class Client:
    def __init__(self, ws, timeout=30):
        self.ws = ws
        self.timeout = timeout
        self.ident = 0

    async def request(self, method, params):
        self.ident += 1
        ident = self.ident
        await self.ws.send(json.dumps({'id': ident, 'method': method, 'params': params}))
        # One absolute deadline: unrelated notifications must not extend the timeout.
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                raise TimeoutError('Request timed out: ' + method)
            try:
                message = json.loads(await asyncio.wait_for(self.ws.recv(), left))
            except asyncio.TimeoutError as exc:
                raise TimeoutError('Request timed out: ' + method) from exc
            except Exception as exc:
                raise BridgeError('Response unavailable for ' + method + ': ' + str(exc)) from exc
            if 'method' in message and 'id' in message:
                raise BridgeError('Server requests user/tool action: ' + message['method'] +
                                  '. Handle it in the owning app; this helper will not approve it.')
            if message.get('id') != ident:
                continue
            if 'error' in message:
                raise BridgeError(json.dumps(message['error'], ensure_ascii=False))
            return message.get('result', {})

    async def initialize(self):
        await self.request('initialize', {'clientInfo': {'name': 'codex_chat_bridge', 'version': '1.1'}})
        await self.ws.send(json.dumps({'method': 'initialized', 'params': {}}))

    async def read(self, thread_id):
        # Full-history hydration can exceed the WebSocket frame limit on an
        # established chat. Read identity/status first, then page recent turns.
        result = await self.request('thread/read', {'threadId': thread_id, 'includeTurns': False})
        thread = result['thread']
        if thread.get('id') != thread_id:
            raise BridgeError('Response thread identity does not match the requested ID.')
        return thread

    async def turns_page(self, thread_id, limit=10, cursor=None):
        params = {'threadId': thread_id, 'limit': limit,
                  'sortDirection': 'desc', 'itemsView': 'summary'}
        if cursor is not None:
            params['cursor'] = cursor
        result = await self.request('thread/turns/list', params)
        if not isinstance(result.get('data'), list):
            raise BridgeError('Invalid thread/turns/list response.')
        return result

    async def recent_turns(self, thread_id, count):
        result = await self.turns_page(thread_id, limit=count)
        return list(reversed(result['data']))

    async def find_turn(self, thread_id, turn_id):
        cursor = None
        for _ in range(10):
            page = await self.turns_page(thread_id, cursor=cursor)
            turn = next((t for t in page['data'] if t.get('id') == turn_id), None)
            if turn is not None:
                return turn
            next_cursor = page.get('nextCursor')
            if not next_cursor or next_cursor == cursor:
                return None
            cursor = next_cursor
        raise BridgeError('Target turn is outside the recent paginated history; inspect it in the owning app.')


def item_text(item):
    if isinstance(item.get('text'), str):
        return item['text']
    return '\n'.join(part.get('text', '') for part in item.get('content', [])
                     if part.get('type') == 'text')


def summary(thread, count=5, limit=1800):
    result = {k: thread.get(k) for k in ('id', 'name', 'status', 'cwd')}
    result['turns'] = []
    for turn in thread.get('turns', [])[-count:]:
        result['turns'].append({
            'id': turn.get('id'), 'status': turn.get('status'), 'error': turn.get('error'),
            'messages': [{'type': item['type'], 'text': item_text(item)[:limit]}
                         for item in turn.get('items', [])
                         if item.get('type') in ('userMessage', 'agentMessage')],
        })
    return result


async def wait_for_turn(client, thread_id, turn_id, seconds, poll=2):
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        left = deadline - asyncio.get_running_loop().time()
        if left <= 0:
            raise TimeoutError('Turn not verified before deadline; read again, do not resend.')
        thread = await asyncio.wait_for(client.read(thread_id), left)
        turn = await asyncio.wait_for(client.find_turn(thread_id, turn_id), left)
        if turn and turn.get('status') == 'completed':
            emit({'event': 'completed', 'threadId': thread_id, 'turnId': turn_id,
                  'reply': '\n'.join(item_text(i) for i in turn.get('items', [])
                                     if i.get('type') == 'agentMessage')})
            return
        if turn and turn.get('status') in ('failed', 'interrupted'):
            raise BridgeError('Target turn ended without completion: ' + json.dumps(turn.get('error') or turn['status']))
        status = thread.get('status') or {}
        if any(flag in ('waitingOnApproval', 'waitingOnUserInput') for flag in status.get('activeFlags', [])):
            raise BridgeError('Target needs user attention in the owning app: ' + json.dumps(status))
        left = deadline - asyncio.get_running_loop().time()
        if left <= 0:
            raise TimeoutError('Turn not verified before deadline; read again, do not resend.')
        await asyncio.sleep(min(poll, left))


async def operate(client, args):
    if args.command == 'wait':
        await wait_for_turn(client, args.thread_id, args.turn_id, args.timeout)
        return
    thread = await client.read(args.thread_id)
    if args.command == 'read':
        thread['turns'] = await client.recent_turns(args.thread_id, args.turns)
        emit(summary(thread, args.turns, args.text_limit))
        return
    if args.expected_name and thread.get('name') != args.expected_name:
        raise BridgeError('Target name differs from --expected-name; nothing sent.')
    status = (thread.get('status') or {}).get('type')
    if status not in ('idle', 'notLoaded'):
        raise BridgeError('Target is not idle: ' + str(status) + '; nothing sent.')
    # An already idle thread is loaded. Resuming it would hydrate history and
    # can exceed the socket message limit without changing its state.
    if status == 'notLoaded':
        # Do not override target model, cwd, permissions, or other configuration.
        try:
            await client.request('thread/resume',
                                 {'threadId': args.thread_id, 'metadataOnly': True})
        except Exception as exc:
            # Some app-server versions send an oversized resume response and
            # close the socket even though the chat becomes idle. No turn has
            # been submitted here. Re-read through a fresh invocation before
            # deciding whether to send; do not guess from this exception.
            raise BridgeError(
                'Resume receipt unavailable; no turn/start was attempted. '
                'Read the target again in a new invocation. If it is idle '
                'and no new message appears, rerun send. Cause: ' + str(exc)
            ) from exc
        thread = await client.read(args.thread_id)
        if (thread.get('status') or {}).get('type') != 'idle':
            raise BridgeError('Target is no longer idle after resume; nothing sent.')
    message = args.message if args.message is not None else Path(args.message_file).read_text(encoding='utf-8')
    if not message.strip():
        raise BridgeError('Empty message; nothing sent.')
    emit({'event': 'submitting', 'threadId': args.thread_id,
          'note': 'If disconnected now, inspect the thread before retrying; never resend automatically.'})
    try:
        result = await client.request('turn/start', {'threadId': args.thread_id,
                                      'input': [{'type': 'text', 'text': message}]})
        turn_id = result['turn']['id']
    except Exception as exc:
        raise BridgeError('Submission not verified; read target before retrying. ' + str(exc)) from exc
    emit({'event': 'accepted', 'threadId': args.thread_id, 'turnId': turn_id})
    if args.wait:
        await wait_for_turn(client, args.thread_id, turn_id, args.wait)


def positive(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return value


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket', default=str(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) /
                                          'app-server-control/app-server-control.sock'))
    p.add_argument('--request-timeout', type=positive, default=30)
    p.add_argument('--max-message-mib', type=positive, default=16,
                   help='WebSocket receive limit in MiB (default: 16); increase only for a known local daemon')
    commands = p.add_subparsers(dest='command', required=True)
    read = commands.add_parser('read', help='Read recent messages without resuming the chat')
    read.add_argument('thread_id')
    read.add_argument('--turns', type=positive, default=5)
    read.add_argument('--text-limit', type=positive, default=1800)
    send = commands.add_parser('send', help='Send one explicitly authorized message to an idle chat')
    send.add_argument('thread_id')
    text = send.add_mutually_exclusive_group(required=True)
    text.add_argument('--message')
    text.add_argument('--message-file', help='UTF-8 file containing the exact authorized prompt')
    send.add_argument('--expected-name', help='Refuse sending if the target name differs')
    send.add_argument('--wait', type=positive, metavar='SECONDS', help='Verify completion within this deadline')
    wait = commands.add_parser('wait', help='Verify a known turn without sending anything')
    wait.add_argument('thread_id')
    wait.add_argument('turn_id')
    wait.add_argument('--timeout', type=positive, default=90)
    return p


async def main(args):
    try:
        import websockets
    except ImportError as exc:
        raise BridgeError('Use a Python environment with websockets installed.') from exc
    async with websockets.unix_connect(args.socket, uri='ws://localhost', open_timeout=10,
                                       max_size=args.max_message_mib * 1024 * 1024) as ws:
        client = Client(ws, args.request_timeout)
        await client.initialize()
        await operate(client, args)


if __name__ == '__main__':
    try:
        asyncio.run(main(parser().parse_args()))
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({'event': 'error', 'error': str(exc) or type(exc).__name__,
                          'note': 'No automatic retry was performed.'}), file=sys.stderr)
        sys.exit(1)
