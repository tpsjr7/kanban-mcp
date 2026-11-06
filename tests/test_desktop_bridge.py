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
                        output.write(f'0: 0 0 0 1 1 {number} {path}\n')
                    if number == 1:
                        self.assertEqual(bridge.endpoint(root, directory), str(path))
                with self.assertRaises(RuntimeError):
                    bridge.endpoint(root, directory)
            finally:
                for listener in listeners:
                    listener.close()

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
