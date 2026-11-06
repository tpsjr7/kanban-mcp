"""Durable board orchestration. Host transport never executes ticket text."""
import contextvars
import hashlib
import json
import uuid
from contextlib import contextmanager

browser_user = contextvars.ContextVar('kanban_browser_user', default=None)
PLANNING = [('needs_plan', 'Needs Plan'), ('planning', 'Planning'),
            ('plan_review', 'Plan Review')]


@contextmanager
def transaction(db, trusted=False):
    connection = db._backend._get_connection()
    connection.create_function('kanban_browser_user', 0, browser_user.get)
    connection.create_function('kanban_workflow_write', 0, lambda: int(trusted))
    try:
        with db._db_cursor(dictionary=True, commit=True) as cursor:
            cursor.execute('BEGIN IMMEDIATE')
            yield cursor
    finally:
        connection.create_function('kanban_workflow_write', 0, lambda: 0)


def setup(db, project):
    """Explicit setup only; enabling never replays historical events."""
    with transaction(db) as c:
        c.execute('INSERT OR IGNORE INTO orchestration_settings(project_id) VALUES(?)', (project,))
        c.execute('SELECT position FROM project_columns WHERE project_id=? AND status_id=2', (project,))
        position = c.fetchone()['position']
        for key, label in reversed(PLANNING):
            c.execute('SELECT id FROM statuses WHERE name=?', (key,))
            row = c.fetchone()
            if row:
                status = row['id']
            else:
                c.execute('INSERT INTO statuses(name) VALUES(?)', (key,))
                status = c.lastrowid
            c.execute('SELECT 1 FROM project_columns WHERE project_id=? AND status_id=?', (project, status))
            if c.fetchone():
                continue
            c.execute('UPDATE project_columns SET position=position+1 WHERE project_id=? AND position>=?',
                      (project, position))
            c.execute('INSERT INTO project_columns VALUES(?,?,?,?,1)', (project, status, label, position))
            c.execute('INSERT INTO column_types SELECT ?,?,id FROM item_types', (project, status))
        c.execute('UPDATE column_revisions SET revision=revision+1 WHERE project_id=?', (project,))


def status(c, item_id):
    c.execute('SELECT i.*,s.name AS status FROM items i JOIN statuses s ON s.id=i.status_id WHERE i.id=?',
              (item_id,))
    row = c.fetchone()
    if not row:
        raise ValueError('Ticket not found')
    return row


def move(c, item, key):
    c.execute('SELECT s.id FROM statuses s JOIN project_columns p ON p.status_id=s.id '
              'WHERE p.project_id=? AND s.name=?', (item['project_id'], key))
    destination = c.fetchone()
    if not destination:
        raise ValueError('Workflow column missing: ' + key)
    c.execute('UPDATE items SET status_id=?,closed_at=CASE WHEN ?=\'closed\' '
              'THEN datetime(\'now\') ELSE NULL END WHERE id=?',
              (destination['id'], key, item['id']))
    c.execute('INSERT INTO status_history(item_id,old_status_id,new_status_id,change_type) '
              "VALUES(?,?,?,'set')", (item['id'], item['status_id'], destination['id']))


    if key == 'closed':
        parent_id = item.get('parent_id')
        while parent_id:
            parent = db_item_for_parent(c, parent_id)
            if not parent or parent['type_id'] != 5:
                break
            c.execute('SELECT 1 FROM ticket_runs WHERE item_id=?', (parent_id,))
            if c.fetchone():
                break
            c.execute('SELECT 1 FROM column_types WHERE project_id=? AND status_id=4 AND type_id=5',
                      (item['project_id'],))
            if not c.fetchone():
                break
            c.execute("WITH RECURSIVE descendants(id) AS (SELECT id FROM items WHERE parent_id=? "
                      'UNION ALL SELECT i.id FROM items i JOIN descendants d ON i.parent_id=d.id) '
                      'SELECT i.id,s.name FROM descendants d JOIN items i ON i.id=d.id '
                      "JOIN statuses s ON s.id=i.status_id WHERE s.name NOT IN ('done','closed') "
                      "OR (s.name='done' AND EXISTS(SELECT 1 FROM ticket_runs tr WHERE tr.item_id=i.id))",
                      (parent_id,))
            if c.fetchone():
                break
            if parent['status_id'] not in (4,5,6):
                c.execute('UPDATE items SET status_id=4 WHERE id=?', (parent_id,))
                c.execute('INSERT INTO status_history(item_id,old_status_id,new_status_id,change_type) '
                          "VALUES(?,?,4,'auto_advance')", (parent_id, parent['status_id']))
            parent_id = parent.get('parent_id')


def db_item_for_parent(c, item_id):
    c.execute('SELECT * FROM items WHERE id=?', (item_id,))
    return c.fetchone()


def dispatch(c, item, target, kind, revision, worker=None, event_id=None):
    c.execute("SELECT id FROM workflow_dispatches WHERE item_id=? AND kind=? AND revision=? "
              "AND state NOT IN ('cancelled','failed','completed')", (item, kind, revision))
    if c.fetchone():
        return
    c.execute('SELECT id FROM workflow_events WHERE item_id=? AND revision=? '
              'AND browser_user IS NOT NULL ORDER BY id DESC LIMIT 1', (item, revision))
    origin = c.fetchone()
    c.execute('INSERT INTO workflow_dispatches(id,item_id,target,kind,revision,worker,event_id) '
              'VALUES(?,?,?,?,?,?,?)',
              (str(uuid.uuid4()), item, json.dumps(target), kind, revision, worker,
               event_id if event_id is not None else (origin['id'] if origin else None)))


