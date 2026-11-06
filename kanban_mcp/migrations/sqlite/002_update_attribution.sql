-- Existing updates remain unattributed. New rows must identify their source.
ALTER TABLE updates ADD COLUMN source_client TEXT;
ALTER TABLE updates ADD COLUMN browser_username TEXT;
ALTER TABLE updates ADD COLUMN chat_id TEXT;
ALTER TABLE updates ADD COLUMN chat_title TEXT;
ALTER TABLE updates ADD COLUMN agent_name TEXT;

CREATE TRIGGER require_update_attribution
BEFORE INSERT ON updates
WHEN NEW.source_client IS NULL
  OR NEW.source_client NOT IN ('browser', 'codex', 'opencode')
  OR (NEW.source_client = 'browser' AND (
      length(trim(coalesce(NEW.browser_username, ''))) = 0
      OR NEW.chat_id IS NOT NULL OR NEW.agent_name IS NOT NULL))
  OR (NEW.source_client IN ('codex', 'opencode') AND (
      length(trim(coalesce(NEW.chat_id, ''))) = 0
      OR length(trim(coalesce(NEW.agent_name, ''))) = 0
      OR NEW.browser_username IS NOT NULL))
BEGIN
  SELECT RAISE(ABORT,
    'Update identity required. Reconnect MCP and supply client, chat_id, and agent_name or chat_title. Browser updates require Basic Auth.');
END;
