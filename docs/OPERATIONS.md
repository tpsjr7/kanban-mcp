# Standalone operation

Use Node 22+, Docker with Compose, and Linux flock. SSH is required only for a configured publication adapter.

Run `npm run kanban -- up` to build local source and start the loopback board.
Use `down`, `status`, `mcp`, `backup`, and `restore-check FILE` through the same command.
MCP containers have no networking and mount the same named database volume.
Set KANBAN_PROJECT_DIR, KANBAN_MCP_VOLUME, KANBAN_MCP_IMAGE and KANBAN_CONTAINER explicitly when integrating an existing installation.

Managed automation requires KANBAN_ADAPTER pointing to a trusted host module exporting instanceInfo, claimOwner, privatePath, hashPlan and verifyReport. Optional publish/stopPublication hooks own public access. Ordinary board/MCP use needs no adapter.

Configure the desktop caller with `automation desktop-configure --thread EXACT_ID --turn EXACT_TURN_ID`, using actual runtime metadata. The private record is retained in KANBAN_AUTOMATION_STATE. The desktop client selects a unique socket owned by the running desktop process and validates its catalog. It never chooses the newest socket. Busy chats wait; failed or ambiguous submissions require history reconciliation. No heartbeat or monitoring agent is used.

Keep claims, browser approvals, reviewed plan hashes and integration evidence intact. A receipt is not proof of completed work. Stop all live writers before a real restore; restore-check uses disposable storage.

Tests: `npm ci`, `npm run test:pilot`, `node --test scripts/kanban/workflow.test.mjs`, and `python tests/test_desktop_bridge.py`. Board regression scripts run inside the built image with tests mounted read-only.

`automation reload` restarts only the verified host supervisor without pausing the board. Desktop endpoint disappearance leaves unowned Codex work on the desktop route; only a positively accessible existing CLI owner uses the CLI route. No fallback follows a desktop error.

Deleting a released, unassigned or completed ticket atomically removes its workflow history. Active assignments or integration leases must be stopped and released first; the browser returns HTTP 409 with that explanation. Parent deletion applies the same guard to cascaded children and rolls back the whole delete if any child is active. Deletion remains a manual action.
