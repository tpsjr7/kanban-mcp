"""Exercise timeline SQL against real SQLite without touching the shared volume."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

scratch = tempfile.TemporaryDirectory()
os.environ['KANBAN_BACKEND'] = 'sqlite'
os.environ['KANBAN_SQLITE_PATH'] = str(Path(scratch.name) / 'board.db')
os.environ['KANBAN_PROJECT_DIR'] = '/workspace/music_gen'
os.environ['HOME'] = scratch.name

from kanban_mcp.core import KanbanDB, KanbanMCPServer
from kanban_mcp.setup import auto_migrate
from kanban_mcp import web
from kanban_mcp.attribution import agent_identity


class TimelineRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = KanbanDB()
        auto_migrate(cls.database._backend)
        web.db = cls.database
        cls.client = web.app.test_client()
        cls.project = cls.database.ensure_project('/workspace/music_gen')
        cls.other_project = cls.database.ensure_project('/workspace/other')
        cls.item = cls.database.create_item(cls.project, 'feature', 'Timeline 音楽')
        cls.other_item = cls.database.create_item(cls.project, 'feature', 'No linked update')
        cls.database.set_status(cls.item, 'review')
        # Reproduce the browser's Add update request with quotes and Unicode.
        cls.content = "User's saved update — 音楽"
        response = cls.client.post('/api/updates', json={
            'project_id': cls.project, 'content': cls.content,
            'item_ids': [cls.item],
        }, headers={'Authorization': 'Basic dGVzdDp0ZXN0'})
        assert response.status_code == 201
        cls.update_id = response.json['update_id']
        cls.database.add_update(cls.project, 'Unlinked project update',
                                attribution=agent_identity('codex', 'test-thread', 'Test agent'))
        cls.database.add_update(cls.other_project, 'Other project private update',
                                attribution=agent_identity('opencode', 'ses_test', chat_title='Other chat'))

    def timeline(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['success'])
        return response.json['entries']

    def test_project_includes_linked_and_unlinked_updates_without_leaking_projects(self):
        entries = self.timeline(f'/api/projects/{self.project}/timeline?limit=100')
        contents = [e['details']['content'] for e in entries if e['activity_type'] == 'update']
        self.assertIn(self.content, contents)
        self.assertIn('Unlinked project update', contents)
        self.assertNotIn('Other project private update', contents)
        linked = next(e for e in entries if e['details'].get('update_id') == self.update_id)
        self.assertEqual(linked['details']['linked_items'], [self.item])

    def test_item_includes_only_its_updates_and_status_history(self):
        entries = self.timeline(f'/api/items/{self.item}/timeline?limit=100')
        self.assertTrue(any(e['activity_type'] == 'status_change' for e in entries))
        contents = [e['details']['content'] for e in entries if e['activity_type'] == 'update']
        self.assertEqual(contents, [self.content])
        other = self.timeline(f'/api/items/{self.other_item}/timeline')
        self.assertFalse(any(e['activity_type'] == 'update' for e in other))

    def test_limit_invalid_limit_and_missing_entities(self):
        self.assertLessEqual(len(self.timeline(f'/api/projects/{self.project}/timeline?limit=1')), 1)
        self.timeline(f'/api/projects/{self.project}/timeline?limit=not-a-number')
        self.assertEqual(self.client.get('/api/projects/missing/timeline').status_code, 404)
        self.assertEqual(self.client.get('/api/items/999999/timeline').status_code, 404)

    def test_browser_assets_load_local_time_helper_before_consumers(self):
        page = self.client.get(f'/?project={self.project}').get_data(as_text=True)
        self.assertLess(page.index('/static/browser-time.js'), page.index('/static/app.js'))
        helper = self.client.get('/static/browser-time.js')
        self.assertEqual(helper.status_code, 200)
        timeline = self.client.get('/static/timeline.js').get_data(as_text=True)
        self.assertIn('KanbanTime.dayKey(entry.timestamp)', timeline)
        self.assertIn('KanbanTime.dayLabel(dateStr)', timeline)
        self.assertIn('KanbanTime.parse(timestamp)', timeline)
        application = self.client.get('/static/app.js').get_data(as_text=True)
        self.assertIn('KanbanTime.parse(decision.created_at)', application)
        self.assertEqual(application.count('KanbanTime.parse(update.created_at)'), 2)

    def test_mcp_uses_the_same_fixed_timeline_builder(self):
        server = KanbanMCPServer()
        for name, arguments in [('get_project_timeline', {}),
                                ('get_item_timeline', {'item_id': self.item})]:
            result = asyncio.run(server.handle_request({
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': name, 'arguments': arguments},
            }))
            self.assertNotIn('error', result)
            data = json.loads(result['result']['content'][0]['text'])
            self.assertTrue(data['success'])
            self.assertTrue(any(e['details'].get('content') == self.content for e in data['entries']))


if __name__ == '__main__':
    unittest.main()
