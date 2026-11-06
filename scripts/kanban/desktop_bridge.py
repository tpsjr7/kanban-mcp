"""Bounded desktop app-tools client. Never retries a submitted message."""
import argparse
import json
import os
from pathlib import Path
import socket
import stat
import struct
import uuid


def endpoint(proc_root=Path('/proc'), socket_prefix='/tmp/codex-app-tools/'):
    candidates = []
    sockets = {}
    for line in (proc_root / 'net/unix').read_text().splitlines()[1:]:
        fields = line.split(maxsplit=7)
        if len(fields) == 8 and fields[7].startswith(socket_prefix):
            sockets['socket:[' + fields[6] + ']'] = fields[7]
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            executable = os.readlink(process / 'exe')
            if not executable.endswith('/chatgpt/ChatGPT'):
                continue
            for descriptor in (process / 'fd').iterdir():
                path = sockets.get(os.readlink(descriptor))
                if path and path not in candidates:
                    info = Path(path).stat()
                    if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600:
                        candidates.append(path)
        except (OSError, ValueError):
            continue
    if len(candidates) != 1:
        raise RuntimeError('Desktop endpoint absent or ambiguous; no alternate server will be used')
    return candidates[0]


def receive(connection, size):
    data = b''
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise RuntimeError('Desktop disconnected; reconcile any submitted send')
        data += chunk
    return data


def request(path, method, params):
    body = json.dumps({'id': 1, 'jsonrpc': '2.0', 'method': method, 'params': params}).encode()
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('Request too large')
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(20)
        connection.connect(path)
        connection.sendall(struct.pack('<I', len(body)) + body)
        size = struct.unpack('<I', receive(connection, 4))[0]
        if not 0 < size <= 8 * 1024 * 1024:
            raise ValueError('Invalid desktop response size')
        response = json.loads(receive(connection, size))
    if response.get('id') != 1 or response.get('error'):
        raise RuntimeError(str(response.get('error', 'Response identity mismatch')))
    return response['result']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['read', 'send'])
    parser.add_argument('thread')
    parser.add_argument('--message')
    args = parser.parse_args()
    state = Path(os.environ.get('KANBAN_AUTOMATION_STATE', str(Path.home() / '.local/state/kanban-mcp/automation')))
    caller = json.loads((state / 'desktop-caller.json').read_text())
    if not caller.get('thread_id') or not caller.get('turn_id'):
        raise ValueError('Configure exact desktop caller identity first')
    path = endpoint()
    catalog = request(path, 'tools/list', {'threadStartKind': 'all'})['tools']
    tool = 'read_thread' if args.action == 'read' else 'send_message_to_thread'
    if not any(row['name'] == tool and row['namespace'] == 'codex_app' for row in catalog):
        raise ValueError('Desktop tool catalog is incompatible')
    arguments = {'threadId': args.thread}
    if args.action == 'read':
        arguments.update(turnLimit=1, maxOutputCharsPerItem=100)
    else:
        arguments['prompt'] = args.message
    result = request(path, 'tools/call', {
        'arguments': arguments, 'callerSource': 'codex', 'callId': str(uuid.uuid4()),
        'namespace': 'codex_app', 'threadId': caller['thread_id'],
        'turnId': caller['turn_id'], 'tool': tool})
    if not result.get('success'):
        raise RuntimeError(str(result))
    content = next(row['text'] for row in result['contentItems'] if row['type'] == 'inputText')
    value = json.loads(content)
    if args.action == 'read':
        thread = value['thread']
        if thread['id'] != args.thread:
            raise ValueError('Desktop target mismatch')
        value = {'id': thread['id'], 'name': thread['title'], 'status': thread['status'], 'route': 'native-mcp'}
    elif value.get('threadId') != args.thread:
        raise ValueError('Send target mismatch; reconcile delivery')
    print(json.dumps(value))


if __name__ == '__main__':
    main()
