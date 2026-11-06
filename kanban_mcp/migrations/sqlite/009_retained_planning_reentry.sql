DROP TRIGGER team_guard_status;
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
 (a.name='backlog' AND b.name='needs_plan' AND EXISTS(
  SELECT 1 FROM ticket_runs r WHERE r.item_id=NEW.id AND r.team IS NULL
  AND r.worker IS NULL AND r.phase='released' AND r.cancel_pending=0)) OR
 (a.name='backlog' AND b.name='needs_plan' AND EXISTS(
  SELECT 1 FROM ticket_runs r WHERE r.item_id=NEW.id AND r.team IS NOT NULL
  AND r.worker IS NOT NULL AND r.phase!='closed')) OR
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
