"""Read-only stdio acceptance against a configured MCP launcher.

Usage: python tests/smoke_workflow_tools.py --item-id 20 --dispatch-id ID \
    -- node scripts/kanban.mjs mcp
Outputs response sizes and assertions, never ticket or credential contents.
"""
import argparse
import json
import selectors
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--item-id', type=int, required=True)
    parser.add_argument('--dispatch-id', required=True)
    parser.add_argument('launcher', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    launcher = args.launcher[1:] if args.launcher[:1] == ['--'] else args.launcher
    if not launcher:
        parser.error('provide the MCP launcher command after --')
    process = subprocess.Popen(launcher, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               text=True)
    sequence = 0
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)

    def request(method, params):
        nonlocal sequence
        sequence += 1
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': sequence,
                                        'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            line = process.stdout.readline()
            if not line:
                raise RuntimeError('MCP launcher exited before its response')
            response = json.loads(line)
            if response.get('id') != sequence:
                continue
            if 'error' in response:
                raise RuntimeError(response['error'])
            return response['result']
        raise TimeoutError('MCP response exceeded 30 seconds')

    try:
        request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                               'clientInfo': {'name': 'workflow-read-smoke', 'version': '1'}})
        catalog = request('tools/list', {})
        names = {tool['name'] for tool in catalog['tools']}
        required = {'workflow_check', 'workflow_ticket', 'workflow_queue'}
        assert required <= names, 'Reconnect did not load the focused tool catalog'
        sizes = {}
        for name, arguments in [
                ('workflow_check', {'dispatch_id': args.dispatch_id}),
                ('workflow_ticket', {'item_id': args.item_id}),
                ('workflow_queue', {'limit': 1, 'offset': 0}),
                ('workflow_status', {})]:
            result = request('tools/call', {'name': name, 'arguments': arguments})
            assert not result.get('isError'), name + ' failed'
            content = '\n'.join(block['text'] for block in result['content']
                                if block['type'] == 'text')
            value = json.loads(content)
            if name in ('workflow_check', 'workflow_ticket'):
                assert value['found'], name + ' could not find the requested record'
            if name != 'workflow_status':
                assert 'plan_text' not in content and 'plan_text' not in value
            sizes[name] = len(content)
        print(json.dumps({'passed': True, 'response_characters': sizes}))
    finally:
        selector.close()
        process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        process.stdout.close()


if __name__ == '__main__':
    main()
