"""Exercise native framing without a desktop or model call."""
import importlib.util
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).parents[1] / 'scripts/kanban/desktop_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FramingTests(unittest.TestCase):
    def run_response(self, response):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'test.sock')
            server = socket.socket(socket.AF_UNIX)
            server.bind(path)
            server.listen(1)
            def serve():
                with server.accept()[0] as peer:
                    size = struct.unpack('<I', bridge.receive(peer, 4))[0]
                    bridge.receive(peer, size)
                    body = json.dumps(response).encode()
                    frame = struct.pack('<I', len(body)) + body
                    for byte in frame:
                        peer.sendall(bytes([byte]))
                server.close()
            worker = threading.Thread(target=serve)
            worker.start()
            try:
                return bridge.request(path, 'tools/list', {})
            finally:
                worker.join()

    def test_discovery_rejects_missing_and_ambiguous_owners(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'net').mkdir()
            (root / 'net/unix').write_text('header\n')
            with self.assertRaises(RuntimeError):
                bridge.endpoint(root, directory)
            process = root / '123'
            (process / 'fd').mkdir(parents=True)
            (process / 'exe').symlink_to('/usr/lib/chatgpt/ChatGPT')
            listeners = []
            try:
                for number in (1, 2):
                    path = root / f'{number}.sock'
                    listener = socket.socket(socket.AF_UNIX)
                    listener.bind(str(path))
                    path.chmod(0o600)
                    listeners.append(listener)
                    (process / 'fd' / str(number)).symlink_to(f'socket:[{number}]')
                    with (root / 'net/unix').open('a') as output:
                        output.write(f'0: 0 0 00010000 0001 01 {number} {path}\n')
                    if number == 1:
                        self.assertEqual(bridge.endpoint(root, directory), str(path))
                with self.assertRaises(RuntimeError):
                    bridge.endpoint(root, directory)
            finally:
                for listener in listeners:
                    listener.close()

    def test_default_directories_follow_current_uid(self):
        from unittest.mock import patch
        with patch.object(bridge.os, 'getuid', return_value=4321):
            self.assertEqual(bridge.socket_prefixes(),
                             ('/tmp/codex-app-tools/', '/tmp/codex-app-tools-4321/'))

    def test_uid_layout_rotation_is_selected_by_live_owner(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'net').mkdir()
            process = root / '123'
            (process / 'fd').mkdir(parents=True)
            (process / 'exe').symlink_to('/usr/lib/chatgpt/ChatGPT')
            parent = root / ('codex-app-tools-' + str(os.getuid()))
            parent.mkdir(mode=0o700)
            for generation in ('old', 'new'):
                path = parent / (generation + '.sock')
                with socket.socket(socket.AF_UNIX) as listener:
                    listener.bind(str(path))
                    path.chmod(0o600)
                    descriptor = process / 'fd/1'
                    descriptor.unlink(missing_ok=True)
                    descriptor.symlink_to('socket:[10]')
                    (root / 'net/unix').write_text(
                        f'header\n0: 0 0 00010000 0001 01 10 {path}\n')
                    # Prefixes use the real UID, not a fixed 1000, while this
                    # fixture maps /tmp into its private temporary directory.
                    with patch.object(bridge, 'socket_prefixes', return_value=(str(parent) + '/',)):
                        self.assertEqual(bridge.endpoint(root), str(path))
            # Old socket files remain, but only the live listener is considered.
            self.assertTrue((parent / 'old.sock').exists())

    def test_discovery_rejects_nonlistener_symlink_and_insecure_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'net').mkdir()
            process = root / '123'
            (process / 'fd').mkdir(parents=True)
            (process / 'exe').symlink_to('/usr/lib/chatgpt/ChatGPT')
            (process / 'fd/1').symlink_to('socket:[10]')
            path = root / 'real.sock'
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                (root / 'net/unix').write_text(f'header\n0: 0 0 0 0001 03 10 {path}\n')
                with self.assertRaises(RuntimeError):
                    bridge.endpoint(root, directory)
                link = root / 'link.sock'
                link.symlink_to(path)
                (root / 'net/unix').write_text(f'header\n0: 0 0 00010000 0001 01 10 {link}\n')
                with self.assertRaises(RuntimeError):
                    bridge.endpoint(root, directory)
                (root / 'net/unix').write_text(f'header\n0: 0 0 00010000 0001 01 10 {path}\n')
                root.chmod(0o777)
                with self.assertRaises(RuntimeError):
                    bridge.endpoint(root, directory)
                root.chmod(0o700)
                path.chmod(0o666)
                with self.assertRaises(RuntimeError):
                    bridge.endpoint(root, directory)
                path.chmod(0o600)
                (process / 'exe').unlink()
                (process / 'exe').symlink_to('/usr/lib/chatgpt/resources/codex')
                with self.assertRaises(RuntimeError):
                    bridge.endpoint(root, directory)

    def test_advertised_dynamic_path_is_only_a_verified_hint(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'net').mkdir()
            process = root / '123'
            (process / 'fd').mkdir(parents=True)
            (process / 'exe').symlink_to('/usr/lib/chatgpt/ChatGPT')
            # readlink raises for this vanished/invalid descriptor. Discovery
            # must continue to the live listener rather than skip the process.
            (process / 'fd/0').write_text('closed descriptor fixture')
            (process / 'fd/1').symlink_to('socket:[10]')
            path = root / 'generated.sock'
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                (root / 'net/unix').write_text(f'header\n0: 0 0 00010000 0001 01 10 {path}\n')
                with patch.object(bridge, 'socket_prefixes', return_value=()), \
                     patch.dict(bridge.os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': str(path)}):
                    self.assertEqual(bridge.endpoint(root), str(path))
                    (process / 'exe').unlink()
                    (process / 'exe').symlink_to('/usr/bin/unrelated')
                    with self.assertRaises(RuntimeError):
                        bridge.endpoint(root)

    def test_history_is_bounded_and_preserves_exact_user_id_text_and_time(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'desktop-caller.json').write_text(json.dumps({
                'thread_id': 'caller', 'turn_id': 'caller-turn'}))
            history = {'thread': {'id': 'exact-thread'}, 'turns': [{
                'startedAt': 1793999044, 'items': [
                    {'id': 'human-message', 'type': 'userMessage',
                     'content': [{'type': 'text', 'text': 'Exact text\n'}]},
                    {'id': 'agent-message', 'type': 'agentMessage', 'text': 'Not authority'}]}]}
            responses = [{'tools': [{'name': 'read_thread', 'namespace': 'codex_app'}]},
                         {'success': True, 'contentItems': [{'type': 'inputText',
                                                            'text': json.dumps(history)}]}]
            output = io.StringIO()
            with patch.dict(bridge.os.environ, {'KANBAN_AUTOMATION_STATE': directory}), \
                 patch('sys.argv', ['bridge', 'history', 'exact-thread']), \
                 patch.object(bridge, 'endpoint', return_value='verified-socket'), \
                 patch.object(bridge, 'request', side_effect=responses) as request, \
                 redirect_stdout(output):
                bridge.main()
            result = json.loads(output.getvalue())
            self.assertEqual(result['id'], 'exact-thread')
            self.assertEqual(len(result['messages']), 1)
            self.assertEqual(result['messages'][0]['id'], 'human-message')
            self.assertEqual(result['messages'][0]['source_time'], 1793999044)
            self.assertEqual(result['messages'][0]['content'][0]['text'], 'Exact text\n')
            arguments = request.call_args_list[1].args[2]['arguments']
            self.assertEqual(arguments['turnLimit'], 10)
            self.assertEqual(arguments['threadId'], 'exact-thread')

    def test_fragmented_success(self):
        self.assertEqual(self.run_response({'id':1,'result':{'tools':[]}}), {'tools':[]})

    def test_wrong_request_id(self):
        with self.assertRaises(RuntimeError):
            self.run_response({'id':2,'result':{}})

    def test_explicit_failure(self):
        with self.assertRaises(RuntimeError):
            self.run_response({'id':1,'error':{'message':'blocked'}})


if __name__ == '__main__':
    unittest.main()
