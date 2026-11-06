"""Isolated project configuration and workflow acceptance."""
import os
import tempfile
import unittest
from pathlib import Path

scratch = tempfile.TemporaryDirectory()
os.environ['KANBAN_BACKEND'] = 'sqlite'
os.environ['KANBAN_SQLITE_PATH'] = str(Path(scratch.name) / 'board.db')
os.environ['HOME'] = scratch.name
from kanban_mcp.core import KanbanDB
from kanban_mcp.setup import auto_migrate
from kanban_mcp.columns import read, save
from kanban_mcp import web


class ColumnsTest(unittest.TestCase):
    def setUp(self):
        self.db = KanbanDB()
        auto_migrate(self.db._backend)
        self.project = self.db.ensure_project('/test/' + self.id())
        self.other = self.db.ensure_project('/other/' + self.id())
        web.db = self.db

    def custom(self):
        draft = read(self.db, self.project)
        draft['columns'].insert(1, {'label': 'Waiting 音楽', 'allowed_types': ['feature']})
        return save(self.db, self.project, draft)

    def test_project_progression_and_rename(self):
        result = self.custom()
        custom = result['columns'][1]
        card = self.db.create_item(self.project, 'feature', 'Test')
        self.assertEqual(self.db.advance_status(card)['new_status'], custom['key'])
        self.assertEqual(self.db.revert_status(card)['new_status'], 'backlog')
        result['columns'][1]['label'] = '<new name>'
        renamed = save(self.db, self.project, result)
        self.assertEqual(renamed['columns'][1]['key'], custom['key'])
        self.assertEqual(len(read(self.db, self.other)['columns']), 6)
        outsider = self.db.create_item(self.other, 'feature', 'Other')
        with self.assertRaises(ValueError):
            self.db.set_status(outsider, custom['key'])

    def test_conflicts_and_restrictions_are_atomic(self):
        result = self.custom()
        card = self.db.create_item(self.project, 'feature', 'Test', status_name=result['columns'][1]['key'])
        original = read(self.db, self.project)
        result['columns'][1]['allowed_types'] = ['issue']
        with self.assertRaisesRegex(ValueError, str(card)):
            save(self.db, self.project, result)
        self.assertEqual(read(self.db, self.project), original)
        stale = read(self.db, self.project)
        save(self.db, self.project, stale)
        with self.assertRaisesRegex(ValueError, 'changed'):
            save(self.db, self.project, stale)

    def test_delete_moves_with_history(self):
        result = self.custom()
        custom = result['columns'][1]
        card = self.db.create_item(self.project, 'feature', 'Test', status_name=custom['key'])
        result['columns'].pop(1)
        with self.assertRaisesRegex(ValueError, 'destination'):
            save(self.db, self.project, result)
        result['destinations'] = {str(custom['status_id']): 2}
        save(self.db, self.project, result)
        self.assertEqual(self.db.get_item(card)['status_name'], 'todo')
        self.assertEqual(len(self.db.get_status_history(card)), 2)

    def test_delete_completion_advances_parent(self):
        result = self.custom()
        custom = result['columns'][1]
        parent = self.db.create_item(self.project, 'epic', 'Parent')
        child = self.db.create_item(self.project, 'feature', 'Child',
                                    status_name=custom['key'], parent_id=parent)
        result['columns'].pop(1)
        result['destinations'] = {str(custom['status_id']): 5}
        save(self.db, self.project, result)
        self.assertEqual(self.db.get_item(child)['status_name'], 'done')
        self.assertEqual(self.db.get_item(parent)['status_name'], 'review')

    def test_browser_and_restart(self):
        result = self.custom()
        client = web.app.test_client()
        response = client.get(f'/api/projects/{self.project}/columns')
        self.assertEqual(response.json, result)
        self.assertEqual(client.get('/?project=' + self.project).status_code, 200)
        self.assertEqual(read(KanbanDB(), self.project), result)
        with self.db._db_cursor() as cursor:
            cursor.execute('PRAGMA integrity_check')
            self.assertEqual(cursor.fetchone()[0], 'ok')

    def test_export_and_online_recovery(self):
        import sqlite3
        from kanban_mcp.export import ExportBuilder
        result = self.custom()
        exported = ExportBuilder(self.db, self.project).build_export_data()
        self.assertEqual(exported['column_configuration'], result)
        restored = str(Path(scratch.name) / (self.project + '.backup.db'))
        with sqlite3.connect(os.environ['KANBAN_SQLITE_PATH']) as source:
            with sqlite3.connect(restored) as target:
                source.backup(target)
                self.assertEqual(target.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertFalse(target.execute('PRAGMA foreign_key_check').fetchall())
                for table in ('items', 'status_history', 'updates', 'project_columns',
                              'column_types', 'column_revisions'):
                    self.assertEqual(source.execute('SELECT * FROM ' + table).fetchall(),
                                     target.execute('SELECT * FROM ' + table).fetchall())

    def test_completion_independent_of_order(self):
        result = self.custom()
        result['columns'].append(result['columns'].pop(1))
        save(self.db, self.project, result)
        card = self.db.create_item(self.project, 'feature', 'Test')
        self.assertEqual(self.db.close_item(card)['new_status'], 'closed')
        self.db.set_status(card, result['columns'][-1]['key'])
        self.assertIsNone(self.db.get_item(card)['closed_at'])


if __name__ == '__main__':
    unittest.main()
