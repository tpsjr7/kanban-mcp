"""Live acceptance guard: forward real MCP calls, allowing fixture-scoped writes only."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

def configuration(config_path):
    info = config_path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Private owned acceptance configuration required')
    return json.loads(config_path.read_text())


def allowed(name, arguments, config):
    if name == 'get_current_project':
        return True
    if name in ('workflow_ticket', 'get_item'):
        return arguments.get('item_id') in config['read_items']
    if name == 'workflow_check':
        return arguments.get('dispatch_id') in config['dispatches']
    if name == 'workflow_revise_plan':
        return (arguments.get('item_id') == config['item_id']
                and arguments.get('client') == config['client']
                and arguments.get('chat_id') == config['chat_id'])
    if name == 'workflow_report':
        if (arguments.get('dispatch_id') not in config['dispatches']
                or arguments.get('client') != config['client']
                or arguments.get('chat_id') != config['chat_id']):
            return False
        if arguments.get('kind') == 'started':
            return True
        if arguments.get('kind') == 'plan_ready':
            return json.loads(arguments.get('payload', '{}')) == {'plan': config['plan']}
    return False


def main():
    config_path = Path(sys.argv[1])
    config = configuration(config_path)
    child = subprocess.Popen(config['command'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=sys.stderr, text=True)
    exposed = ('get_current_project', 'workflow_ticket', 'get_item', 'workflow_check',
               'workflow_revise_plan', 'workflow_report')
    try:
        for line in sys.stdin:
            request = json.loads(line)
            config = configuration(config_path)
            params = request.get('params', {})
            if request.get('method') == 'tools/call' and not allowed(params.get('name'), params.get('arguments', {}), config):
                print(json.dumps({'jsonrpc': '2.0', 'id': request.get('id'), 'result': {
                    'isError': True, 'content': [{'type': 'text', 'text': 'Outside authorized acceptance fixture scope'}]}}), flush=True)
                continue
            child.stdin.write(line)
            child.stdin.flush()
            if 'id' not in request:
                continue
            for response_line in child.stdout:
                response = json.loads(response_line)
                if request.get('method') == 'tools/list' and response.get('id') == request['id']:
                    tools = response.get('result', {}).get('tools', [])
                    response['result']['tools'] = [tool for tool in tools if tool['name'] in exposed]
                print(json.dumps(response), flush=True)
                if response.get('id') == request['id']:
                    break
            else:
                raise RuntimeError('Real MCP server closed before its response')
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=10)


if __name__ == '__main__':
    main()