def snapshot(db, project):
    with db._db_cursor(dictionary=True) as c:
        result = {}
        for name in ('orchestration_settings', 'teams', 'team_workers'):
            c.execute('SELECT * FROM ' + name + ' WHERE project_id=?', (project,))
            result[name] = c.fetchall()
        c.execute('SELECT r.* FROM ticket_runs r JOIN items i ON i.id=r.item_id WHERE i.project_id=?', (project,))
        result['tickets'] = c.fetchall()
        c.execute('SELECT d.* FROM workflow_dispatches d JOIN items i ON i.id=d.item_id '
                  'WHERE i.project_id=? ORDER BY d.created_at DESC LIMIT 50', (project,))
        result['dispatches'] = c.fetchall()
        c.execute('SELECT p.*,d.item_id FROM workflow_reports p JOIN workflow_dispatches d '
                  'ON d.id=p.dispatch_id JOIN items i ON i.id=d.item_id WHERE i.project_id=? '
                  'ORDER BY p.id DESC LIMIT 50', (project,))
        result['reports'] = c.fetchall()
        c.execute('SELECT e.*,a.name AS old_status,b.name AS new_status FROM workflow_events e '
                  'JOIN items i ON i.id=e.item_id JOIN statuses a ON a.id=e.old_status '
                  'JOIN statuses b ON b.id=e.new_status WHERE i.project_id=? ORDER BY e.id DESC LIMIT 100',
                  (project,))
        result['events'] = c.fetchall()
        return result


def configure(db, project, action, data):
    with transaction(db) as c:
        if action == 'manager_set':
            c.execute('INSERT INTO orchestration_settings(project_id,manager) VALUES(?,?) '
                      'ON CONFLICT(project_id) DO UPDATE SET manager=excluded.manager',
                      (project, json.dumps(data['identity'])))
        elif action == 'manager_clear':
            c.execute('UPDATE orchestration_settings SET manager=NULL,paused=1 WHERE project_id=?', (project,))
        elif action == 'team_add':
            c.execute('INSERT INTO teams VALUES(?,?,?)', (project, data['name'], data['instance']))
        elif action == 'worker_add':
            identity = data['identity']
            c.execute('INSERT INTO team_workers VALUES(?,?,?,?,?,?,?)',
                      (identity['client'] + ':' + identity['chat_id'], project, data['team'],
                       identity['client'], identity['chat_id'], identity.get('host'), identity.get('title')))
        elif action in ('worker_remove', 'team_remove'):
            field, value = ('worker', data['worker']) if action == 'worker_remove' else ('team', data['name'])
            c.execute('SELECT r.item_id FROM ticket_runs r JOIN items i ON i.id=r.item_id '
                      'WHERE i.project_id=? AND r.' + field + "=? AND r.phase!='closed'", (project, value))
            if c.fetchone():
                raise ValueError('Active assignment requires completion or explicit handoff first')
            if action == 'worker_remove':
                c.execute('DELETE FROM team_workers WHERE project_id=? AND id=? AND team=?',
                          (project, value, data.get('team', value)))
            else:
                c.execute('DELETE FROM team_workers WHERE project_id=? AND team=?', (project, value))
                c.execute('DELETE FROM teams WHERE project_id=? AND name=?', (project, value))
        elif action == 'arm':
            c.execute('UPDATE workflow_events SET consumed=1 WHERE item_id IN '
                      '(SELECT id FROM items WHERE project_id=?)', (project,))
            c.execute('UPDATE orchestration_settings SET enabled=1,paused=0 WHERE project_id=?', (project,))
        elif action in ('pause', 'resume', 'stop'):
            c.execute('UPDATE orchestration_settings SET paused=? WHERE project_id=?',
                      (0 if action == 'resume' else 1, project))
        else:
            raise ValueError('Unknown configuration action')
    return snapshot(db, project)



