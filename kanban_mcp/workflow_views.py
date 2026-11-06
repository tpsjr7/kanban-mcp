"""Small, project-scoped workflow reads for agent checkpoints.

These inspect board state. Host evidence verification and caller identity checks
remain mandatory; a read response cannot grant new execution authority.
"""
import json

from kanban_mcp.team_runtime import transaction


def settings(c, project):
    c.execute('SELECT enabled,paused FROM orchestration_settings WHERE project_id=?', (project,))
    row = c.fetchone()
    return {'enabled': bool(row and row['enabled']),
            'paused': not row or bool(row['paused'])}


def ticket_row(c, project, item_id):
    c.execute('SELECT i.id AS item_id,s.name AS status,r.team,r.worker,r.revision,r.phase,'
              'r.reason,r.cancel_pending,r.plan_revision_pending,r.plan,r.plan_hash,r.plan_path,r.plan_saved_at,r.plan_approved,'
              'r.merge_approved,r.preview,r.commit_hash,t.instance '
              'FROM items i JOIN statuses s ON s.id=i.status_id '
              'LEFT JOIN ticket_runs r ON r.item_id=i.id '
              'LEFT JOIN teams t ON t.project_id=i.project_id AND t.name=r.team '
              'WHERE i.project_id=? AND i.id=?', (project, item_id))
    return c.fetchone()


def ticket_summary(row):
    return {
        'item_id': row['item_id'], 'status': row['status'],
        'managed': row['revision'] is not None,
        'team': row['team'], 'instance': row['instance'], 'worker': row['worker'],
        'revision': row['revision'], 'phase': row['phase'], 'reason': row['reason'],
        'cancel_pending': bool(row['cancel_pending']),
        'plan_revision_pending': row['plan_revision_pending'],
        'plan': {'path': row['plan_path'] or row['plan'], 'hash': row['plan_hash'], 'saved_at': row['plan_saved_at']},
        'approvals': {
            'plan': row['revision'] is not None and row['plan_approved'] == row['revision'],
            'merge': row['revision'] is not None and row['merge_approved'] == row['revision']},
        'preview': row['preview'], 'commit': row['commit_hash'],
    }


def ticket(db, project, item_id):
    with transaction(db) as c:
        row = ticket_row(c, project, item_id)
        if not row:
            return {'found': False}
        return {'found': True, 'automation': settings(c, project), **ticket_summary(row)}


def check(db, project, dispatch_id):
    with transaction(db) as c:
        return check_cursor(c, project, dispatch_id)


