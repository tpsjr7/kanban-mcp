CREATE TABLE integration_attempts (
 id TEXT PRIMARY KEY,
 dispatch_id TEXT NOT NULL REFERENCES workflow_dispatches(id) ON DELETE CASCADE,
 item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
 phase TEXT NOT NULL,
 record TEXT NOT NULL,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX integration_attempt_dispatch ON integration_attempts(dispatch_id,updated_at);
