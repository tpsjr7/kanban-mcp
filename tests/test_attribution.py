"""Identity, migration and data-preservation checks on disposable SQLite."""
import asyncio
import base64
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

scratch = tempfile.TemporaryDirectory()
os.environ.update(KANBAN_BACKEND='sqlite', HOME=scratch.name,
                  KANBAN_SQLITE_PATH=str(Path(scratch.name) / 'board.db'),
                  KANBAN_PROJECT_DIR='/workspace/music_gen')

import kanban_mcp
from kanban_mcp.core import KanbanDB, KanbanMCPServer
from kanban_mcp.setup import auto_migrate
from kanban_mcp.attribution import agent_identity
from kanban_mcp.export import ExportBuilder, export_to_format
from kanban_mcp import web


class AttributionRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = KanbanDB()
        initial = Path(kanban_mcp.__file__).parent / 'migrations/sqlite/001_initial_schema.sql'
        connection = cls.database._backend._get_connection()
        connection.executescript(initial.read_text())
        cls.project = cls.database.ensure_project('/workspace/music_gen')
        # Build a genuine pre-migration fixture without the new column adapter.
        with cls.database._db_cursor(commit=True) as cursor:
            cursor.execute("INSERT INTO items(project_id,type_id,status_id,title) VALUES (?,3,1,?)",
                           (cls.project, 'Attribution fixture'))
            cls.item = cursor.lastrowid
        with cls.database._db_cursor(commit=True) as cursor:
            cursor.execute('INSERT INTO updates (project_id, content) VALUES (?, ?)',
                           (cls.project, 'Historical update'))
            cls.legacy_id = cursor.lastrowid
        auto_migrate(cls.database._backend)
        auto_migrate(cls.database._backend)
        web.db = cls.database
        cls.client = web.app.test_client()
        cls.server = KanbanMCPServer()

    def call(self, name, **arguments):
        response = asyncio.run(self.server.handle_request({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments},
        }))
        if 'error' in response:
            return response
        return json.loads(response['result']['content'][0]['text'])

    def count(self):
        return len(self.database.get_updates(self.project, limit=1000))

    def test_legacy_is_not_backfilled_and_migration_is_repeatable(self):
        legacy = next(u for u in self.database.get_updates(self.project, 1000)
                      if u['id'] == self.legacy_id)
        self.assertEqual(legacy['content'], 'Historical update')
        for field in ('source_client', 'browser_username', 'chat_id', 'chat_title', 'agent_name'):
            self.assertIsNone(legacy[field])
        entries = self.database.get_timeline_data(project_id=self.project)['entries']
        entry = next(e for e in entries if e['details'].get('update_id') == self.legacy_id)
        self.assertEqual(entry['actor'], 'Unknown author')

    def test_browser_uses_basic_username_ignores_body_and_excludes_credentials(self):
        token = base64.b64encode(b'Jane:never-store-this-password').decode()
        response = self.client.post('/api/updates', headers={'Authorization': 'Basic ' + token}, json={
            'project_id': self.project, 'content': 'Browser identity fixture',
            'item_ids': [self.item], 'browser_username': 'Forged', 'agent_name': 'Forged',
            'chat_id': 'forged', 'source_client': 'codex',
        })
        self.assertEqual(response.status_code, 201)
        identity = response.json['attribution']
        self.assertEqual(identity['browser_username'], 'Jane')
        self.assertEqual(identity['source_client'], 'browser')
        self.assertIsNone(identity['chat_id'])
        with self.database._db_cursor(dictionary=True) as cursor:
            cursor.execute('SELECT * FROM updates WHERE id = ?', (response.json['update_id'],))
            stored = cursor.fetchone()
        self.assertNotIn('never-store-this-password', repr(stored))
        self.assertNotIn(token, repr(stored))
        self.assertNotIn('Forged', repr(stored))

    def test_browser_requires_login_without_creating_an_update(self):
        before = self.count()
        response = self.client.post('/api/updates', json={
            'project_id': self.project, 'content': 'Must not be stored',
        })
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.count(), before)

    def test_both_clients_and_interleaved_identity_are_per_update(self):
        cases = [('codex', 'codex-opaque-id', 'Explicit agent', 'Original title'),
                 ('opencode', 'ses_one', '', 'OpenCode title fallback'),
                 ('codex', 'other-id', '', 'Another Codex chat')]
        for client, identifier, name, title in cases:
            result = self.call('add_update', content=f'Interleaved {identifier}',
                               client=client, chat_id=identifier,
                               agent_name=name, chat_title=title, item_ids=str(self.item))
            self.assertTrue(result['success'])
            identity = result['attribution']
            self.assertEqual(identity['chat_id'], identifier)
            self.assertEqual(identity['agent_name'], name or title)
            self.assertEqual(identity['chat_title'], title)
            self.assertEqual(identity['source_client'], client)

    def test_incomplete_identity_and_old_clients_fail_without_partial_updates(self):
        before = self.count()
        invalid = [{}, {'client': 'codex', 'chat_id': '  ', 'agent_name': 'Agent'},
                   {'client': 'opencode', 'chat_id': 'ses_blank', 'chat_title': '  '},
                   {'client': 'browser', 'chat_id': 'id', 'agent_name': 'Impersonator'}]
        for identity in invalid:
            self.assertIn('error', self.call('add_update', content='Rejected', **identity))
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.add_update(self.project, 'Old connection rejected')
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.add_update(self.project, 'Invalid link', [999999],
                                     agent_identity('opencode', 'ses_rollback', 'Agent'))
        self.assertEqual(self.count(), before)

    def test_identity_survives_every_read_surface_and_reopen(self):
        result = self.call('add_update', content='Unique attribution search phrase',
                           client='opencode', chat_id='ses_persist', chat_title='Snapshot title',
                           item_ids=str(self.item))
        identity = result['attribution']
        update_id = result['update_id']
        for tool in ('get_updates', 'get_latest_update'):
            data = self.call(tool)
            update = (next(u for u in data['updates'] if u['id'] == update_id)
                      if tool == 'get_updates' else data['update'])
            for field, value in identity.items():
                self.assertEqual(update[field], value)
        for path in (f'/api/items/{self.item}/timeline', f'/api/projects/{self.project}/timeline'):
            data = self.client.get(path).json
            entry = next(e for e in data['entries'] if e['details'].get('update_id') == update_id)
            self.assertEqual(entry['details']['attribution'], identity)
            self.assertEqual(entry['actor'], 'Snapshot title')
        search = self.call('search', query='Unique attribution search phrase')
        self.assertEqual(search['updates'][0]['attribution'], identity)
        exported = ExportBuilder(self.database, self.project).build_export_data(include_updates=True)
        update = next(u for u in exported['updates'] if u['id'] == update_id)
        self.assertEqual(update['attribution'], identity)
        for format_name in ('json', 'yaml', 'markdown'):
            rendered = export_to_format(exported, format_name)
            self.assertIn('ses_persist', rendered)
            self.assertIn('Snapshot title', rendered)
            self.assertIn('opencode', rendered)
        reopened = KanbanDB()
        latest = reopened.get_latest_update(self.project)
        self.assertEqual(latest['chat_id'], 'ses_persist')


if __name__ == '__main__':
    unittest.main()