def resume_requested_planning(c, project):
    """Resume a human-requested revision only after its exact stop is verified.

    The durable event and report are the intent record, including for requests
    stopped by older supervisors. Status alone never authorizes a new dispatch.
    """
    c.execute("SELECT r.*,e.id AS request_id FROM ticket_runs r "
              'JOIN items i ON i.id=r.item_id JOIN statuses s ON s.id=i.status_id '
              'JOIN workflow_events e ON e.item_id=r.item_id '
              'JOIN statuses destination ON destination.id=e.new_status '
              'JOIN statuses origin ON origin.id=e.old_status '
              "WHERE i.project_id=? AND s.name='needs_plan' AND r.phase='stopped' "
              "AND r.cancel_pending=0 AND destination.name='needs_plan' "
              'AND e.browser_user IS NOT NULL AND e.consumed=1 AND ('
              "(origin.name!='backlog' AND e.revision=r.revision-1) OR "
              "(origin.name='backlog' AND (e.revision=r.revision OR (e.revision=r.revision-1 "
              'AND EXISTS(SELECT 1 FROM workflow_events withdrawal '
              'JOIN statuses withdrawn ON withdrawn.id=withdrawal.new_status '
              'WHERE withdrawal.id=(SELECT max(prior.id) FROM workflow_events prior '
              'WHERE prior.item_id=e.item_id AND prior.id<e.id) '
              "AND withdrawn.name='backlog' AND withdrawal.browser_user IS NOT NULL "
              'AND withdrawal.consumed=1 AND withdrawal.revision=e.revision))))) '
              'AND NOT EXISTS(SELECT 1 FROM workflow_events newer '
              'WHERE newer.item_id=r.item_id AND newer.id>e.id) '
              'AND NOT EXISTS(SELECT 1 FROM integration_lease l WHERE l.item_id=r.item_id) '
              'AND EXISTS(SELECT 1 FROM workflow_dispatches d '
              'JOIN workflow_reports p ON p.dispatch_id=d.id '
              "WHERE d.item_id=r.item_id AND d.kind='cancel' AND d.state='completed' "
              'AND d.revision=r.revision AND d.worker=r.worker '
              "AND p.worker=r.worker AND p.kind='stopped' AND p.state='verified')",
              (project,))
    for run in c.fetchall():
        c.execute('SELECT * FROM team_workers WHERE id=? AND project_id=? AND team=?',
                  (run['worker'], project, run['team']))
        worker = c.fetchone()
        if not worker:
            continue
        dispatch(c, run['item_id'], worker, 'plan', run['revision'],
                 run['worker'], event_id=run['request_id'])
        c.execute("UPDATE ticket_runs SET phase='assigned',reason=NULL WHERE item_id=?",
                  (run['item_id'],))


def consume(db, project):
    with transaction(db) as c:
        c.execute('SELECT * FROM orchestration_settings WHERE project_id=?', (project,))
        settings = c.fetchone()
        if not settings or not settings['enabled'] or settings['paused'] or not settings['manager']:
            return []
        c.execute('SELECT e.*,a.name AS old_key,b.name AS new_key FROM workflow_events e '
                  'JOIN items i ON i.id=e.item_id JOIN statuses a ON a.id=e.old_status '
                  'JOIN statuses b ON b.id=e.new_status WHERE i.project_id=? AND e.consumed=0 ORDER BY e.id',
                  (project,))
        events = c.fetchall()
        for event in events:
            item = event['item_id']
            c.execute('UPDATE workflow_events SET consumed=1 WHERE id=?', (event['id'],))
            c.execute('SELECT * FROM ticket_runs WHERE item_id=?', (item,))
            run = c.fetchone()
            old, new = event['old_key'], event['new_key']
            if run and event['revision'] != run['revision']:
                quick_return = False
                if (event['browser_user'] and old == 'backlog' and new == 'needs_plan'
                        and run['worker'] and event['revision'] == run['revision'] - 1):
                    c.execute('SELECT e.*,s.name AS destination FROM workflow_events e '
                              'JOIN statuses s ON s.id=e.new_status WHERE e.item_id=? AND e.id<? '
                              'ORDER BY e.id DESC LIMIT 1', (item, event['id']))
                    preceding = c.fetchone()
                    quick_return = bool(preceding and preceding['browser_user']
                                        and preceding['destination'] == 'backlog'
                                        and preceding['revision'] == event['revision'])
                if not quick_return:
                    continue
            if event['browser_user'] and old == 'backlog' and new == 'needs_plan':
                if status(c, item)['status'] != 'needs_plan':
                    continue
                c.execute('INSERT OR IGNORE INTO ticket_runs(item_id) VALUES(?)', (item,))
                c.execute("UPDATE ticket_runs SET phase='queued',reason=NULL WHERE item_id=? "
                          "AND phase='released' AND team IS NULL AND worker IS NULL AND cancel_pending=0",
                          (item,))
                if not run or not run['worker']:
                    dispatch(c, item, json.loads(settings['manager']), 'assign', event['revision'])
                # Retained assignments resume only via the verified-stop
                # reconciliation below; never send them to another team.
            elif run and event['browser_user'] and old == 'plan_review' and new == 'todo':
                if run['plan_hash'] and not run['cancel_pending'] and not run['plan_revision_pending']:
                    c.execute("UPDATE ticket_runs SET plan_approved=revision,phase='todo' WHERE item_id=?", (item,))
                    c.execute('SELECT * FROM team_workers WHERE id=?', (run['worker'],))
                    dispatch(c, item, c.fetchone(), 'implement', run['revision'], run['worker'])
            elif run and event['browser_user'] and old == 'review' and new == 'done':
                if (run['preview'] and run['commit_hash'] and run['plan_approved'] == run['revision']
                        and not run['cancel_pending']):
                    c.execute("UPDATE ticket_runs SET merge_approved=revision,phase='done' WHERE item_id=?", (item,))
                    c.execute('SELECT * FROM team_workers WHERE id=?', (run['worker'],))
                    dispatch(c, item, c.fetchone(), 'integrate', run['revision'], run['worker'])
            elif run and event['browser_user'] and new in ('backlog', 'needs_plan', 'planning', 'plan_review', 'todo', 'in_progress', 'review'):
                if old != new:
                    c.execute('UPDATE ticket_runs SET revision=revision+1,plan_approved=NULL,merge_approved=NULL,'
                              "cancel_pending=1,reason='Human changed stage; stop acknowledgment required' WHERE item_id=?", (item,))
                    c.execute("UPDATE workflow_dispatches SET state='cancelled' WHERE item_id=? "
                              "AND state IN ('queued','native_pending') AND kind!='cancel'", (item,))
        resume_requested_planning(c, project)
        # No worker exists to acknowledge withdrawal of an unassigned request.
        # This also repairs records stranded by older supervisors, retaining history.
        c.execute("UPDATE ticket_runs SET phase='released',cancel_pending=0,reason=NULL "
                  "WHERE team IS NULL AND worker IS NULL AND cancel_pending=1 AND phase='queued' "
                  'AND item_id IN (SELECT i.id FROM items i JOIN statuses s ON s.id=i.status_id '
                  "WHERE i.project_id=? AND s.name='backlog') "
                  'AND NOT EXISTS(SELECT 1 FROM integration_lease l WHERE l.item_id=ticket_runs.item_id)',
                  (project,))
        c.execute("SELECT * FROM workflow_dispatches WHERE state IN ('queued','native_pending') AND item_id IN "
                  '(SELECT id FROM items WHERE project_id=?) ORDER BY created_at', (project,))
        return c.fetchall()


