ALTER TABLE workflow_dispatches ADD COLUMN event_id INTEGER REFERENCES workflow_events(id);
