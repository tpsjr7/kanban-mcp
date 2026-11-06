ALTER TABLE ticket_runs ADD COLUMN plan_revision_pending TEXT;
ALTER TABLE ticket_runs ADD COLUMN plan_saved_at TEXT;
UPDATE ticket_runs SET plan_saved_at=(
 SELECT max(h.changed_at) FROM status_history h JOIN statuses s ON s.id=h.new_status_id
 WHERE h.item_id=ticket_runs.item_id AND s.name='plan_review');
CREATE TABLE plan_revision_requests (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
 client TEXT NOT NULL CHECK(client IN ('codex','opencode')),
 chat_id TEXT NOT NULL, message_id TEXT NOT NULL, request_text TEXT NOT NULL,
 request_key TEXT NOT NULL, expected_revision INTEGER NOT NULL,
 expected_hash TEXT NOT NULL, plan_saved_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 receipt TEXT, dispatch_id TEXT, reason TEXT, editing_started INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(project_id,client,chat_id,request_key)
);
CREATE UNIQUE INDEX used_plan_revision_message
 ON plan_revision_requests(project_id,client,chat_id,message_id) WHERE state IN ('active','completed');
CREATE UNIQUE INDEX one_active_plan_revision ON plan_revision_requests(item_id)
 WHERE state='active';
CREATE TABLE plan_revision_history (
 item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, snapshot TEXT NOT NULL,
 saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(item_id,revision)
);
CREATE TRIGGER plan_revision_approval_fence BEFORE UPDATE OF status_id ON items
WHEN OLD.status_id!=NEW.status_id AND EXISTS(
 SELECT 1 FROM ticket_runs r JOIN statuses s ON s.id=NEW.status_id
 WHERE r.item_id=NEW.id AND r.plan_revision_pending IS NOT NULL AND s.name='todo')
BEGIN
 SELECT RAISE(ABORT,'Plan revision pending; wait for the revised plan');
END;
DROP TRIGGER team_scope_stop;
CREATE TRIGGER team_scope_stop AFTER UPDATE OF revision ON ticket_runs
WHEN NEW.revision!=OLD.revision AND NEW.worker IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM plan_revision_requests q WHERE q.id=NEW.plan_revision_pending
 AND q.state='active' AND q.expected_revision=OLD.revision)
BEGIN
 INSERT INTO workflow_dispatches(id,item_id,worker,target,kind,revision)
 SELECT lower(hex(randomblob(16))),NEW.item_id,w.id,
 json_object('client',w.client,'chat_id',w.chat_id,'title',w.title,'host',w.host),
 'cancel',NEW.revision FROM team_workers w WHERE w.id=NEW.worker;
END;