def assign(db, project, chat_id, item_id, team, worker):
    with transaction(db) as c:
        c.execute('SELECT manager FROM orchestration_settings WHERE project_id=?', (project,))
        manager = c.fetchone()
        if not manager or json.loads(manager['manager'])['chat_id'] != chat_id:
            raise ValueError('Only the registered manager can assign tickets')
        item = status(c, item_id)
        if item['project_id'] != project or item['status'] != 'needs_plan':
            raise ValueError('Ticket must belong to this project and need planning')
        c.execute('SELECT r.item_id FROM ticket_runs r JOIN items i ON i.id=r.item_id '
                  "WHERE i.project_id=? AND r.team=? AND r.phase!='closed' AND r.item_id!=?", (project, team, item_id))
        if c.fetchone():
            raise ValueError('Team is busy')
        c.execute('SELECT * FROM team_workers WHERE id=? AND team=? AND project_id=?', (worker, team, project))
        identity = c.fetchone()
        if not identity:
            raise ValueError('Worker is not in this team')
        c.execute('SELECT * FROM ticket_runs WHERE item_id=?', (item_id,))
        previous = c.fetchone()
        if not previous or previous['cancel_pending']:
            raise ValueError('A current human planning request is required')
        if previous['team'] is not None:
            if (previous['team'], previous['worker']) != (team, worker):
                raise ValueError('Existing assignment requires explicit stop and handoff')
            return {'assigned': True, 'duplicate': True}
        c.execute("UPDATE ticket_runs SET team=?,worker=?,phase='assigned',reason=NULL WHERE item_id=?", (team, worker, item_id))
        c.execute('SELECT revision FROM ticket_runs WHERE item_id=?', (item_id,))
        active = c.fetchone()
        if not active:
            raise ValueError('No human planning request was recorded')
        revision = active['revision']
        dispatch(c, item_id, identity, 'plan', revision, worker)
        c.execute("UPDATE workflow_dispatches SET state='acknowledged' WHERE item_id=? AND kind='assign'", (item_id,))
        c.execute("SELECT id FROM workflow_dispatches WHERE item_id=? AND kind='plan' AND revision=? "
                  'ORDER BY rowid DESC LIMIT 1', (item_id, revision))
        planning = c.fetchone()
    return {'assigned': True, 'item_id': item_id, 'team': team, 'worker': worker,
            'revision': revision, 'dispatch_id': planning['id']}


def report(db, client, chat_id, dispatch_id, kind, payload):
    """Reports are verified by the host before they can advance the board."""
    with transaction(db) as c:
        c.execute('SELECT d.*,w.client,w.chat_id,r.revision AS current_revision '
                  'FROM workflow_dispatches d JOIN team_workers w ON w.id=d.worker '
                  'JOIN ticket_runs r ON r.item_id=d.item_id WHERE d.id=?', (dispatch_id,))
        d = c.fetchone()
        if not d or (d['client'], d['chat_id']) != (client, chat_id):
            raise ValueError('Report does not match the assigned worker')
        if d['revision'] != d['current_revision'] or d['state'] == 'cancelled':
            raise ValueError('Dispatch is stale')
        if kind == 'stopped' and d['kind'] != 'cancel':
            raise ValueError('Stopping requires a current cancellation dispatch')
        if kind not in ('started', 'plan_ready', 'preview_ready', 'resolution_ready', 'integrated', 'stopped', 'failed'):
            raise ValueError('Unknown worker report')
        c.execute('SELECT id,state FROM workflow_reports WHERE dispatch_id=? AND kind=?', (dispatch_id, kind))
        existing = c.fetchone()
        if not existing:
            c.execute('INSERT INTO workflow_reports(dispatch_id,worker,kind,payload) VALUES(?,?,?,?)',
                      (dispatch_id, d['worker'], kind, json.dumps(payload)))
            report_id, state = c.lastrowid, 'pending'
        else:
            report_id, state = existing['id'], existing['state']
    return {'accepted': True, 'report_id': report_id, 'dispatch_id': dispatch_id,
            'kind': kind, 'state': state, 'duplicate': bool(existing)}


