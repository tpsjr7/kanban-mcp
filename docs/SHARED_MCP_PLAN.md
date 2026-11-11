# Shared MCP runtime

Status: implemented; final standalone and live adoption checks in progress. Approved 2026-11-10.

Replace a Docker container per client connection with one owned MCP service per
board. Keep web independent, the existing named SQLite volume, canonical project,
non-root execution, disabled MCP networking and ordinary stdio client settings.

## Implementation

- Keep the Unix socket inside the Linux container. A small `docker exec -i`
  relay connects each host stdio launcher to it. This avoids host Unix sockets
  and works with Docker Desktop Linux containers on Windows. Windows execution
  remains unverified until tested on Windows.
- Use Docker's atomic container naming to converge concurrent MCP launchers.
  Validate role, owner, project, volume, security settings and image before reuse.
  Start an absent/stopped compatible service; reject incompatible services.
- Initialize schema once at service startup, share the database and serialize
  calls. Keep tool closures and current-project state separate per connection.
  Attribution remains explicit per update.
- Use asynchronous stdio supervision in the generic launcher and music_gen
  wrapper. Close on EOF/signals, detect orphaned launchers and expire abandoned
  relay sessions through a bounded host-renewed lease. Lease renewal is ordinary
  process IPC, never a model request, chat message or automation heartbeat.
- Report MCP health, image and connection count through status. Add `mcp-stop`
  with a server-side drain gate; refuse connected clients unless `--force` is
  explicit. Web shutdown must leave MCP running.
- Replace host lifecycle flock with an atomic directory lock. Release only the
  recorded owner token; report stale locks for inspection instead of removing
  another owner's lock. Desktop automation remains a separate Linux integration.

## Acceptance and rollout

Test isolated sessions, overlapping request IDs, attribution, malformed input,
20 concurrent clients, concurrent creation, wrong ownership/image, EOF, signals,
abrupt launcher loss, socket/service restart, and busy-stop refusal. Use disposable
storage for writes and recovery. Run actual Codex and OpenCode clients, including
OpenCode Code Mode. Verify MCP with web absent and unchanged live browser health.

Back up before live activation. Reconnect idle clients gradually; never interrupt
busy chats. Remove legacy containers only with exact ownership and disconnected
client evidence. Record deferred legacy clients, Windows limitations, checks and
commit IDs. Keep implementation/tests here; music_gen owns only its wrapper and
integration policy. Commit task-owned changes and run scoped completion checks.
