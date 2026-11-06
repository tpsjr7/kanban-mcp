"""Request/arm boundary for same-column plan edits. Host evidence is mandatory."""
import hashlib
from datetime import datetime, timezone
import json
import uuid


def current(c, project, data):
    from kanban_mcp.team_runtime import status
    item = status(c, data['item_id'])
    c.execute('SELECT * FROM orchestration_settings WHERE project_id=?', (project,))
    settings = c.fetchone()
    if item['project_id'] != project or not settings or not settings['enabled'] or settings['paused']:
        raise ValueError('Automation disabled, paused or wrong project')
    c.execute('SELECT * FROM ticket_runs WHERE item_id=?', (item['id'],))
    run = c.fetchone()
    if (not run or item['status'] != 'plan_review' or run['cancel_pending']
            or run['plan_revision_pending'] or not run['plan_hash']
            or run['revision'] != data['expected_revision'] or run['plan_hash'] != data['expected_hash']):
        raise ValueError('Current Plan Review revision/hash required; no pending revision')
    c.execute('SELECT * FROM team_workers WHERE id=? AND project_id=? AND team=?',
              (run['worker'], project, run['team']))
    worker = c.fetchone()
    if not worker or (worker['client'], worker['chat_id']) != (data['client'], data['chat_id']):
        raise ValueError('Exact assigned worker required')
    c.execute('SELECT 1 FROM integration_lease WHERE item_id=?', (item['id'],))
    if c.fetchone():
        raise ValueError('Integration lease must be resolved')
    c.execute("SELECT 1 FROM workflow_dispatches WHERE item_id=? AND revision=? "
              "AND kind IN ('plan','revise_plan','implement','integrate','cancel') "
              "AND state NOT IN ('completed','cancelled','failed')", (item['id'], run['revision']))
    if c.fetchone():
        raise ValueError('Conflicting dispatch remains active')
    return run, worker


def request(db, project, data):
    from kanban_mcp.team_runtime import transaction
    for key in ('chat_id', 'message_id', 'request_text', 'request_key', 'expected_hash'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError('Missing ' + key)
    if data.get('client') not in ('codex', 'opencode') or len(data['request_text']) > 16000:
        raise ValueError('Unsupported client or request too long')
    if not isinstance(data.get('expected_revision'), int):
        raise ValueError('Expected revision must be an integer')
    with transaction(db) as c:
        c.execute('SELECT * FROM plan_revision_requests WHERE project_id=? AND client=? AND chat_id=? '
                  'AND request_key=?', (project, data['client'], data['chat_id'], data['request_key']))
        old = c.fetchone()
        if old:
            if any(old[key] != data[key] for key in ('item_id', 'message_id', 'request_text',
                                                   'expected_revision', 'expected_hash')):
                raise ValueError('Idempotency key reused for changed request')
            return summary(old)
        c.execute("SELECT 1 FROM plan_revision_requests WHERE project_id=? AND client=? AND chat_id=? "
                  "AND message_id=? AND state IN ('active','completed')",
                  (project, data['client'], data['chat_id'], data['message_id']))
        if c.fetchone():
            raise ValueError('Human message already used for a plan revision')
        run, _ = current(c, project, data)
        if not run['plan_saved_at']:
            raise ValueError('Saved plan time unavailable; reconcile historical plan evidence')
        identifier = str(uuid.uuid4())
        c.execute('INSERT INTO plan_revision_requests(id,project_id,item_id,client,chat_id,message_id,'
                  'request_text,request_key,expected_revision,expected_hash,plan_saved_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (identifier, project, data['item_id'], data['client'], data['chat_id'], data['message_id'],
                   data['request_text'], data['request_key'], data['expected_revision'], data['expected_hash'], run['plan_saved_at']))
        c.execute('SELECT * FROM plan_revision_requests WHERE id=?', (identifier,))
        return summary(c.fetchone())


def summary(row):
    return {key: row[key] for key in ('id', 'item_id', 'state', 'dispatch_id', 'reason')}


