# Kanban team workflow

Status: host automation and direct desktop socket delivery enabled; no heartbeat.
Updated: 2026-11-06.

## Commands and prerequisites

The browser board and ordinary MCP access retain their Node/Docker prerequisites.
Host-side chat automation additionally requires Linux `flock`, Python with
`websockets`, the existing owning Codex CLI app-server socket for CLI sessions,
and the installed OpenCode CLI/background API for OpenCode workers. Desktop-owned
chats use the native app relay described below. It does not create another
Codex app-server, change chat models/permissions, answer approval requests or
expose a chat-control network listener. Chat transport is repository-owned under
`scripts/kanban/`; it does not depend on a private skill installation.

Configure or inspect the deployed workflow:

```bash
npm run kanban -- manager set --chat 'Kanban Todo'
npm run kanban -- manager show
npm run kanban -- team add local-a --instance local-a
npm run kanban -- team worker add local-a --client codex --chat 'agent local-a'
npm run kanban -- team list
npm run kanban -- team show local-a
npm run kanban -- team worker add local-a --client opencode --session ses_EXACT_ID
npm run kanban -- team worker remove local-a --worker opencode:ses_EXACT_ID
npm run kanban -- automation start
npm run kanban -- automation status
npm run kanban -- automation pause
npm run kanban -- automation resume
npm run kanban -- automation stop
npm run kanban -- automation retry TICKET_ID
npm run kanban -- automation release TICKET_ID
```

Use the corresponding names for local-b, local-c and local-d. Team/worker commands
print JSON (`--json` is accepted); `--help` describes the commands. `--id` selects
an exact Codex ID instead of resolving a unique title. OpenCode IDs are opaque
strings and use `--session`; titles are read by exact session ID. Removing active
members or teams is rejected. Team names, instance mappings and workers persist
in the shared board database. Models and client permissions stay as configured.

`manager set` creates the planning columns idempotently; it does not arm delivery.
First `automation start` arms from a new event boundary and ignores prior moves.
Start/reuse is protected by a host `flock`. There is one persistent network-isolated
helper container per running supervisor, rather than a new helper per poll.
`kanban up` starts/reuses enabled, unpaused automation; `down` stops only its
verified supervisor. Explicit stop/pause requires resume before new dispatch.
State/logs are private under `~/.local/state/music-gen/kanban-automation/`.
Status exposes ownership, assignments, receipts and blockers. A foreign checkout's
supervisor is never stopped or replaced.

## Desktop delivery

The phone can control the desktop app, while a separate Codex CLI daemon runs
on the same host. Shared chat history does not make those servers interchangeable.
The supervisor inspects the existing writer's process without changing its lock.
Desktop-owned Codex chats enter a durable `native_pending` queue; the app's native
`send_message_to_thread` delivers them. CLI and OpenCode keep their existing routes.
If ownership cannot be inspected, delivery stops rather than guessing a server.
Discovery supports the legacy socket directory and the current per-user directory
(`/tmp/codex-app-tools-<uid>/`). The desktop-advertised
`CODEX_APP_TOOLS_PIPE_PATH` is a hint, accepted only when the live listener is
owned by the current user's desktop process with private permissions. Socket
UUIDs rotate; creation time, file recency and cached environment values never
establish ownership. Inspect runtime processes from the actual host namespace;
the executor sandbox may hide them.

The standalone supervisor now delivers pending desktop messages directly through
the verified desktop-owned Unix socket. It discovers the owned endpoint and uses
recorded exact caller identity. Keep the desktop app available. Ordinary host
polling consumes no model calls while idle. No recurring app heartbeat is enabled.
Configure a trusted adapter for the project canonical path, storage and evidence verification.

Manual recovery remains available:

The relay reads the exact target's status/history, waits for an idle chat, then:

```bash
npm run --silent kanban -- automation native-list --json
npm run --silent kanban -- automation native-claim DISPATCH_ID
# Send the returned prompt once through the native app tool to its exact target.
npm run --silent kanban -- automation native-receipt DISPATCH_ID --thread ID
# If sending fails or its outcome is uncertain:
npm run --silent kanban -- automation native-failure DISPATCH_ID --thread ID --reason TEXT
```

