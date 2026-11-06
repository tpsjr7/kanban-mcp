-- SQLite executes this within the deleting statement, including cascaded children.
-- Keep live work and its claim/lease intact until normal stop/release completes.
CREATE TRIGGER team_delete_item BEFORE DELETE ON items
BEGIN
 SELECT RAISE(ABORT,'Stop and release assigned work before deleting this ticket')
 WHERE EXISTS(SELECT 1 FROM ticket_runs WHERE item_id=OLD.id
              AND ((worker IS NOT NULL AND phase!='closed') OR cancel_pending=1))
    OR EXISTS(SELECT 1 FROM integration_lease WHERE item_id=OLD.id);
 DELETE FROM workflow_reports WHERE dispatch_id IN
     (SELECT id FROM workflow_dispatches WHERE item_id=OLD.id);
 DELETE FROM workflow_dispatches WHERE item_id=OLD.id;
 DELETE FROM workflow_events WHERE item_id=OLD.id;
 DELETE FROM ticket_runs WHERE item_id=OLD.id;
END;
