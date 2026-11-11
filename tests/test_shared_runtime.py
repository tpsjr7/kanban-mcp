"""Socket session isolation, leases and draining on disposable SQLite."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

scratch = tempfile.TemporaryDirectory()
os.environ.update(
    HOME=scratch.name, KANBAN_BACKEND='sqlite',
    KANBAN_SQLITE_PATH=str(Path(scratch.name) / 'board.db'),
    KANBAN_PROJECT_DIR='/workspace/shared-runtime-tests')
from kanban_mcp.shared_runtime import Service, encode


class SharedRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = Service()
        self.path = str(Path(scratch.name) / 'test.sock')
        Path(self.path).unlink(missing_ok=True)
        self.listener = await asyncio.start_unix_server(self.service.connection, self.path)
        self.writers = []

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
        await asyncio.sleep(0.02)
        self.listener.close()
        await self.listener.wait_closed()
        self.service.prototype.db._backend.close()

    async def connection(self, frame):
        reader, writer = await asyncio.open_unix_connection(self.path)
        self.writers.append(writer)
        writer.write(encode(frame))
        await writer.drain()
        return reader, writer

    async def open(self, identifier):
        reader, writer = await self.connection({'op': 'open', 'session': identifier})
        self.assertTrue(json.loads(await reader.readline())['ready'])
        return reader, writer

    async def call(self, connection, name, arguments):
        reader, writer = connection
        writer.write(encode({'op': 'message', 'line': json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments},
        })}))
        await writer.drain()
        response = json.loads(await reader.readline())
        return json.loads(response['result']['content'][0]['text'])

    async def test_isolated_state_and_no_per_connection_migrations(self):
        with patch('kanban_mcp.setup.auto_migrate') as migrate:
            first = await self.open('first')
            second = await self.open('second')
            migrate.assert_not_called()
        for connection, path in ((first, '/first'), (second, '/second')):
            await self.call(connection, 'set_current_project', {'project_dir': path})
        self.assertEqual((await self.call(first, 'get_current_project', {}))['directory_path'], '/first')
        self.assertEqual((await self.call(second, 'get_current_project', {}))['directory_path'], '/second')

    async def test_lease_expiry_releases_abandoned_connection(self):
        with patch('kanban_mcp.shared_runtime.LEASE_SECONDS', 0.05):
            reader, _ = await self.open('abandoned')
            self.assertEqual(await asyncio.wait_for(reader.read(), timeout=1), b'')
            self.assertNotIn('abandoned', self.service.sessions)

    async def test_duplicate_handshake_does_not_remove_original(self):
        await self.open('original')
        reader, _ = await self.connection({'op': 'open', 'session': 'original'})
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        self.assertIn('original', self.service.sessions)

    async def test_bad_json_rpc_does_not_break_connection(self):
        reader, writer = await self.open('malformed')
        writer.write(encode({'op': 'message', 'line': '{broken'}))
        await writer.drain()
        self.assertEqual(json.loads(await reader.readline())['error']['code'], -32700)
        self.assertFalse((await self.call((reader, writer), 'get_current_project', {}))['success'])

    async def test_drain_is_atomic_and_requires_force_for_connected_clients(self):
        reader, _ = await self.open('busy')
        admin, _ = await self.connection({'op': 'drain'})
        self.assertFalse(json.loads(await admin.readline())['draining'])
        admin, _ = await self.connection({'op': 'drain', 'force': True})
        self.assertTrue(json.loads(await admin.readline())['draining'])
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        rejected, _ = await self.connection({'op': 'open', 'session': 'new'})
        self.assertEqual(await asyncio.wait_for(rejected.read(), 1), b'')


if __name__ == '__main__':
    unittest.main()
