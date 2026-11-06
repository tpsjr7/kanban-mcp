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