Claiming repeats current revision, approval, plan, checkout and ownership checks,
then atomically changes the state to `native_submitted` before sending. Repeated
claims fail. Receipts validate the exact target and preserve a worker's faster
acknowledgment. After an interruption, inspect actual target history for the
dispatch ID before reconciling a receipt; never resend an uncertain submission.
Pause/stop prevents new claims; stopping the host also prevents relay claims.
Already accepted messages cannot be recalled, so workers recheck workflow state.
The relay only transports approved dispatches; it does not perform ticket work.

Inspect the app's automation settings for relay state. Pausing the board is
sufficient to pause dispatch; pausing the app heartbeat alone leaves desktop
messages queued. Never remove writer locks, start competing daemons or change a
chat's model/permissions to recover a handoff.

## Team and ticket lifecycle

| Browser/worker action | Result and authority |
| --- | --- |
| Human Backlog → Needs Plan | Kanban Todo selects an available team/worker for planning. |
| Worker claims instance and reports started | Host verifies ownership; Planning, with `team:<name>` tag. |
| Worker reports plan_ready | Host reads scoped Markdown and stores its hash/text snapshot; Plan Review. |
| Human Plan Review → Todo | Approves that plan and scope revision; implementation dispatched to the same team. |
| Worker reports started | In Progress. |
| Worker reports preview_ready | Clean task commit, check evidence and official preview URL verified; Review. |
| Human Review → Done | Approves full gtg for the recorded commit; integrations serialize globally. |
| Worker reports integrated | Host verifies main ancestry, restart receipt, public health, clean synchronized instance and released claim; Closed. |

One team handles one active ticket and retains its instance through plan/preview
review. One worker owns edits; other team chats can assist with research or review
without competing writers. Claims remain individual exact chat/session IDs. An
instance occupied by another chat is unavailable; registration never transfers
it. No available team means the request waits. When capacity changes, the manager
receives a new durable assignment notification for queued work.

Open the ticket editor to see its team/worker, automation stage and reason,
plan filesystem path, creator chat link, and preview link. Paths and chat names
render as text; the browser does not serve arbitrary host files. Published previews must match the official instance
URL. Managed Done is integration pending, and does not satisfy dependency or epic
completion; Closed does. Unmanaged tickets retain ordinary column behavior.

## Worker protocol

Use the focused MCP tools on canonical `/workspace/project`:

Manager startup (`assign` dispatches) uses `workflow_check(dispatch_id)` followed
by `workflow_queue`. At this stage, an empty team/worker/instance is expected.
Do not run the worker host preflight, claim an instance, invoke worker inline
retry or send `workflow_report`. Call `workflow_assign` only when a team and its
registered worker are available; otherwise wait for a capacity-change dispatch.
A legacy prompt calling host preflight receives a manager result directing it to
the queue, without accessing any checkout. Current-manager identity, pause,
revision and human-request checks still apply; an already-assigned request is
not actionable. These rules apply to both Codex and OpenCode.

Routine worker startup uses two checks:

1. `workflow_check(dispatch_id)`: compare the exact target client/chat ID, stage and
   revision against the dispatch and runtime identity; stop on blockers. Its ticket
   summary already includes assignment, plan path/hash, preview and report state.
2. Run the host command below with Docker/host escalation on its first invocation.
   It batches exact runtime identity, instance ownership, Git state and applicable
   plan-hash/reviewed-commit checks. Then start the assigned work.

```bash
npm run --silent kanban -- automation preflight EXACT_DISPATCH_ID --client codex
# OpenCode uses the same command with --client opencode.
```

Do not follow the first check with `workflow_ticket` just to reread the same data.
Use that tool only for a standalone workflow lookup. The compact tools intentionally
omit the ticket description and acceptance criteria. If that task content is not
already available, call `get_item(item_id)` and read it before planning or
implementing. This is normal investigation, not an extra startup safety check.
Do not stop, request another dispatch or repeat `workflow_ticket` because its
response lacks task content. Report a requirements blocker only after `get_item`
itself is empty, inaccessible or genuinely ambiguous. For OpenCode, use Code Mode
`execute` with `tools["kanban"]["get_item"]({ item_id: assignedItemID })`.
Normal task investigation and required tests remain necessary.
Use returned report states and the retained checkpoint to avoid repeating completed
steps. Recheck before integration and after material state changes, rather than
between every read. Do not reread unchanged guides or search implementations of
known commands during normal startup.

