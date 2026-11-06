CREATE TABLE workflow_stage_order (key TEXT PRIMARY KEY, rank INTEGER NOT NULL);
INSERT INTO workflow_stage_order VALUES
 ('backlog',0),('needs_plan',1),('planning',2),('plan_review',3),('todo',4),
 ('in_progress',5),('review',6),('done',7),('closed',8);
CREATE TABLE orchestration_settings (
 project_id TEXT PRIMARY KEY REFERENCES projects(id),
 manager TEXT, capacity TEXT, enabled INTEGER NOT NULL DEFAULT 0,
 paused INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE teams (
 project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
 instance TEXT NOT NULL UNIQUE, PRIMARY KEY(project_id,name)
);
CREATE TABLE team_workers (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, team TEXT NOT NULL,
 client TEXT NOT NULL CHECK(client IN ('codex','opencode')),
 chat_id TEXT NOT NULL, host TEXT, title TEXT,
 UNIQUE(client,chat_id),
 FOREIGN KEY(project_id,team) REFERENCES teams(project_id,name)
);
CREATE TABLE ticket_runs (
 item_id INTEGER PRIMARY KEY REFERENCES items(id),
 team TEXT, worker TEXT,
 revision INTEGER NOT NULL DEFAULT 0,
 phase TEXT NOT NULL DEFAULT 'queued', reason TEXT,
 plan TEXT, plan_hash TEXT, plan_text TEXT, preview TEXT, commit_hash TEXT,
 plan_approved INTEGER, merge_approved INTEGER,
 cancel_pending INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE workflow_events (
 id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
 old_status INTEGER, new_status INTEGER, browser_user TEXT,
 revision INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 consumed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE workflow_dispatches (
 id TEXT PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
 worker TEXT, target TEXT NOT NULL, kind TEXT NOT NULL,
 revision INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 receipt TEXT, error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE workflow_reports (
 id INTEGER PRIMARY KEY, dispatch_id TEXT NOT NULL REFERENCES workflow_dispatches(id),
 worker TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE integration_lease (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), item_id INTEGER UNIQUE
);
INSERT INTO integration_lease VALUES(1,NULL);
CREATE TRIGGER team_scope_change AFTER UPDATE OF title,description ON items
WHEN EXISTS(SELECT 1 FROM ticket_runs WHERE item_id=NEW.id)
 AND (OLD.title IS NOT NEW.title OR OLD.description IS NOT NEW.description)
BEGIN
 UPDATE ticket_runs SET revision=revision+1,plan_approved=NULL,merge_approved=NULL,plan_hash=NULL,
 reason='Scope changed; approvals invalidated',cancel_pending=1 WHERE item_id=NEW.id;
 UPDATE workflow_dispatches SET state='cancelled' WHERE item_id=NEW.id AND state='queued' AND kind!='cancel';
END;
CREATE TRIGGER team_status_event AFTER UPDATE OF status_id ON items
WHEN OLD.status_id != NEW.status_id
BEGIN
 INSERT INTO workflow_events(item_id,old_status,new_status,browser_user,revision)
 SELECT NEW.id,OLD.status_id,NEW.status_id,kanban_browser_user(),
 coalesce((SELECT revision FROM ticket_runs WHERE item_id=NEW.id),0);
END;
CREATE TRIGGER team_guard_status BEFORE UPDATE OF status_id ON items
WHEN OLD.status_id!=NEW.status_id
 AND EXISTS(SELECT 1 FROM ticket_runs WHERE item_id=NEW.id)
 AND kanban_workflow_write()=0
BEGIN
 SELECT RAISE(ABORT,'Managed tickets advance through worker reports or human browser approval')
 WHERE kanban_browser_user() IS NULL;
 SELECT RAISE(ABORT,'Invalid managed workflow transition')
 WHERE NOT EXISTS(
 SELECT 1 FROM statuses a JOIN statuses b ON b.id=NEW.status_id
 JOIN project_columns pa ON pa.status_id=a.id AND pa.project_id=NEW.project_id
 JOIN project_columns pb ON pb.status_id=b.id AND pb.project_id=NEW.project_id
 WHERE a.id=OLD.status_id AND (
 (a.name='plan_review' AND b.name='todo') OR
 (a.name='review' AND b.name='done') OR
 ((SELECT rank FROM workflow_stage_order WHERE key=b.name)<
  (SELECT rank FROM workflow_stage_order WHERE key=a.name) AND b.name!='closed')));
 SELECT RAISE(ABORT,'A current saved plan is required')
 WHERE NEW.status_id=2 AND NOT EXISTS(
 SELECT 1 FROM ticket_runs WHERE item_id=NEW.id AND plan_hash IS NOT NULL AND cancel_pending=0);
 SELECT RAISE(ABORT,'A current reviewed preview is required')
 WHERE NEW.status_id=5 AND NOT EXISTS(
 SELECT 1 FROM ticket_runs WHERE item_id=NEW.id AND preview IS NOT NULL
 AND commit_hash IS NOT NULL AND plan_approved=revision AND cancel_pending=0);
END;
CREATE TRIGGER team_scope_stop AFTER UPDATE OF revision ON ticket_runs
WHEN NEW.revision!=OLD.revision AND NEW.worker IS NOT NULL
BEGIN
 INSERT INTO workflow_dispatches(id,item_id,worker,target,kind,revision)
 SELECT lower(hex(randomblob(16))),NEW.item_id,w.id,
 json_object('client',w.client,'chat_id',w.chat_id,'title',w.title,'host',w.host),
 'cancel',NEW.revision FROM team_workers w WHERE w.id=NEW.worker;
END;