def arm(c, data):
    from kanban_mcp.workflow_views import check_cursor
    c.execute('SELECT * FROM plan_revision_requests WHERE id=?', (data['id'],))
    q = c.fetchone()
    if not q:
        raise ValueError('Unknown plan revision request')
    if q['state'] in ('active', 'completed'):
        if data.get('inline') and q['state'] == 'active':
            c.execute("UPDATE workflow_dispatches SET state='acknowledged',receipt=? WHERE id=? AND state='queued'",
                      (json.dumps({'transport': 'inline', 'client': q['client'], 'chat_id': q['chat_id']}),
                       q['dispatch_id']))
        return {'duplicate': True, **summary(q),
                'checkpoint': check_cursor(c, q['project_id'], q['dispatch_id'])}
    if q['state'] != 'pending':
        raise ValueError('Request is not pending')
    run, worker = current(c, q['project_id'], q)
    receipt = data.get('receipt', {})
    saved_at = datetime.fromisoformat(q['plan_saved_at']).replace(tzinfo=timezone.utc).timestamp()
    expected = hashlib.sha256(q['request_text'].encode()).hexdigest()
    if (receipt.get('client') != q['client'] or receipt.get('chat_id') != q['chat_id']
            or receipt.get('message_id') != q['message_id'] or receipt.get('sha256') != expected
            or receipt.get('role') != 'user' or not receipt.get('verified_at')
            or not isinstance(receipt.get('source_time'), (int, float))
            or receipt['source_time'] < saved_at or run['plan_saved_at'] != q['plan_saved_at']
            or data.get('plan_hash') != q['expected_hash'] or data.get('owner') != q['chat_id']):
        raise ValueError('Verified exact human request, plan and owner receipt required')
    c.execute('INSERT OR IGNORE INTO plan_revision_history(item_id,revision,snapshot) VALUES(?,?,?)',
              (q['item_id'], run['revision'], json.dumps(run)))
    identifier = str(uuid.uuid4())
    c.execute("UPDATE plan_revision_requests SET state='active',receipt=?,dispatch_id=? WHERE id=?",
              (json.dumps(receipt), identifier, q['id']))
    c.execute('UPDATE ticket_runs SET revision=revision+1,plan_revision_pending=?,plan_approved=NULL,'
              "merge_approved=NULL,phase='plan_revising',reason=NULL WHERE item_id=?", (q['id'], q['item_id']))
    target = {key: worker[key] for key in ('client', 'chat_id', 'title', 'host')}
    state = 'acknowledged' if data.get('inline') else 'queued'
    c.execute('INSERT INTO workflow_dispatches(id,item_id,worker,target,kind,revision,state) '
              'VALUES(?,?,?,?,?,?,?)', (identifier, q['item_id'], worker['id'], json.dumps(target),
                                       'revise_plan', run['revision'] + 1, state))
    return {'armed': True, 'id': q['id'], 'dispatch_id': identifier,
            'checkpoint': check_cursor(c, q['project_id'], identifier)}


def finish(c, run):
    pending = run['plan_revision_pending']
    if pending:
        c.execute('SELECT * FROM plan_revision_requests WHERE id=?', (pending,))
        q = c.fetchone()
        if not q or q['state'] != 'active' or q['expected_revision'] + 1 != run['revision']:
            raise ValueError('Plan revision request no longer current')
        c.execute("UPDATE plan_revision_requests SET state='completed' WHERE id=?", (pending,))
    c.execute('UPDATE ticket_runs SET plan_revision_pending=NULL WHERE item_id=?', (run['item_id'],))
    c.execute('SELECT * FROM ticket_runs WHERE item_id=?', (run['item_id'],))
    saved = c.fetchone()
    c.execute('INSERT OR REPLACE INTO plan_revision_history(item_id,revision,snapshot) VALUES(?,?,?)',
              (run['item_id'], run['revision'], json.dumps(saved)))
