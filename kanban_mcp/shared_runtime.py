"""Shared MCP service and disposable stdio relays (Linux-container internal IPC).

The host renews a lease independently of model activity. Losing a host launcher
therefore releases the relay even if Docker retains an exec attachment.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import select
import signal
import socket
import sys

SOCKET = '/tmp/kanban-mcp-runtime/service.sock'
PROTOCOL = 1
LEASE_SECONDS = 35
MAX_FRAME = 16 * 1024 * 1024


def encode(value):
    return (json.dumps(value, ensure_ascii=False) + '\n').encode()


class Service:
    def __init__(self, database=None):
        from kanban_mcp.core import KanbanMCPServer
        self.prototype = KanbanMCPServer(database=database)
        self.prototype.db.ensure_project(
            os.environ['KANBAN_PROJECT_DIR'],
            os.environ.get('KANBAN_PROJECT_NAME', 'project'))
        self.sessions = {}
        self.draining = False
        self.serial = asyncio.Lock()

    async def connection(self, reader, writer):
        session_id = None
        try:
            frame = await self.read(reader)
            op = frame.get('op')
            if op in ('status', 'drain'):
                if op == 'drain':
                    if not self.sessions or frame.get('force') is True:
                        self.draining = True
                    if self.draining:
                        for connection in list(self.sessions.values()):
                            connection.close()
                writer.write(encode({
                    'protocol': PROTOCOL, 'connections': len(self.sessions),
                    'draining': self.draining,
                    'project': os.environ['KANBAN_PROJECT_DIR'],
                }))
                await writer.drain()
                return
            if op != 'open' or self.draining:
                raise ValueError('MCP service is draining or handshake is invalid')
            session_id = frame.get('session')
            if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
                raise ValueError('Invalid session ID')
            if session_id in self.sessions:
                raise ValueError('Session ID is already connected')
            from kanban_mcp.core import KanbanMCPServer
            server = KanbanMCPServer(database=self.prototype.db)
            self.sessions[session_id] = writer
            writer.write(encode({'protocol': PROTOCOL, 'ready': True}))
            await writer.drain()
            while True:
                frame = await self.read(reader)
                if frame.get('op') == 'lease':
                    continue
                if frame.get('op') != 'message' or not isinstance(frame.get('line'), str):
                    raise ValueError('Invalid relay frame')
                try:
                    request = json.loads(frame['line'])
                    if not isinstance(request, dict):
                        raise ValueError('MCP request must be an object')
                except (ValueError, TypeError):
                    writer.write(encode({'jsonrpc': '2.0', 'id': None, 'error': {
                        'code': -32700, 'message': 'Invalid JSON-RPC request'}}))
                    await writer.drain()
                    continue
                async with self.serial:
                    response = await server.handle_request(request)
                if 'id' in request:
                    writer.write(encode(response))
                    await writer.drain()
        except (EOFError, asyncio.TimeoutError, ConnectionError):
            pass
        except Exception as error:
            print(f'[kanban-mcp] Connection closed: {error}', file=sys.stderr)
        finally:
            if session_id is not None and self.sessions.get(session_id) is writer:
                del self.sessions[session_id]
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    @staticmethod
    async def read(reader):
        line = await asyncio.wait_for(reader.readline(), timeout=LEASE_SECONDS)
        if not line:
            raise EOFError()
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError('Relay frame must be an object')
        return value


async def serve():
    directory = Path(SOCKET).parent
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    # This container runs only this daemon; its private socket can survive restart.
    Path(SOCKET).unlink(missing_ok=True)
    # Coordinate initialization with the independent web initializer.
    import fcntl
    lock_path = Path(os.environ['KANBAN_SQLITE_PATH']).parent / '.initialize.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        service = Service()
    listener = await asyncio.start_unix_server(
        service.connection, SOCKET, limit=MAX_FRAME)
    Path(SOCKET).chmod(0o600)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event, stop.set)
    async with listener:
        await stop.wait()
    for writer in list(service.sessions.values()):
        writer.close()
    service.prototype.db._backend.close()
    Path(SOCKET).unlink(missing_ok=True)


def admin(op, force=False):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(5)
        connection.connect(SOCKET)
        connection.sendall(encode({'op': op, 'force': force}))
        with connection.makefile('rb') as stream:
            value = json.loads(stream.readline(MAX_FRAME))
    print(json.dumps(value))


def relay(session):
    """Copy framed host input to the socket and raw MCP responses to stdout."""
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(10)
        connection.connect(SOCKET)
        connection.sendall(encode({'op': 'open', 'session': session}))
        # Read only the handshake; leave subsequent response bytes untouched.
        handshake = bytearray()
        while not handshake.endswith(b'\n') and len(handshake) < 1024:
            part = connection.recv(1)
            if not part:
                raise RuntimeError('MCP service refused connection')
            handshake.extend(part)
        if json.loads(handshake).get('ready') is not True:
            raise RuntimeError('MCP service handshake failed')
        connection.settimeout(None)
        stdin_open = True
        while True:
            inputs = [connection]
            if stdin_open:
                inputs.append(sys.stdin.buffer)
            readable, _, _ = select.select(inputs, [], [])
            if connection in readable:
                chunk = connection.recv(65536)
                if not chunk:
                    return
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            if sys.stdin.buffer in readable:
                chunk = os.read(sys.stdin.fileno(), 65536)
                if not chunk:
                    stdin_open = False
                    connection.shutdown(socket.SHUT_WR)
                else:
                    connection.sendall(chunk)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('serve', 'relay', 'status', 'drain'))
    parser.add_argument('--session')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    if args.action == 'serve':
        asyncio.run(serve())
    elif args.action == 'relay':
        if not args.session:
            parser.error('relay requires --session')
        relay(args.session)
    else:
        admin(args.action, args.force)


if __name__ == '__main__':
    main()