Manager: `workflow_queue(limit=20, offset=0)` lists pending assignments and registered
team/workers. Follow `next_offset` until null. Availability is the host's last
observation and must be checked again before assigning or claiming an instance.

`workflow_status` is a full diagnostic snapshot with plan text and history. Do not
use it for routine worker checkpoints or poll it while waiting. Deduplicate dispatch
IDs in your checkpoint. Claim only the assigned instance and preserve runtime/GPU
settings. Synchronize clean committed main on initial planning; retain ticket commits
and checkpoints when revising a plan on the same claim. Do not edit primary while
implementing.

Reports use `workflow_report(client, chat_id, dispatch_id, kind, payload)` with
JSON-string payloads. Supply the exact runtime ID (`CODEX_THREAD_ID` or
`OPENCODE_SESSION_ID`) per call. Reports are accepted into an outbox; acceptance
is distinct from host verification and board progression. The compact acknowledgment
returns `report_id`, `state` and `duplicate`; pending means the host has not yet
verified it. Use `workflow_check` to confirm verification at a real checkpoint,
without a full snapshot or a model-driven polling loop.

```javascript
// OpenCode: call through Code Mode execute using the current discovered catalog.
const checkpoint = await tools["kanban"]["workflow_check"]({
  dispatch_id: assignedDispatchID
});
// Stop on blockers or an exact-target mismatch.
// Run the batched host preflight next; do not also fetch workflow_ticket.
// If the task description/acceptance criteria are not already available:
// await tools["kanban"]["get_item"]({ item_id: assignedItemID });
// Missing descriptions in the compact checkpoint are expected, not a blocker.
// Manager only: tools["kanban"]["workflow_queue"]({ limit: 20, offset: 0 });
await tools["kanban"]["workflow_report"]({
  client: "opencode", chat_id: exactSessionID, dispatch_id: assignedDispatchID,
  kind: "plan_ready", payload: JSON.stringify({ plan: "docs/MY_TASK_PLAN.md" })
});
```

Reconnect existing Codex and OpenCode MCP connections after this schema update;
a running disposable MCP container retains its old tool catalog. OpenCode uses
these ordinary arguments through its Code Mode `execute` catalog; no Codex-only
identity or plugin is required.

Codex calls the same MCP tools with `client: "codex"` and its exact thread ID.
The registered manager uses `workflow_assign(chat_id, item_id, team, worker)`;
`worker` is the registered `client:chat_id` identifier. Metadata is caller-supplied
attribution, not cryptographic identity proof. The trusted local host and
nginx-authenticated browser are the authority boundary; never store credentials.

- `started`: empty payload, after taking the matching instance claim.
- `plan_ready`: `{plan: relativeMarkdownPath}`. Preserve the approved plan unchanged.
- `preview_ready`: `{preview, commit, checks:[{result:"passed", evidence:relativePath}]}`.
  Commit is the full current hash; checkout must be clean. Check evidence must
  exist inside that instance. Report real required checks; file existence alone
  does not prove test correctness or musical quality.
- `integrated`: `{health:true, cleanup:true, restart_evidence:relativePathInPrimary}`.
  The JSON receipt must contain current main `commit`, `mode` (`frontend` or
  `full`), `api`, `frontend`, `proxy`, `entry` all true, and ISO `restarted_at`
  after approval. Generate it from the official restart's actual probe results;
  never invent passing checks. Retain the source restart logs.
- `stopped`: checkpoint preserved; stop work without discarding changes.
- `failed`: `{reason}`. Retains stage and assignment with an actionable explanation.

## Approval, failure and recovery

Only recorded browser transitions authorize planning, implementation or gtg.
Agent/MCP moves cannot create approvals. Recheck approval immediately before
writes and integration. Title/description edits invalidate approvals; host checks
also reject changed plan hashes and reviewed commits. Backward browser moves
invalidate approvals and queue a stop request. The team remains reserved until
stop is acknowledged and matching-owner cleanup/release is verified. `automation
release` then releases only the assignment, preserving ticket/history; it does
not delete files or force an instance unlock. To enroll released work again, move
it through Backlog → Needs Plan so the manager receives a fresh planning request.

