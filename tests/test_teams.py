"""Isolated workflow authorization, event durability and assignment tests."""
import os
import tempfile
import unittest
from pathlib import Path

scratch = tempfile.TemporaryDirectory()
os.environ.update(KANBAN_BACKEND='sqlite', HOME=scratch.name,
                  KANBAN_SQLITE_PATH=str(Path(scratch.name) / 'board.db'))
from kanban_mcp.core import KanbanDB
from kanban_mcp.setup import auto_migrate
from kanban_mcp import team_runtime as r


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.db = KanbanDB()
        auto_migrate(self.db._backend)
        self.project = self.db.ensure_project('/test/' + self.id())
        r.setup(self.db, self.project)
        r.configure(self.db, self.project, 'manager_set', {'identity': {'client': 'codex', 'chat_id': 'manager'}})
        r.configure(self.db, self.project, 'team_add', {'name': self.id(), 'instance': self.id()})
        self.worker = 'codex:' + self.id()
        r.configure(self.db, self.project, 'worker_add', {'team': self.id(), 'identity': {
            'client': 'codex', 'chat_id': self.id(), 'title': 'Worker'}})
        r.configure(self.db, self.project, 'arm', {})
        self.item = self.db.create_item(self.project, 'feature', 'Work')

    def tearDown(self):
        self.db._backend._get_connection().close()

    def human_move(self, key):
        token = r.browser_user.set('kanban')
        try:
            self.db.set_status(self.item, key)
        finally:
            r.browser_user.reset(token)

    def assigned(self):
        self.human_move('needs_plan')
        events = r.consume(self.db, self.project)
        self.assertEqual(len(events), 1)
        r.assign(self.db, self.project, 'manager', self.item, self.id(), self.worker)
        return next(d for d in r.consume(self.db, self.project) if d['kind'] == 'plan')

    def report(self, dispatch, kind, payload=None):
        r.report(self.db, 'codex', self.id(), dispatch['id'], kind, payload or {})
        with self.db._db_cursor(dictionary=True) as c:
            c.execute('SELECT id FROM workflow_reports WHERE dispatch_id=? AND kind=?', (dispatch['id'], kind))
            report_id = c.fetchone()['id']
        return r.host_action(self.db, 'verify_report', {'id': report_id, 'plan_hash': 'hash', 'plan_path': '/verified/docs/plan.md'})

    def test_complete_flow_requires_human_approvals(self):
        parent = self.db.create_item(self.project, 'epic', 'Parent')
        self.db.set_parent(self.item, parent)
        d = self.assigned()
        self.report(d, 'started')
        self.assertEqual(self.db.get_item(self.item)['status_name'], 'planning')
        self.report(d, 'plan_ready', {'plan': 'docs/plan.md'})
        run = r.snapshot(self.db, self.project)['tickets'][0]
        self.assertEqual(run['plan_path'], '/verified/docs/plan.md')
        self.assertEqual(run['plan_client'], 'codex')
        self.assertEqual(run['plan_chat_id'], self.id())
        self.assertEqual(run['plan_chat_title'], 'Worker')
        with self.assertRaises(Exception): self.db.set_status(self.item, 'todo')
        self.human_move('todo')
        implementation = next(d for d in r.consume(self.db, self.project) if d['kind'] == 'implement')
        self.report(implementation, 'started')
        self.report(implementation, 'preview_ready', {'preview': 'https://local-a.flippyface.com', 'commit': 'a'*40})
        with self.assertRaises(Exception): self.db.set_status(self.item, 'done')
        self.human_move('done')
        integration = next(d for d in r.consume(self.db, self.project) if d['kind'] == 'integrate')
        self.assertIsNotNone(integration['event_id'])
        event = next(e for e in r.snapshot(self.db, self.project)['events'] if e['id']==integration['event_id'])
        self.assertEqual(event['browser_user'], 'kanban')
        self.assertEqual(event['new_status'], 'done')
        self.assertFalse(r.effectively_complete(self.db, self.item, 'done'))
        self.assertEqual(self.db.get_epic_progress(parent)['completed'], 0)
        dependent = self.db.create_item(self.project, 'feature', 'Dependent')
        self.db.add_relationship(self.item, dependent, 'blocks')
        self.assertEqual(len(self.db.get_blocking_items(dependent)), 1)
        r.host_action(self.db, 'lease', {'item_id': self.item})
        self.report(integration, 'integrated')
        self.assertEqual(self.db.get_item(self.item)['status_name'], 'closed')
        self.assertEqual(self.db.get_blocking_items(dependent), [])
        self.assertEqual(self.db.get_item(parent)['status_name'], 'review')
        r.configure(self.db, self.project, 'worker_remove', {'worker': self.worker, 'team': self.id()})
        r.configure(self.db, self.project, 'team_remove', {'name': self.id()})

    def test_duplicate_reports_and_poll_do_not_duplicate_dispatch(self):
        d = self.assigned()
        self.report(d, 'started')
        self.report(d, 'started')
        self.assertEqual(len([x for x in r.consume(self.db, self.project) if x['kind']=='plan']), 0)
        with self.db._db_cursor() as c:
            c.execute('SELECT count(*) FROM workflow_reports WHERE dispatch_id=?', (d['id'],))
            self.assertEqual(c.fetchone()[0], 1)

    def test_scope_change_invalidates_pending_approval(self):
        d = self.assigned()
        self.report(d, 'started')
        self.report(d, 'plan_ready', {'plan': 'docs/plan.md'})
        self.human_move('todo')
        with self.db._db_cursor(commit=True) as c:
            c.execute('UPDATE items SET title=? WHERE id=?', ('Changed scope', self.item))
        self.assertFalse(any(d['kind']=='implement' for d in r.consume(self.db, self.project)))

    def test_manager_cannot_reassign_active_ticket(self):
        self.assigned()
        with self.assertRaises(ValueError):
            r.assign(self.db, self.project, 'manager', self.item, 'other', 'other')

    def test_active_membership_removal_and_foreign_reports_rejected(self):
        d = self.assigned()
        with self.assertRaises(ValueError):
            r.configure(self.db, self.project, 'worker_remove', {'worker': self.worker})
        with self.assertRaises(ValueError):
            r.report(self.db, 'codex', 'other', d['id'], 'started', {})

    def test_backward_move_requests_stop_without_releasing_claim(self):
        d = self.assigned()
        self.report(d, 'started')
        self.human_move('backlog')
        pending = r.consume(self.db, self.project)
        cancellation = next(x for x in pending if x['kind'] == 'cancel')
        self.assertEqual(cancellation['revision'], 1)
        self.report(cancellation, 'stopped')
        run = next(x for x in r.snapshot(self.db, self.project)['tickets'] if x['item_id']==self.item)
        self.assertEqual(run['phase'], 'stopped')
        self.assertEqual(run['worker'], self.worker)
        self.assertFalse(any(x['kind']=='cancel' for x in r.consume(self.db, self.project)))

    def reviewed(self):
        plan = self.assigned()
        self.report(plan, 'started')
        self.report(plan, 'plan_ready', {'plan': 'docs/plan.md'})
        self.human_move('todo')
        implementation = next(d for d in r.consume(self.db, self.project)
                              if d['kind'] == 'implement')
        self.report(implementation, 'started')
        self.report(implementation, 'preview_ready', {
            'preview': 'https://local-d.flippyface.com', 'commit': 'a' * 40})
        return implementation

    def stopped_replan(self):
        self.reviewed()
        self.human_move('needs_plan')
        pending = r.consume(self.db, self.project)
        self.assertFalse(any(d['kind'] == 'plan' for d in pending))
        cancel = next(d for d in pending if d['kind'] == 'cancel')
        self.report(cancel, 'stopped')
        return cancel

    def test_verified_stop_resumes_requested_plan_on_same_worker(self):
        self.stopped_replan()
        pending = r.consume(self.db, self.project)
        plans = [d for d in pending if d['kind'] == 'plan']
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual((plan['worker'], plan['revision']), (self.worker, 1))
        state = r.snapshot(self.db, self.project)
        run = next(t for t in state['tickets'] if t['item_id'] == self.item)
        self.assertEqual((run['team'], run['worker']), (self.id(), self.worker))
        self.assertEqual(run['commit_hash'], 'a' * 40)
        self.assertIsNone(run['plan_approved'])
        self.assertIsNone(run['merge_approved'])
        event = next(e for e in state['events'] if e['id'] == plan['event_id'])
        self.assertEqual((event['browser_user'], event['new_status']), ('kanban', 'needs_plan'))
        self.assertEqual([d['id'] for d in r.consume(self.db, self.project)
                          if d['kind'] == 'plan'], [plan['id']])
        self.report(plan, 'started')
        self.report(plan, 'plan_ready', {'plan': 'docs/revised-plan.md'})
        self.assertEqual(self.db.get_item(self.item)['status_name'], 'plan_review')
        self.assertFalse(any(d['kind'] == 'implement' for d in r.consume(self.db, self.project)))

    def test_scope_change_after_stop_does_not_reuse_planning_request(self):
        self.stopped_replan()
        with self.db._db_cursor(commit=True) as c:
            c.execute('UPDATE items SET title=? WHERE id=?', ('Newer scope', self.item))
        self.assertFalse(any(d['kind'] == 'plan' for d in r.consume(self.db, self.project)))

    def test_stale_stop_cannot_acknowledge_newer_cancellation(self):
        self.reviewed()
        self.human_move('needs_plan')
        cancel = next(d for d in r.consume(self.db, self.project) if d['kind'] == 'cancel')
        r.report(self.db, 'codex', self.id(), cancel['id'], 'stopped', {})
        with self.db._db_cursor(commit=True) as c:
            c.execute('UPDATE items SET title=? WHERE id=?', ('Changed again', self.item))
        with self.assertRaises(ValueError):
            self.report(cancel, 'stopped')
        with self.db._db_cursor(dictionary=True) as c:
            c.execute('SELECT id FROM workflow_reports WHERE dispatch_id=?', (cancel['id'],))
            report_id = c.fetchone()['id']
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'verify_report', {'id': report_id})
        run = next(t for t in r.snapshot(self.db, self.project)['tickets']
                   if t['item_id'] == self.item)
        self.assertEqual(run['cancel_pending'], 1)

    def test_compact_views_are_project_scoped_and_omit_large_content(self):
        import json
        from kanban_mcp import workflow_views as v
        d = self.assigned()
        r.host_action(self.db, 'delivery', {'id': d['id'], 'state': 'delivered'})
        with r.transaction(self.db, trusted=True) as c:
            c.execute('UPDATE ticket_runs SET plan_text=? WHERE item_id=?',
                      ('large-private-plan-' * 10000, self.item))
        result = v.check(self.db, self.project, d['id'])
        self.assertTrue(result['current'])
        self.assertEqual(result['dispatch']['target']['chat_id'], self.id())
        self.assertEqual(result['human_request']['browser_user'], 'kanban')
        compact = json.dumps(result)
        self.assertLess(len(compact), 2500)
        self.assertNotIn('large-private-plan', compact)
        self.assertNotIn('plan_text', json.dumps(v.ticket(self.db, self.project, self.item)))
        other = self.db.ensure_project('/other/' + self.id())
        self.assertFalse(v.check(self.db, other, d['id'])['found'])
        self.assertFalse(v.ticket(self.db, other, self.item)['found'])
        unmanaged = self.db.create_item(self.project, 'feature', 'Unmanaged')
        self.assertFalse(v.ticket(self.db, self.project, unmanaged)['managed'])

    def test_compact_check_rejects_pause_stale_cancel_and_uncertain_delivery(self):
        from kanban_mcp import workflow_views as v
        d = self.assigned()
        self.assertFalse(v.check(self.db, self.project, d['id'])['current'])
        r.host_action(self.db, 'delivery', {'id': d['id'], 'state': 'unknown'})
        self.assertIn('dispatch_unknown', v.check(self.db, self.project, d['id'])['blockers'])
        r.host_action(self.db, 'delivery', {'id': d['id'], 'state': 'delivered'})
        r.configure(self.db, self.project, 'pause', {})
        self.assertIn('automation_disabled_or_paused', v.check(self.db, self.project, d['id'])['blockers'])
        r.configure(self.db, self.project, 'resume', {})
        self.report(d, 'started')
        self.human_move('backlog')
        cancel = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'cancel')
        self.assertIn('stale_revision', v.check(self.db, self.project, d['id'])['blockers'])
        r.host_action(self.db, 'delivery', {'id': cancel['id'], 'state': 'delivered'})
        self.assertTrue(v.check(self.db, self.project, cancel['id'])['current'])
        self.report(cancel, 'stopped')
        self.assertFalse(v.check(self.db, self.project, cancel['id'])['current'])

    def test_compact_check_requires_plan_approval_and_exposes_report_state(self):
        from kanban_mcp import workflow_views as v
        d = self.assigned()
        self.report(d, 'started')
        self.report(d, 'plan_ready', {'plan': 'docs/plan.md'})
        self.human_move('todo')
        implementation = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'implement')
        r.host_action(self.db, 'delivery', {'id': implementation['id'], 'state': 'delivered'})
        self.assertTrue(v.check(self.db, self.project, implementation['id'])['current'])
        ack = r.report(self.db, 'codex', self.id(), implementation['id'], 'started', {})
        self.assertEqual(ack['state'], 'pending')
        self.assertFalse(ack['duplicate'])
        self.assertEqual(v.check(self.db, self.project, implementation['id'])['reports'][0]['state'], 'pending')
        self.report(implementation, 'started')
        ack = r.report(self.db, 'codex', self.id(), implementation['id'], 'started', {})
        self.assertEqual((ack['state'], ack['duplicate']), ('verified', True))
        with r.transaction(self.db, trusted=True) as c:
            c.execute('UPDATE ticket_runs SET plan_approved=NULL WHERE item_id=?', (self.item,))
        self.assertIn('plan_not_approved', v.check(self.db, self.project, implementation['id'])['blockers'])

    def test_manager_queue_is_paginated_and_preserves_busy_teams(self):
        from kanban_mcp import workflow_views as v
        d = self.assigned()
        for index in range(3):
            item = self.db.create_item(self.project, 'feature', 'Pending ' + str(index))
            token = r.browser_user.set('kanban')
            try:
                self.db.set_status(item, 'needs_plan')
            finally:
                r.browser_user.reset(token)
        r.consume(self.db, self.project)
        r.host_action(self.db, 'capacity', {'project': self.project, 'available': [self.id()]})
        first = v.queue(self.db, self.project, limit=2)
        second = v.queue(self.db, self.project, limit=2, offset=first['next_offset'])
        self.assertEqual((first['total'], len(first['pending']), len(second['pending'])), (3, 2, 1))
        self.assertIsNone(second['next_offset'])
        self.assertFalse(first['teams'][0]['available'])
        self.assertEqual(first['teams'][0]['active_items'], [self.item])
        self.assertNotIn('dispatches', first)
        with self.assertRaises(ValueError):
            v.queue(self.db, self.project, limit=0)

    def test_compact_opencode_session_and_report_identity(self):
        from kanban_mcp import workflow_views as v
        session = 'ses_exact_opaque_session'
        worker = 'opencode:' + session
        r.configure(self.db, self.project, 'worker_add', {
            'team': self.id(), 'identity': {'client': 'opencode', 'chat_id': session, 'title': 'Worker'}})
        self.human_move('needs_plan')
        r.consume(self.db, self.project)
        ack = r.assign(self.db, self.project, 'manager', self.item, self.id(), worker)
        self.assertNotIn('tickets', ack)
        r.host_action(self.db, 'delivery', {'id': ack['dispatch_id'], 'state': 'delivered'})
        result = v.check(self.db, self.project, ack['dispatch_id'])
        self.assertTrue(result['current'])
        self.assertEqual(result['dispatch']['target'], {'client': 'opencode', 'chat_id': session})
        report = r.report(self.db, 'opencode', session, ack['dispatch_id'], 'started', {})
        self.assertEqual(report['state'], 'pending')
        with self.assertRaises(ValueError):
            r.report(self.db, 'codex', session, ack['dispatch_id'], 'started', {})

    def test_compact_integration_requires_approval_and_lease(self):
        from kanban_mcp import workflow_views as v
        self.reviewed()
        self.human_move('done')
        d = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'integrate')
        r.host_action(self.db, 'delivery', {'id': d['id'], 'state': 'delivered'})
        self.assertIn('integration_lease_missing', v.check(self.db, self.project, d['id'])['blockers'])
        r.host_action(self.db, 'lease', {'item_id': self.item})
        self.assertTrue(v.check(self.db, self.project, d['id'])['current'])
        with r.transaction(self.db, trusted=True) as c:
            c.execute('UPDATE ticket_runs SET merge_approved=NULL WHERE item_id=?', (self.item,))
            c.execute('UPDATE integration_lease SET item_id=NULL WHERE item_id=?', (self.item,))
        self.assertIn('merge_not_approved', v.check(self.db, self.project, d['id'])['blockers'])

    def test_compact_tools_use_ordinary_mcp_arguments(self):
        import asyncio
        import json
        from kanban_mcp.core import KanbanMCPServer
        server = KanbanMCPServer()
        server.current_project_id = self.project
        d = self.assigned()
        for name, arguments in [('workflow_check', {'dispatch_id': d['id']}),
                                ('workflow_ticket', {'item_id': self.item}),
                                ('workflow_queue', {'limit': 1, 'offset': 0})]:
            response = asyncio.run(server.handle_request({
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': name, 'arguments': arguments}}))
            self.assertNotIn('error', response)
            result = json.loads(response['result']['content'][0]['text'])
            self.assertNotIn('plan_text', result)
            self.assertNotIn('events', result)
        server.db._backend._get_connection().close()

    def test_retained_backlog_reentry_waits_for_verified_stop(self):
        d = self.assigned()
        self.report(d, 'started')
        self.human_move('backlog')
        cancel = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'cancel')
        self.human_move('needs_plan')
        pending = r.consume(self.db, self.project)
        self.assertFalse(any(x['kind'] in ('plan', 'assign') for x in pending))
        self.report(cancel, 'stopped')
        plans = [x for x in r.consume(self.db, self.project) if x['kind'] == 'plan']
        self.assertEqual(len(plans), 1)
        self.assertEqual((plans[0]['worker'], plans[0]['revision']), (self.worker, 1))
        self.assertEqual(len([x for x in r.consume(self.db, self.project) if x['kind'] == 'plan']), 1)

    def test_rapid_retained_backlog_roundtrip_preserves_request(self):
        from kanban_mcp import workflow_views as v
        d = self.assigned()
        self.report(d, 'started')
        self.human_move('backlog')
        self.human_move('needs_plan')
        cancel = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'cancel')
        self.report(cancel, 'stopped')
        plan = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'plan')
        r.host_action(self.db, 'delivery', {'id': plan['id'], 'state': 'delivered'})
        self.assertTrue(v.check(self.db, self.project, plan['id'])['current'])
        self.assertEqual(plan['revision'], cancel['revision'])

    def test_retained_reentry_does_not_approve_later_scope_or_bypass_lease(self):
        d = self.assigned()
        self.report(d, 'started')
        self.human_move('backlog')
        cancel = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'cancel')
        self.human_move('needs_plan')
        r.consume(self.db, self.project)
        with self.db._db_cursor(commit=True) as c:
            c.execute('UPDATE items SET title=? WHERE id=?', ('New scope after request', self.item))
        cancel = next(x for x in r.consume(self.db, self.project)
                      if x['kind'] == 'cancel' and x['revision'] == 2)
        self.report(cancel, 'stopped')
        self.assertFalse(any(x['kind'] == 'plan' for x in r.consume(self.db, self.project)))

    def test_retained_reentry_still_waits_for_integration_lease_resolution(self):
        d = self.assigned()
        self.report(d, 'started')
        self.human_move('backlog')
        cancel = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'cancel')
        self.human_move('needs_plan')
        r.host_action(self.db, 'lease', {'item_id': self.item})
        try:
            self.report(cancel, 'stopped')
            self.assertFalse(any(x['kind'] == 'plan' for x in r.consume(self.db, self.project)))
        finally:
            with r.transaction(self.db, trusted=True) as c:
                c.execute('UPDATE integration_lease SET item_id=NULL WHERE item_id=?', (self.item,))

    def test_delete_withdrawn_ticket_cleans_workflow_foreign_keys(self):
        self.human_move('needs_plan')
        dispatch = r.consume(self.db, self.project)[0]
        self.human_move('backlog')
        r.consume(self.db, self.project)
        self.assertTrue(self.db.delete_item(self.item)['success'])
        with self.db._db_cursor() as c:
            c.execute('PRAGMA foreign_key_check')
            self.assertEqual(c.fetchall(), [])
            for table in ('ticket_runs', 'workflow_events', 'workflow_dispatches'):
                c.execute('SELECT count(*) FROM ' + table + ' WHERE item_id=?', (self.item,))
                self.assertEqual(c.fetchone()[0], 0)

    def test_delete_closed_ticket_cleans_reports_and_preserves_other_tickets(self):
        dispatch = self.assigned()
        self.report(dispatch, 'started')
        with r.transaction(self.db, trusted=True) as c:
            c.execute("UPDATE ticket_runs SET phase='closed' WHERE item_id=?", (self.item,))
        other = self.db.create_item(self.project, 'feature', 'Preserve unrelated ticket')
        self.db.delete_item(self.item)
        self.assertIsNotNone(self.db.get_item(other))
        with self.db._db_cursor() as c:
            c.execute('SELECT count(*) FROM workflow_reports WHERE dispatch_id=?', (dispatch['id'],))
            self.assertEqual(c.fetchone()[0], 0)
            c.execute('PRAGMA foreign_key_check')
            self.assertEqual(c.fetchall(), [])

    def test_parent_deletion_is_atomic_when_child_has_active_work(self):
        dispatch = self.assigned()
        parent = self.db.create_item(self.project, 'epic', 'Parent')
        self.db.set_parent(self.item, parent)
        with self.assertRaises(Exception):
            self.db.delete_item(parent)
        self.assertIsNotNone(self.db.get_item(parent))
        self.assertIsNotNone(self.db.get_item(self.item))
        self.assertTrue(any(d['id'] == dispatch['id'] for d in r.snapshot(self.db, self.project)['dispatches']))
        from kanban_mcp import web
        web.db = self.db
        response = web.app.test_client().delete('/api/items/' + str(self.item))
        self.assertEqual(response.status_code, 409)

    def test_submission_is_atomic_and_paused_dispatch_cannot_send(self):
        d = self.assigned()
        claimed = r.host_action(self.db, 'submitted', {'id': d['id']})
        self.assertTrue(claimed['claimed'])
        self.assertFalse(r.host_action(self.db, 'submitted', {'id': d['id']})['claimed'])
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'retry', {'item_id': self.item})

    def test_unassigned_request_can_be_withdrawn_and_requested_again(self):
        self.human_move('needs_plan')
        old = r.consume(self.db, self.project)[0]
        r.host_action(self.db, 'native_queue', {'id': old['id']})
        self.human_move('backlog')
        r.consume(self.db, self.project)
        run = r.snapshot(self.db, self.project)['tickets'][0]
        self.assertEqual((run['phase'], run['cancel_pending'], run['revision']), ('released', 0, 1))
        self.assertFalse(r.host_action(self.db, 'native_claim', {'id': old['id']})['claimed'])
        with self.assertRaises(Exception):
            self.db.set_status(self.item, 'needs_plan')
        self.human_move('needs_plan')
        pending = r.consume(self.db, self.project)
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0]['id'], old['id'])
        self.assertEqual(pending[0]['revision'], 1)
        self.assertEqual(len(r.consume(self.db, self.project)), 1)
        run = r.snapshot(self.db, self.project)['tickets'][0]
        self.assertEqual(run['phase'], 'queued')

    def test_legacy_unassigned_cancellation_recovers_without_replaying_request(self):
        self.human_move('needs_plan')
        r.consume(self.db, self.project)
        self.human_move('backlog')
        r.consume(self.db, self.project)
        with self.db._db_cursor(commit=True) as c:
            c.execute("UPDATE ticket_runs SET phase='queued',cancel_pending=1,reason='Legacy stop' WHERE item_id=?",
                      (self.item,))
        self.assertEqual(r.consume(self.db, self.project), [])
        run = r.snapshot(self.db, self.project)['tickets'][0]
        self.assertEqual((run['phase'], run['cancel_pending'], run['reason']), ('released', 0, None))
        self.assertEqual(self.db.get_item(self.item)['status_name'], 'backlog')

    def test_explicit_release_after_stop_still_allows_manager_reassignment(self):
        d = self.assigned()
        self.report(d, 'started')
        self.human_move('backlog')
        cancellation = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'cancel')
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'release', {'item_id': self.item})
        self.report(cancellation, 'stopped')
        r.host_action(self.db, 'release', {'item_id': self.item})
        self.human_move('needs_plan')
        pending = r.consume(self.db, self.project)
        self.assertEqual(len(pending), 1)
        self.assertEqual((pending[0]['kind'], pending[0]['revision']), ('assign', 1))

    def test_restart_preserves_outbox_and_backup(self):
        import sqlite3
        d = self.assigned()
        r.host_action(self.db, 'submitted', {'id': d['id']})
        reopened = KanbanDB()
        self.assertFalse(any(x['id']==d['id'] for x in r.consume(reopened, self.project)))
        backup = str(Path(scratch.name) / (self.project + '.backup'))
        source = sqlite3.connect(os.environ['KANBAN_SQLITE_PATH'])
        with sqlite3.connect(backup) as target:
            source.backup(target)
            self.assertEqual(target.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertFalse(target.execute('PRAGMA foreign_key_check').fetchall())
            for table in ('teams','team_workers','ticket_runs','workflow_events',
                          'workflow_dispatches','workflow_reports','orchestration_settings'):
                self.assertEqual(source.execute('SELECT * FROM '+table).fetchall(),
                                 target.execute('SELECT * FROM '+table).fetchall())
        source.close()

    def test_native_delivery_claim_and_receipt_are_guarded(self):
        d = self.assigned()
        r.host_action(self.db, 'native_queue', {'id': d['id']})
        self.assertTrue(any(x['id'] == d['id'] for x in r.consume(self.db, self.project)))
        r.configure(self.db, self.project, 'pause', {})
        self.assertFalse(r.host_action(self.db, 'native_claim', {'id': d['id']})['claimed'])
        r.configure(self.db, self.project, 'resume', {})
        self.assertTrue(r.host_action(self.db, 'native_claim', {'id': d['id']})['claimed'])
        self.assertFalse(r.host_action(self.db, 'native_claim', {'id': d['id']})['claimed'])
        with self.assertRaisesRegex(ValueError, 'target mismatch'):
            r.host_action(self.db, 'native_receipt', {'id': d['id'], 'thread_id': 'foreign'})
        receipt = {'id': d['id'], 'thread_id': self.id()}
        self.assertTrue(r.host_action(self.db, 'native_receipt', receipt)['delivered'])
        self.assertTrue(r.host_action(self.db, 'native_receipt', receipt)['delivered'])
        self.report(d, 'started')
        self.assertTrue(r.host_action(self.db, 'native_receipt', receipt)['delivered'])
        stored = next(x for x in r.snapshot(self.db, self.project)['dispatches'] if x['id'] == d['id'])
        self.assertEqual(stored['state'], 'acknowledged')

    def test_native_claim_rejects_changed_revision(self):
        d = self.assigned()
        r.host_action(self.db, 'native_queue', {'id': d['id']})
        self.human_move('backlog')
        r.consume(self.db, self.project)
        self.assertFalse(r.host_action(self.db, 'native_claim', {'id': d['id']})['claimed'])

    def test_browser_auth_context_and_invalid_preview_are_enforced(self):
        from kanban_mcp import web
        web.db = self.db
        client = web.app.test_client()
        response = client.post('/api/items/'+str(self.item)+'/status',
                               json={'status':'needs_plan'}, headers={'Authorization':'Basic aHVtYW46c2VjcmV0'})
        self.assertEqual(response.status_code, 200)
        event = r.consume(self.db, self.project)[0]
        r.assign(self.db, self.project, 'manager', self.item, self.id(), self.worker)
        with self.assertRaises(Exception):
            self.human_move('done')
        snapshot = client.get('/api/projects/'+self.project+'/workflow').json
        self.assertNotIn('secret', str(snapshot))

    def test_failed_dispatch_can_retry_once_without_replaying_uncertain_delivery(self):
        d = self.assigned()
        self.report(d, 'failed', {'reason':'Fixture failure'})
        r.host_action(self.db, 'retry', {'item_id':self.item})
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'retry', {'item_id':self.item})
        queued = [x for x in r.consume(self.db, self.project) if x['kind']=='plan']
        self.assertEqual(len(queued), 1)
        self.assertNotEqual(queued[0]['id'], d['id'])

    def inline_data(self, dispatch):
        return {'id': dispatch['id'], 'item_id': self.item, 'client': 'codex',
                'chat_id': self.id(), 'revision': dispatch['revision'],
                'head': 'a' * 40, 'plan_hash': 'hash'}

    def test_inline_retry_is_atomic_exact_worker_and_does_not_queue_delivery(self):
        from kanban_mcp import workflow_views as v
        d = self.assigned()
        self.report(d, 'failed', {'reason': 'Fixture failure'})
        data = self.inline_data(d)
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'retry_inline', {**data, 'chat_id': 'foreign'})
        result = r.host_action(self.db, 'retry_inline', data)
        self.assertTrue(result['resumed'])
        child = result['checkpoint']['dispatch']
        self.assertTrue(result['checkpoint']['current'])
        self.assertEqual(child['state'], 'acknowledged')
        self.assertEqual(child['event_id'], d['event_id'])
        self.assertFalse(any(x['id'] == child['id'] for x in r.consume(self.db, self.project)))
        again = r.host_action(self.db, 'retry_inline', data)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['checkpoint']['dispatch']['id'], child['id'])
        self.report(child, 'started')
        self.report(child, 'plan_ready', {'plan': 'docs/plan.md'})
        self.assertEqual(self.db.get_item(self.item)['status_name'], 'plan_review')
        self.assertEqual(v.check(self.db, self.project, d['id'])['dispatch']['state'], 'failed')

    def test_inline_retry_rejects_pause_unverified_uncertain_stale_and_competing_retry(self):
        d = self.assigned()
        data = self.inline_data(d)
        r.host_action(self.db, 'delivery', {'id': d['id'], 'state': 'unknown'})
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'retry_inline', data)
        r.host_action(self.db, 'delivery', {'id': d['id'], 'state': 'failed'})
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'retry_inline', data)
        self.report(d, 'failed', {'reason': 'Fixture failure'})
        r.configure(self.db, self.project, 'pause', {})
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'retry_inline', data)
        r.configure(self.db, self.project, 'resume', {})
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'retry_inline', {**data, 'revision': 999})
        r.host_action(self.db, 'retry', {'item_id': self.item})
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'retry_inline', data)

    def test_inline_retry_rejects_new_scope_after_verified_failure(self):
        d = self.assigned()
        self.report(d, 'failed', {'reason': 'Fixture failure'})
        self.human_move('backlog')
        r.consume(self.db, self.project)
        with self.assertRaises(ValueError):
            r.host_action(self.db, 'retry_inline', self.inline_data(d))

    def test_inline_retry_integrate_keeps_approval_commit_and_lease_gates(self):
        self.reviewed()
        self.human_move('done')
        d = next(x for x in r.consume(self.db, self.project) if x['kind'] == 'integrate')
        r.host_action(self.db, 'lease', {'item_id': self.item})
        try:
            self.report(d, 'failed', {'reason': 'Fixture failure'})
            data = self.inline_data(d)
            with self.assertRaises(ValueError):
                r.host_action(self.db, 'retry_inline', {**data, 'head': 'b' * 40})
            with self.assertRaises(ValueError):
                r.host_action(self.db, 'retry_inline', {**data, 'plan_hash': 'changed'})
            result = r.host_action(self.db, 'retry_inline', data)
            self.assertTrue(result['checkpoint']['current'])
            self.assertTrue(result['checkpoint']['ticket']['approvals']['merge'])
        finally:
            with r.transaction(self.db, trusted=True) as c:
                c.execute('UPDATE integration_lease SET item_id=NULL WHERE item_id=?', (self.item,))

    def test_pause_and_new_event_boundary(self):
        self.human_move('needs_plan')
        r.configure(self.db, self.project, 'arm', {})
        self.assertEqual(r.consume(self.db, self.project), [])
        r.configure(self.db, self.project, 'pause', {})
        self.assertEqual(r.consume(self.db, self.project), [])

    def test_dependency_blocks_integration_lease(self):
        blocker = self.db.create_item(self.project, 'feature', 'Unfinished dependency')
        self.db.add_relationship(blocker, self.item, 'blocks')
        with self.assertRaisesRegex(ValueError, 'blocked'):
            r.host_action(self.db, 'lease', {'item_id':self.item})
        with self.db._db_cursor() as c:
            c.execute('SELECT item_id FROM integration_lease WHERE singleton=1')
            self.assertIsNone(c.fetchone()[0])

    def test_integration_lease_serializes(self):
        self.assertTrue(r.host_action(self.db, 'lease', {'item_id': self.item})['acquired'])
        self.assertFalse(r.host_action(self.db, 'lease', {'item_id': self.item + 1})['acquired'])
        r.host_action(self.db, 'verify_report', {'id': -1})
        with self.db._db_cursor(commit=True) as c:
            c.execute('UPDATE integration_lease SET item_id=NULL')


if __name__ == '__main__': unittest.main()
