# Standalone operation

Use Node 22+ and Docker with Compose. Windows uses Docker Desktop in Linux-container mode. SSH is required only for a configured publication adapter; host chat automation additionally requires Linux and flock. Windows execution has not yet been tested.

Run `npm run kanban -- up` to build local source and start the loopback board.
Use `down`, `status`, `mcp`, `backup`, and `restore-check FILE` through the same command.
MCP uses one shared, non-root container per named board volume, with no networking
or published port. Each stdio connection runs a small `docker exec -i` relay to a
Unix socket inside that container. Nothing mounts a Unix socket from the host.
Browser and MCP services work independently and share the named database volume.
Set KANBAN_PROJECT_DIR, KANBAN_MCP_VOLUME, KANBAN_MCP_IMAGE and KANBAN_CONTAINER explicitly when integrating an existing installation.

Managed automation requires KANBAN_ADAPTER pointing to a trusted host module exporting instanceInfo, claimOwner, privatePath, hashPlan and verifyReport. Optional publish/stopPublication hooks own public access. Ordinary board/MCP use needs no adapter.

Configure the desktop caller with `automation desktop-configure --thread EXACT_ID --turn EXACT_TURN_ID`, using actual runtime metadata. The private record is retained in KANBAN_AUTOMATION_STATE. The desktop client selects a unique socket owned by the running desktop process and validates its catalog. It never chooses the newest socket. Busy chats wait; failed or ambiguous submissions require history reconciliation. No heartbeat or monitoring agent is used.

Keep claims, browser approvals, reviewed plan hashes and integration evidence intact. A receipt is not proof of completed work. Stop all live writers before a real restore; restore-check uses disposable storage.

Tests: `npm ci`, `npm run test:pilot`, `node --test scripts/kanban/workflow.test.mjs`, and `python tests/test_desktop_bridge.py`. Board regression scripts run inside the built image with tests mounted read-only.
Worker startup/retry unit checks: `node --test scripts/kanban/worker-start.test.mjs`.
Real host-command acceptance uses disposable Git worktrees and a Docker volume:
`KANBAN_TEST_IMAGE=kanban-mcp:local node --test scripts/kanban/worker-start.integration.test.mjs`.
Build that image first and run with host Docker access; the test sends no chat
messages and removes only its own volume/worktrees.

`automation reload` restarts only the verified host supervisor without pausing the board. Desktop endpoint disappearance leaves unowned Codex work on the desktop route; only a positively accessible existing CLI owner uses the CLI route. No fallback follows a desktop error.

Deleting a released, unassigned or completed ticket atomically removes its workflow history. Active assignments or integration leases must be stopped and released first; the browser returns HTTP 409 with that explanation. Parent deletion applies the same guard to cascaded children and rolls back the whole delete if any child is active. Deletion remains a manual action.

## Shared MCP lifecycle

Build the image with `npm run kanban -- up` before opening the first MCP client.
Register the absolute Node executable and `scripts/kanban.mjs mcp` as the ordinary
stdio command in Codex or OpenCode. Both use unchanged MCP tool arguments; OpenCode
continues to call them through its Code Mode execute catalog. Use the same
KANBAN_PROJECT_DIR, KANBAN_MCP_VOLUME and KANBAN_MCP_IMAGE for every client.

`status` includes MCP container name, image, health, connection count and untracked
legacy containers. A cached chat connection is allowed to stay open; it no longer
owns a container or Python worker. Its host relay renews a small process lease
without model requests. EOF/signals close the session; lost renewal expires it
after 35 seconds. A long synchronous tool call can delay expiry until it returns.
Client calls are serialized against one database connection; session/project
state and request IDs remain separate. Attribution is still supplied per update.

`npm run kanban -- mcp-stop` drains and removes only a compatible, owned MCP
service with no connected clients. `mcp-stop --force` explicitly disconnects
clients or permits recovery of an unresponsive owned service. Neither command
removes board data. `down` stops web/publication and leaves MCP available.
Before rebuilding an already-running MCP image, close/reconnect clients and stop
that service while the old image is still selected. Image/ownership mismatches
fail with an inspection message rather than replacing a service silently.

Concurrent clients use Docker's atomic container name to converge on one service.
Web/backup lifecycle commands use an atomic directory lock under
`~/.local/state/kanban-mcp/`. Its owner.json records the PID and token. A crashed
command can leave a lock; inspect its exact process and operation before manually
reconciling it. Startup never removes another process's lock.

Old per-chat containers have no ownership labels. Do not prune by image or age.
Reconnect their clients, then verify disconnection before retiring any remaining
container. Busy chats keep their old connection until safe to reconnect. A desktop
app restart can close cached connections; it is a user action, not an automatic
part of deployment. New connections use the existing configured launcher path.

Shared runtime checks: `npm run test:mcp`, then
`KANBAN_TEST_IMAGE=kanban-mcp:local npm run test:mcp:integration` with Docker access.
Run `python tests/test_shared_runtime.py` inside the image with tests mounted read-only.
The Docker test uses a disposable named board and verifies 20 concurrent clients,
EOF/signals/abrupt loss, restart, persistence and online backup restoration.

Acceptance and platform limits: [shared MCP runtime](SHARED_MCP_ACCEPTANCE.md).