Returning an unassigned planning request to Backlog cancels its queued dispatch
and releases the request without waiting for a nonexistent worker. After the
supervisor processes that withdrawal, Backlog → Needs Plan creates one fresh
request at the current revision. Assigned work can also return from Backlog to
Needs Plan while retaining its worker/team. The browser records that planning
request even if the withdrawal's stop is pending; planning waits for the exact
current stop acknowledgment and integration-lease resolution. A rapid round trip
before the supervisor consumes the withdrawal is preserved. A later scope edit
invalidates the request. Explicit owner-scoped release remains available when
operator intent is to return the ticket to manager reassignment instead.

Moving assigned work directly back to Needs Plan preserves the team and instance
claim. After the current cancellation's stop report is verified, the supervisor
issues one planning dispatch to that same worker, linked to the human browser
request. Existing commits and checkpoints are retained. The revised plan must
pass Plan Review → Todo again; earlier implementation and merge approvals do not
carry forward. A newer scope revision or stage change blocks this continuation.
This reconciliation also recovers requests stopped by an older supervisor.

Busy chats and foreign claims remain queued. Submitted delivery without a receipt
is uncertain, never automatically resent. Inspect the exact owning chat and
receipt before recovery. `retry` permits acknowledged failures, not uncertain
submission. It continues from the retained checkpoint; it does not authorize
repeating completed side effects. Automatic-review denial is never bypassed with
another sender, socket, permission setting or client.

When the human asks the assigned worker to resume a host-verified failed attempt,
continue in the same turn using the exact failed dispatch ID:

```bash
npm run --silent kanban -- automation retry TICKET_ID --inline FAILED_DISPATCH_ID --client codex
# OpenCode: --client opencode, with its exact OPENCODE_SESSION_ID in the host environment.
```

Use Docker/host escalation on the first attempt. The host verifies the current
runtime ID (`CODEX_THREAD_ID` or `OPENCODE_SESSION_ID`), retained claim, local state,
plan and reviewed commit, then atomically rechecks board approval/revision/lease
and the verified failure. It records an acknowledged child dispatch with an inline
receipt and the original browser-event provenance. Continue under the returned
checkpoint's dispatch ID without ending the turn or waiting for a self-message.
A duplicate returns that same child and its current checkpoint; never replay
completed side effects. Pause, cancellation, a newer dispatch/revision, changed
plan/commit, foreign identity/claim or uncertain delivery blocks this path.
If a runtime variable is absent, resolve it from verified current-session metadata;
never choose the latest session or guess an ID. The caller-supplied ID remains
attribution, not cryptographic authentication.

Plain `automation retry TICKET_ID` remains the operator route for delivery to an
idle worker. An active assigned worker should use `--inline` after an explicit
human resume request instead of sending itself another dispatch. Retry does not
authorize new scope, replay completed integration side effects or approve changed
code; fresh review is still required when the reviewed code changes.

Integrations hold one durable global lease through required primary verification
and cleanup. Failed gtg stays Done and retains the lease until resolved; another
team cannot interleave integration. A transport receipt does not prove work
started or finished. Worker reports and host verification are separate records.

Backups include teams, assignments, approvals, event cursors, receipts and reports.
For restoration, stop all board writers and the supervisor. Restore privately,
set `orchestration_settings.paused=1` in the restored database before starting any
writer, and inspect outstanding dispatches/receipts against actual Git and chats.
Only explicit `automation resume` may restart delivery. Never replay a restored
outbox blindly or overwrite live data during acceptance testing.

See [team plan](OPERATIONS.md) and
[acceptance record](OPERATIONS.md) for rollout status and evidence.

## Plan references

The ticket shows the verified absolute filesystem path when available and a
link to the chat that created the plan. Codex uses `codex://threads/CHAT_ID`;
OpenCode exposes the session ID and copying without an unverified opening URL.
Creator identity is saved when the plan is verified, so later worker changes
do not redirect it. Historical plans retain their stored path without guessed
authors. Plan contents stay out of the ticket panel; review them in the creator
chat. Internal snapshots and hashes remain available for approval validation.
