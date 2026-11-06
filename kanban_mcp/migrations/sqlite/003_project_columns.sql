CREATE TABLE project_columns (
 project_id TEXT NOT NULL REFERENCES projects(id),
 status_id INTEGER NOT NULL REFERENCES statuses(id),
 label TEXT NOT NULL,
 position INTEGER NOT NULL,
 builtin INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(project_id,status_id), UNIQUE(project_id,label)
);
CREATE TABLE column_types (
 project_id TEXT NOT NULL, status_id INTEGER NOT NULL,
 type_id INTEGER NOT NULL REFERENCES item_types(id),
 PRIMARY KEY(project_id,status_id,type_id),
 FOREIGN KEY(project_id,status_id) REFERENCES project_columns(project_id,status_id) ON DELETE CASCADE
);
CREATE TABLE column_revisions (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), revision INTEGER NOT NULL DEFAULT 0
);
INSERT INTO project_columns SELECT p.id,s.id,s.name,s.id,1 FROM projects p CROSS JOIN statuses s;
INSERT INTO column_types SELECT p.id,w.status_id,w.type_id FROM projects p CROSS JOIN type_status_workflow w;
INSERT INTO column_revisions SELECT id,0 FROM projects;
CREATE TRIGGER seed_project_columns AFTER INSERT ON projects BEGIN
 INSERT INTO project_columns SELECT NEW.id,id,name,id,1 FROM statuses WHERE id <= 6;
 INSERT INTO column_types SELECT NEW.id,status_id,type_id FROM type_status_workflow;
 INSERT INTO column_revisions VALUES(NEW.id,0);
END;
CREATE TRIGGER validate_item_column_insert BEFORE INSERT ON items
WHEN NOT EXISTS(SELECT 1 FROM column_types WHERE project_id=NEW.project_id AND status_id=NEW.status_id AND type_id=NEW.type_id)
BEGIN
 SELECT RAISE(ABORT,'Column does not allow this card type in this project');
END;
CREATE TRIGGER validate_item_column_update BEFORE UPDATE OF status_id,type_id,project_id ON items
WHEN NOT EXISTS(SELECT 1 FROM column_types WHERE project_id=NEW.project_id AND status_id=NEW.status_id AND type_id=NEW.type_id)
BEGIN
 SELECT RAISE(ABORT,'Column does not allow this card type in this project');
END;