def host_action(db, action, data):
    """Private stdio-only host control; never exposed as an HTTP or MCP tool."""
    with transaction(db, trusted=True) as c:
        if action == 'plan_revision_arm':
            from kanban_mcp.plan_revisions import arm
            return arm(c, data)
        elif action == 'plan_revision_reject':
            c.execute("UPDATE plan_revision_requests SET state='rejected',reason=? WHERE id=? AND state='pending'",
                      (data['reason'], data['id']))
            return {'rejected': bool(c.rowcount)}
        elif action == 'capacity':
            project = data['project']
            encoded = json.dumps(sorted(data['available']))
            c.execute('SELECT * FROM orchestration_settings WHERE project_id=?', (project,))
            settings = c.fetchone()
            if settings and settings['capacity'] != encoded:
                c.execute('UPDATE orchestration_settings SET capacity=? WHERE project_id=?', (encoded, project))
                if data['available'] and settings['enabled'] and not settings['paused']:
                    c.execute('SELECT r.* FROM ticket_runs r JOIN items i ON i.id=r.item_id '
                              "WHERE i.project_id=? AND r.team IS NULL AND r.phase='queued'", (project,))
                    for run in c.fetchall():
                        c.execute("UPDATE workflow_dispatches SET state='completed' WHERE item_id=? "
                                  "AND kind='assign' AND state='delivered'", (run['item_id'],))
                        dispatch(c, run['item_id'], json.loads(settings['manager']), 'assign', run['revision'])
        elif action == 'native_queue':
            c.execute("UPDATE workflow_dispatches SET state='native_pending' WHERE id=? AND state='queued'", (data['id'],))
        elif action == 'native_receipt':
            c.execute('SELECT target,state FROM workflow_dispatches WHERE id=?', (data['id'],))
            existing = c.fetchone()
            if not existing or json.loads(existing['target']).get('chat_id') != data['thread_id']:
                raise ValueError('Native receipt target mismatch')
            if existing['state'] not in ('native_submitted', 'delivered', 'acknowledged', 'completed', 'failed'):
                raise ValueError('Native dispatch was not claimed')
            receipt = {'transport': 'native-mcp', 'thread_id': data['thread_id']}
            c.execute("UPDATE workflow_dispatches SET state=CASE WHEN state='native_submitted' THEN ? ELSE state END,"
                      'receipt=?,error=? WHERE id=?',
                      ('unknown' if data.get('error') else 'delivered', json.dumps(receipt),
                       data.get('error'), data['id']))
            return {'delivered': True}
        elif action in ('submitted', 'native_claim'):
            next_state = 'native_submitted' if action == 'native_claim' else 'submitted'
            old_state = 'native_pending' if action == 'native_claim' else 'queued'
            c.execute("UPDATE workflow_dispatches SET state=?,receipt=? WHERE id=? AND state=? "
                      'AND EXISTS(SELECT 1 FROM ticket_runs r JOIN items i ON i.id=r.item_id '
                      'JOIN orchestration_settings s ON s.project_id=i.project_id '
                      'WHERE r.item_id=workflow_dispatches.item_id AND r.revision=workflow_dispatches.revision '
                      "AND (r.cancel_pending=0 OR workflow_dispatches.kind='cancel') AND s.enabled=1 AND s.paused=0)",
                      (next_state, json.dumps(data.get('receipt')), data['id'], old_state))
            return {'claimed': c.rowcount == 1}
        elif action == 'delivery':
            c.execute('UPDATE workflow_dispatches SET receipt=?,state=?,error=? WHERE id=?',
                      (json.dumps(data.get('receipt')), data['state'], data.get('error'), data['id']))
        elif action == 'release':
            c.execute("SELECT * FROM ticket_runs WHERE item_id=? AND phase='stopped'", (data['item_id'],))
            if not c.fetchone():
                raise ValueError('A stop acknowledgment is required before release')
            c.execute('SELECT 1 FROM integration_lease WHERE item_id=?', (data['item_id'],))
            if c.fetchone():
                raise ValueError('Resolve integration lease before releasing the team')
            c.execute("UPDATE ticket_runs SET team=NULL,worker=NULL,phase='released',reason=NULL WHERE item_id=?",
                      (data['item_id'],))
        elif action == 'retry':
            item = status(c, data['item_id'])
            c.execute('SELECT * FROM workflow_dispatches WHERE item_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
                      (item['id'],))
            previous = c.fetchone()
            if not previous or previous['state'] != 'failed':
                raise ValueError('Only acknowledged failures may retry; reconcile uncertain delivery first')
            dispatch(c, item['id'], json.loads(previous['target']), previous['kind'],
                     previous['revision'], previous['worker'], event_id=previous['event_id'])
        elif action == 'retry_inline':
            # Private host path: the registered worker is already running. Never
            # queue a message back to that worker or replay an uncertain send.
            from kanban_mcp.workflow_views import check_cursor
            c.execute('SELECT d.*,i.project_id FROM workflow_dispatches d '
                      'JOIN items i ON i.id=d.item_id WHERE d.id=?', (data['id'],))
            previous = c.fetchone()
            if not previous or previous['item_id'] != data['item_id']:
                raise ValueError('Exact failed dispatch and ticket required')
            identity = json.loads(previous['target'])
            if (identity.get('client'), identity.get('chat_id')) != (data['client'], data['chat_id']):
                raise ValueError('Only the exact dispatched worker may resume inline')
            child_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'kanban-inline-retry:' + previous['id']))
            c.execute('SELECT id FROM workflow_dispatches WHERE id=?', (child_id,))
            existing = c.fetchone()
            if existing:
                return {'resumed': False, 'duplicate': True,
                        'checkpoint': check_cursor(c, previous['project_id'], child_id)}
            view = check_cursor(c, previous['project_id'], previous['id'])
            if view['blockers'] != ['dispatch_failed'] or previous['kind'] not in ('plan', 'revise_plan', 'implement', 'integrate'):
                raise ValueError('Inline retry requires a current failed worker dispatch with no other blockers')
            c.execute('SELECT id FROM workflow_dispatches WHERE item_id=? '
                      'ORDER BY created_at DESC,rowid DESC LIMIT 1', (previous['item_id'],))
            if c.fetchone()['id'] != previous['id']:
                raise ValueError('A newer dispatch already exists')
            c.execute("SELECT id FROM workflow_reports WHERE dispatch_id=? AND kind='failed' AND state='verified'",
                      (previous['id'],))
            if not c.fetchone():
                raise ValueError('Failure must be host verified before inline retry')
            run = view['ticket']
            if data['revision'] != run['revision']:
                raise ValueError('Revision changed after host preflight')
            if previous['kind'] in ('implement', 'integrate') and data['plan_hash'] != run['plan']['hash']:
                raise ValueError('Plan changed after host preflight')
            if previous['kind'] == 'integrate' and data['head'] != run['commit']:
                raise ValueError('Reviewed commit changed after host preflight')
            receipt = json.dumps({'transport': 'inline', 'client': data['client'],
                                  'chat_id': data['chat_id'], 'predecessor': previous['id']})
            c.execute('INSERT INTO workflow_dispatches(id,item_id,worker,target,kind,revision,state,receipt,event_id) '
                      "VALUES(?,?,?,?,?,?,'acknowledged',?,?)",
                      (child_id, previous['item_id'], previous['worker'], previous['target'],
                       previous['kind'], previous['revision'], receipt, previous['event_id']))
            c.execute('UPDATE ticket_runs SET reason=NULL WHERE item_id=?', (previous['item_id'],))
            return {'resumed': True, 'duplicate': False,
                    'checkpoint': check_cursor(c, previous['project_id'], child_id)}
        elif action == 'candidate_read':
            c.execute('SELECT record FROM integration_attempts WHERE id=?', (data['id'],))
            row = c.fetchone()
            if not row:
                raise ValueError('Unknown integration attempt')
            return json.loads(row['record'])
        elif action == 'candidate_record':
            from kanban_mcp.workflow_views import check_cursor
            record = data['record']
            c.execute('SELECT i.project_id FROM workflow_dispatches d JOIN items i ON i.id=d.item_id WHERE d.id=?',
                      (record['dispatch_id'],))
            project = c.fetchone()
            if not project:
                raise ValueError('Unknown integration dispatch')
            check = check_cursor(c, project['project_id'], record['dispatch_id'])
            if not check['current'] or check['dispatch']['kind'] != 'integrate':
                raise ValueError('Current approved integration dispatch required')
            if (check['ticket']['commit'] != record['approved']
                    or check['dispatch']['target']['chat_id'] != record['owner']
                    or record.get('client') != check['dispatch']['target']['client']
                    or record['policy'] != 'verified-candidate-v1'):
                raise ValueError('Candidate identity or approved task mismatch')
            phases = ['prepared', 'proof_verified', 'tested', 'quiesce_pending', 'quiesced', 'publishing', 'installed',
                      'restart_pending', 'restarted', 'cleanup_pending', 'cleaned']
            if record['phase'] not in phases + ['abandoned']:
                raise ValueError('Unknown candidate phase')
            c.execute('SELECT * FROM integration_attempts WHERE id=?', (record['id'],))
            previous = c.fetchone()
            if previous:
                old = json.loads(previous['record'])
                for key in ('dispatch_id', 'approved', 'owner', 'policy'):
                    if old[key] != record[key]:
                        raise ValueError('Attempt identity changed')
                abandon = record['phase'] == 'abandoned' and previous['phase'] in ('prepared', 'proof_verified', 'tested')
                difference = (phases.index(record['phase']) - phases.index(previous['phase'])) if record['phase'] in phases and previous['phase'] in phases else -1
                if not abandon and difference not in (0, 1):
                    raise ValueError('Candidate phase must advance one verified boundary at a time')
                for key in ('candidate', 'tree', 'main', 'base'):
                    if old.get(key) and old[key] != record.get(key):
                        raise ValueError('Proof inputs changed; create a new attempt')
            if not previous and record['phase'] != 'prepared':
                raise ValueError('New candidate attempts start prepared')
            c.execute('INSERT INTO integration_attempts(id,dispatch_id,item_id,phase,record) VALUES(?,?,?,?,?) '
                      'ON CONFLICT(id) DO UPDATE SET phase=excluded.phase,record=excluded.record,updated_at=CURRENT_TIMESTAMP',
                      (record['id'], record['dispatch_id'], check['ticket']['item_id'],
                       record['phase'], json.dumps(record)))
            return {'recorded': True, 'id': record['id'], 'phase': record['phase']}
        elif action == 'block':
            c.execute('UPDATE ticket_runs SET reason=? WHERE item_id=?',
                      (data['reason'], data['item_id']))
        elif action == 'lease':
            c.execute('UPDATE integration_lease SET item_id=? WHERE singleton=1 AND (item_id IS NULL OR item_id=?)',
                      (data['item_id'], data['item_id']))
            acquired = c.rowcount == 1
            if acquired:
                status(c, data['item_id'])
                blocked = db._check_blocking_constraint(data['item_id'], 'closed')
                if blocked:
                    raise ValueError('Integration blocked: ' + json.dumps(blocked))
            return {'acquired': acquired}
        elif action == 'verify_report':
            c.execute('SELECT p.*,d.item_id,d.kind AS dispatch_kind,d.revision,d.target AS creator FROM workflow_reports p '
                      'JOIN workflow_dispatches d ON d.id=p.dispatch_id WHERE p.id=? AND p.state=\'pending\'', (data['id'],))
            p = c.fetchone()
            if not p:
                return {'duplicate': True}
            item = status(c, p['item_id'])
            c.execute('SELECT * FROM ticket_runs WHERE item_id=?', (item['id'],))
            run = c.fetchone()
            if run['revision'] != p['revision'] or (p['kind'] != 'stopped' and run['cancel_pending']):
                raise ValueError('Report revision is stale or cancellation is pending')
            payload = json.loads(p['payload'])
            phase = p['kind']
            if phase == 'started':
                if p['dispatch_kind'] not in ('plan', 'revise_plan', 'implement', 'integrate'):
                    raise ValueError('This dispatch only permits stopping')
                allowed = {'plan': ('needs_plan', 'planning'),
                           'revise_plan': ('plan_review',), 'implement': ('todo', 'in_progress'), 'integrate': ('done',)}
                if item['status'] not in allowed[p['dispatch_kind']]:
                    raise ValueError('Ticket stage no longer matches dispatch')
                target = 'planning' if p['dispatch_kind'] == 'plan' else 'in_progress'
                if p['dispatch_kind'] == 'implement' and run['plan_approved'] != run['revision']:
                    raise ValueError('Plan approval missing')
                if p['dispatch_kind'] == 'integrate':
                    target = 'done'
                if p['dispatch_kind'] == 'revise_plan':
                    target = 'plan_review'
                    c.execute('UPDATE plan_revision_requests SET editing_started=1 WHERE id=? AND state=\'active\'',
                              (run['plan_revision_pending'],))
                move(c, item, target)
                tag = 'team:' + run['team']
                c.execute('DELETE FROM item_tags WHERE item_id=? AND tag_id IN '
                          "(SELECT id FROM tags WHERE project_id=? AND name LIKE 'team:%')",
                          (item['id'], item['project_id']))
                c.execute('INSERT OR IGNORE INTO tags(project_id,name,color) VALUES(?,?,?)',
                          (item['project_id'], tag, '#bb86fc'))
                c.execute('INSERT OR IGNORE INTO item_tags(item_id,tag_id) '
                          'SELECT ?,id FROM tags WHERE project_id=? AND name=?',
                          (item['id'], item['project_id'], tag))
                c.execute("UPDATE workflow_dispatches SET state='acknowledged' WHERE id=?", (p['dispatch_id'],))
            elif phase == 'plan_ready':
                if not ((p['dispatch_kind'] == 'plan' and item['status'] == 'planning')
                        or (p['dispatch_kind'] == 'revise_plan' and item['status'] == 'plan_review'
                            and run['plan_revision_pending'])):
                    raise ValueError('Not a planning dispatch')
                if p['dispatch_kind'] == 'revise_plan' and not data.get('revision_authority_verified'):
                    raise ValueError('Revision human request must be reverified before saving')
                creator = json.loads(p['creator'])
                c.execute('UPDATE ticket_runs SET plan=?,plan_hash=?,plan_text=?,phase=?,plan_path=?,'
                          'plan_client=?,plan_chat_id=?,plan_chat_title=?,plan_saved_at=CURRENT_TIMESTAMP WHERE item_id=?',
                          (payload['plan'], data['plan_hash'], data.get('plan_text'), 'plan_review',
                           data.get('plan_path'), creator['client'], creator['chat_id'],
                           creator.get('title'), item['id']))
                from kanban_mcp.plan_revisions import finish
                if p['dispatch_kind'] == 'revise_plan':
                    finish(c, run)
                else:
                    c.execute("UPDATE plan_revision_requests SET state='cancelled' WHERE item_id=? AND state='active'",
                              (item['id'],))
                    c.execute('UPDATE ticket_runs SET plan_revision_pending=NULL WHERE item_id=?', (item['id'],))
                if item['status'] != 'plan_review':
                    move(c, item, 'plan_review')
            elif phase == 'preview_ready':
                if p['dispatch_kind'] != 'implement' or item['status'] != 'in_progress':
                    raise ValueError('Not an implementation dispatch')
                if run['plan_approved'] != run['revision']:
                    raise ValueError('Plan approval missing')
                c.execute("UPDATE ticket_runs SET preview=?,commit_hash=?,merge_approved=NULL,phase='review' WHERE item_id=?",
                          (payload['preview'], payload['commit'], item['id']))
                move(c, item, 'review')
            elif phase == 'resolution_ready':
                if (p['dispatch_kind'] != 'integrate' or item['status'] != 'done'
                        or run['merge_approved'] != run['revision']
                        or not data.get('resolution_verified')):
                    raise ValueError('Verified same-plan integration resolution required')
                c.execute("SELECT 1 FROM integration_attempts WHERE dispatch_id=? AND phase NOT IN "
                          "('prepared','proof_verified','tested','abandoned')", (p['dispatch_id'],))
                if c.fetchone():
                    raise ValueError('Reconcile publication/runtime before returning to review')
                c.execute("UPDATE ticket_runs SET commit_hash=?,preview=?,merge_approved=NULL,phase='review' WHERE item_id=?",
                          (payload['commit'], payload['preview'], item['id']))
                move(c, item, 'review')
                c.execute('UPDATE integration_lease SET item_id=NULL WHERE item_id=?', (item['id'],))
            elif phase == 'integrated':
                c.execute('SELECT * FROM integration_attempts WHERE dispatch_id=? ORDER BY updated_at DESC LIMIT 1',
                          (p['dispatch_id'],))
                attempt = c.fetchone()
                if attempt:
                    candidate = json.loads(attempt['record'])
                    if (attempt['phase'] != 'cleaned' or data.get('candidate_id') != attempt['id']
                            or data.get('candidate_commit') != candidate.get('candidate')):
                        raise ValueError('Verified candidate completion evidence required')
                if p['dispatch_kind'] != 'integrate' or item['status'] != 'done':
                    raise ValueError('Not an integration dispatch')
                if run['merge_approved'] != run['revision']:
                    raise ValueError('Merge approval missing')
                blocked = db._check_blocking_constraint(item['id'], 'closed')
                if blocked:
                    raise ValueError('Completion blocked: ' + json.dumps(blocked))
                c.execute('SELECT item_id FROM integration_lease WHERE singleton=1')
                if c.fetchone()['item_id'] != item['id']:
                    raise ValueError('Serialized integration lease missing')
                move(c, item, 'closed')
                c.execute("UPDATE ticket_runs SET phase='closed',reason=NULL WHERE item_id=?", (item['id'],))
                c.execute('UPDATE integration_lease SET item_id=NULL WHERE item_id=?', (item['id'],))
            elif phase == 'stopped':
                if p['dispatch_kind'] != 'cancel' or not run['cancel_pending']:
                    raise ValueError('No current cancellation is pending')
                c.execute("UPDATE workflow_dispatches SET state='completed' WHERE id=?",
                          (p['dispatch_id'],))
                c.execute("UPDATE plan_revision_requests SET state='cancelled' WHERE item_id=? AND state='active'",
                          (item['id'],))
                c.execute("UPDATE ticket_runs SET cancel_pending=0,plan_revision_pending=NULL,phase='stopped' WHERE item_id=?",
                          (item['id'],))
            elif phase == 'failed':
                c.execute("UPDATE workflow_dispatches SET state='failed' WHERE id=?", (p['dispatch_id'],))
                c.execute('UPDATE ticket_runs SET reason=? WHERE item_id=?', (payload.get('reason', 'Worker failed'), item['id']))
            c.execute("UPDATE workflow_reports SET state='verified' WHERE id=?", (p['id'],))
            if phase != 'failed':
                c.execute('UPDATE ticket_runs SET reason=NULL WHERE item_id=?', (item['id'],))
            if phase == 'started':
                c.execute('UPDATE ticket_runs SET phase=? WHERE item_id=?', (target, item['id']))
            if phase in ('plan_ready', 'preview_ready', 'resolution_ready', 'integrated'):
                c.execute("UPDATE workflow_dispatches SET state='completed' WHERE id=?", (p['dispatch_id'],))
        else:
            raise ValueError('Unknown host action')
    return {'ok': True}


def effectively_complete(db, item_id, key):
    if key == 'closed':
        return True
    if key != 'done':
        return False
    with db._db_cursor() as c:
        c.execute('SELECT 1 FROM ticket_runs WHERE item_id=?', (item_id,))
        return c.fetchone() is None


def ensure_schema(db):
    with db._db_cursor() as c:
        c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name IN "
                  "('team_guard_status','team_scope_change','team_status_event','team_scope_stop')")
        if len(c.fetchall()) != 4:
            raise RuntimeError('Team workflow migration incomplete; reconnect after upgrading')
        c.execute('SELECT event_id FROM workflow_dispatches LIMIT 0')
        c.execute('SELECT plan_path,plan_client,plan_chat_id,plan_chat_title,plan_revision_pending FROM ticket_runs LIMIT 0')
        c.execute('SELECT id,receipt FROM plan_revision_requests LIMIT 0')


def is_managed(db, item_id):
    with db._db_cursor() as c:
        c.execute('SELECT 1 FROM ticket_runs WHERE item_id=?', (item_id,))
        return c.fetchone() is not None
