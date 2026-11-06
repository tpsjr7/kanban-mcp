"""Private backup, recovery verification, and idempotent backlog import."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


def fingerprint(connection):
    result = {}
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    for (name,) in tables:
        escaped = name.replace('"', '""')
        rows = connection.execute(f'SELECT * FROM "{escaped}"').fetchall()
        encoded = json.dumps(sorted(rows, key=repr), default=str).encode()
        result[name] = {"rows": len(rows), "sha256": hashlib.sha256(encoded).hexdigest()}
    return result


def backup():
    path = Path(os.environ['KANBAN_SQLITE_PATH'])
    if not path.is_file():
        raise RuntimeError('Start the board before taking a backup')
    source = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / 'backup.db'
        with sqlite3.connect(target) as destination:
            source.backup(destination)
            assert destination.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        sys.stdout.buffer.write(target.read_bytes())
    source.close()


def restore_check():
    target = Path(os.environ['KANBAN_SQLITE_PATH'])
    if target.exists():
        raise RuntimeError('Recovery check requires an empty disposable volume')
    target.write_bytes(sys.stdin.buffer.read())
    with sqlite3.connect(target) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
        print(json.dumps(fingerprint(connection), sort_keys=True))


def initialize():
    import fcntl
    from kanban_mcp.core import KanbanDB
    from kanban_mcp.setup import auto_migrate
    with open('/data/.initialize.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        database = KanbanDB()
        auto_migrate(database._backend)
        from kanban_mcp.attribution import ensure_schema
        ensure_schema(database)
        from kanban_mcp.team_runtime import ensure_schema as ensure_teams
        ensure_teams(database)
        database.ensure_project(os.environ['KANBAN_PROJECT_DIR'], os.environ.get('KANBAN_PROJECT_NAME', 'project'))


def seed():
    from kanban_mcp.core import KanbanDB
    items = json.load(sys.stdin)
    database = KanbanDB()
    project = database.ensure_project(os.environ['KANBAN_PROJECT_DIR'], os.environ.get('KANBAN_PROJECT_NAME', 'project'))
    results = []
    keys = [ticket['key'] for ticket in items]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate W-numbers in input')
    for ticket in items:
        key = ticket['key']
        if not key.startswith('W-') or not key[2:].isdigit():
            raise ValueError('Each ticket requires a W-number')
        title = f"[{key}] {ticket['title']}"
        matches = database.list_items(project_id=project, tag_names=[key], limit=10000)
        if len(matches) > 1:
            raise RuntimeError(f'Duplicate external identifier: {key}')
        if matches:
            results.append({'key': key, 'id': matches[0]['id'], 'created': False})
            continue
        partial = [item for item in database.list_items(project_id=project, limit=10000)
                   if item['title'] == title]
        if len(partial) > 1:
            raise RuntimeError(f'Duplicate partial import: {key}')
        identifier = partial[0]['id'] if partial else database.create_item(
            project_id=project, type_name=ticket.get('type', 'feature'),
            title=title, description=ticket['description'], priority=3,
        )
        status = database.set_status(identifier, ticket['status'])
        if not status.get('success'):
            raise RuntimeError(str(status))
        existing = {link['file_path'] for link in database.get_item_files(identifier)}
        for link in ticket.get('files', []):
            if link not in existing:
                database.link_file(identifier, link)
        database.add_tag_to_item(identifier, key)
        results.append({'key': key, 'id': identifier, 'created': True})
    print(json.dumps(results))


if __name__ == '__main__':
    {'initialize': initialize, 'backup': backup, 'restore-check': restore_check, 'seed': seed}[sys.argv[1]]()