def check_cursor(c, project, dispatch_id):
    c.execute('SELECT d.id,d.item_id,d.worker,d.target,d.kind,d.revision,d.state,d.event_id '
              'FROM workflow_dispatches d JOIN items i ON i.id=d.item_id '
              'WHERE d.id=? AND i.project_id=?', (dispatch_id, project))
    d = c.fetchone()
    if not d:
        return {'found': False, 'current': False, 'blockers': ['dispatch_not_found']}
    row = ticket_row(c, project, d['item_id'])
    automation = settings(c, project)
    blockers = []
    if not automation['enabled'] or automation['paused']:
        blockers.append('automation_disabled_or_paused')
    if row['revision'] != d['revision']:
        blockers.append('stale_revision')
    if d['state'] not in ('delivered', 'acknowledged', 'submitted', 'native_submitted'):
        blockers.append('dispatch_' + d['state'])
    if row['cancel_pending'] and d['kind'] != 'cancel':
        blockers.append('cancellation_pending')
    if d['kind'] != 'assign' and row['worker'] != d['worker']:
        blockers.append('assignment_changed')
    stages = {'assign': ('needs_plan',), 'plan': ('needs_plan', 'planning'),
              'revise_plan': ('plan_review',), 'implement': ('todo', 'in_progress'), 'integrate': ('done',)}
    if d['kind'] in stages and row['status'] not in stages[d['kind']]:
        blockers.append('stage_changed')
    if d['kind'] == 'cancel' and not row['cancel_pending']:
        blockers.append('cancellation_not_pending')
    if d['kind'] in ('implement', 'integrate') and row['plan_approved'] != row['revision']:
        blockers.append('plan_not_approved')
    if d['kind'] == 'integrate' and row['merge_approved'] != row['revision']:
        blockers.append('merge_not_approved')
    c.execute('SELECT e.id,e.browser_user,a.name AS old_status,b.name AS new_status,e.revision '
              'FROM workflow_events e JOIN statuses a ON a.id=e.old_status '
              'JOIN statuses b ON b.id=e.new_status WHERE e.id=? AND e.item_id=?',
              (d['event_id'], d['item_id']))
    origin = c.fetchone()
    revision_request = None
    if d['kind'] == 'revise_plan':
        c.execute('SELECT * FROM plan_revision_requests WHERE id=? AND item_id=? AND state=\'active\'',
                  (row['plan_revision_pending'], d['item_id']))
        request = c.fetchone()
        if (not request or request['expected_revision'] + 1 != d['revision']
                or not request['receipt'] or request['client'] != json.loads(d['target'])['client']
                or request['chat_id'] != json.loads(d['target'])['chat_id']):
            blockers.append('human_revision_request_missing')
        else:
            revision_request = {key: request[key] for key in ('id', 'request_text', 'message_id', 'editing_started')}
    elif d['kind'] != 'cancel':
        expected = {'assign': 'needs_plan', 'plan': 'needs_plan',
                    'implement': 'todo', 'integrate': 'done'}
        revisions = (d['revision'], d['revision'] - 1) if d['kind'] == 'plan' else (d['revision'],)
        if (not origin or not origin['browser_user']
                or origin['new_status'] != expected.get(d['kind'])
                or origin['revision'] not in revisions):
            blockers.append('human_request_missing')
    target = json.loads(d.pop('target'))
    d['target'] = {key: target.get(key) for key in ('client', 'chat_id')}
    if d['kind'] == 'assign':
        c.execute('SELECT manager FROM orchestration_settings WHERE project_id=?', (project,))
        configured = c.fetchone()
        manager = json.loads(configured['manager']) if configured and configured['manager'] else {}
        if {key: manager.get(key) for key in ('client', 'chat_id')} != d['target']:
            blockers.append('manager_registration_changed')
        if row['team'] is not None or row['worker'] is not None:
            blockers.append('assignment_already_recorded')
    if d['worker']:
        c.execute('SELECT client,chat_id FROM team_workers WHERE project_id=? AND team=? AND id=?',
                  (project, row['team'], d['worker']))
        if c.fetchone() != d['target']:
            blockers.append('worker_registration_changed')
    if d['kind'] == 'integrate':
        c.execute('SELECT item_id FROM integration_lease WHERE singleton=1')
        lease = c.fetchone()
        if not lease or lease['item_id'] != d['item_id']:
            blockers.append('integration_lease_missing')
    c.execute('SELECT id,kind,state FROM workflow_reports WHERE dispatch_id=? ORDER BY id',
              (dispatch_id,))
    reports = c.fetchall()
    integration = None
    c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='integration_attempts'")
    if c.fetchone():
        c.execute('SELECT id,phase,record FROM integration_attempts WHERE dispatch_id=? '
                  'ORDER BY updated_at DESC,rowid DESC LIMIT 1', (dispatch_id,))
        attempt = c.fetchone()
        if attempt:
            details = json.loads(attempt['record'])
            integration = {key: details.get(key) for key in
                           ('id', 'phase', 'approved', 'main', 'candidate', 'artifact')}
    return {'found': True, 'current': not blockers, 'blockers': blockers,
            'integration_candidate': integration, 'plan_revision_request': revision_request,
            'automation': automation, 'dispatch': d, 'ticket': ticket_summary(row),
            'human_request': origin, 'reports': reports,
            'host_checks_required': d['kind'] != 'assign'}

def queue(db, project, limit=20, offset=0):
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError('limit must be 1..100 and offset must be nonnegative')
    with transaction(db) as c:
        c.execute('SELECT capacity FROM orchestration_settings WHERE project_id=?', (project,))
        row = c.fetchone()
        capacity = json.loads(row['capacity'] or '[]') if row else []
        c.execute('SELECT name,instance FROM teams WHERE project_id=? ORDER BY name', (project,))
        teams = c.fetchall()
        for team in teams:
            c.execute('SELECT r.item_id FROM ticket_runs r JOIN items i ON i.id=r.item_id '
                      "WHERE i.project_id=? AND r.team=? AND r.phase!='closed' ORDER BY r.item_id",
                      (project, team['name']))
            team['active_items'] = [row['item_id'] for row in c.fetchall()]
            c.execute('SELECT id,client,chat_id,title FROM team_workers '
                      'WHERE project_id=? AND team=? ORDER BY id', (project, team['name']))
            team['workers'] = c.fetchall()
            team['available'] = team['name'] in capacity and not team['active_items'] and bool(team['workers'])
        predicate = ('FROM ticket_runs r JOIN items i ON i.id=r.item_id '
                     'JOIN statuses s ON s.id=i.status_id '
                     "WHERE i.project_id=? AND s.name='needs_plan' AND r.team IS NULL "
                     "AND r.phase='queued' AND r.cancel_pending=0 ")
        c.execute('SELECT count(*) AS total ' + predicate, (project,))
        total = c.fetchone()['total']
        c.execute('SELECT r.item_id,r.revision,(SELECT d.id FROM workflow_dispatches d '
                  "WHERE d.item_id=r.item_id AND d.revision=r.revision AND d.kind='assign' "
                  'ORDER BY d.rowid DESC LIMIT 1) AS dispatch_id ' + predicate
                  + 'ORDER BY r.item_id LIMIT ? OFFSET ?',
                  (project, limit, offset))
        pending = c.fetchall()
        return {'automation': settings(c, project), 'teams': teams, 'pending': pending,
                'total': total, 'next_offset': offset + limit if offset + limit < total else None,
                'availability_requires_host_recheck': True}
