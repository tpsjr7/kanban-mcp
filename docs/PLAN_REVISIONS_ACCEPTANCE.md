# Plan Review revision acceptance

Updated: 2026-11-06. Owner: Kanban Agent.
Status: standalone acceptance passed; integrating host reports deployment and
verified live Codex/OpenCode checks. Interactive visual browser review unavailable.

## Implementation

- Kanban feature: `d835b080715d322afb9d0fccd5832a802cdd7422`.
- music_gen adapter/guidance: `f6d25040577dfef633074e512f6a5a76ef77ed8a`.
- Candidate image: `music-gen-kanban:plan-revision-candidate`, identity
  `sha256:3c1ee364af3418749966c0dd77a8f7443ef0bd9b939237155332bb3537a7aac2`.

Migration 011 adds a pending request fence, saved-plan timestamp, verified request
receipts and immutable prior plan snapshots. Historical timestamps are derived
from existing Plan Review status history where available; missing evidence fails
closed. No historical author is invented. Accepted revisions increment workflow
revision without changing the Plan Review column or creating a cancellation.
Old approvals cannot start implementation while revision is pending.

The focused MCP tool records a pending request. The host verifies its exact
client-owned user message, full text, timestamp, current plan hash and retained
owner before arming a revise_plan dispatch. The host CLI can acknowledge it in
the current turn, including safely adopting an unclaimed queued delivery.
Submitted/uncertain deliveries are never blindly adopted or resent. New plan
submission rechecks human source and a clean, committed Markdown plan. Both
clients keep ordinary arguments and opaque session IDs.

## Verified checks

| Check | Result |
| --- | --- |
| Workflow/database tests in network-isolated candidate container | 52 passed |
| Host message/worker/workflow tests | 16 passed |
| Real Docker helper and host CLI integration, Codex and OpenCode identities | 2 passed |
| Desktop socket framing/discovery/bounded history | 9 passed |
| Existing browser Jest regressions | 50 passed |
| music_gen adapter and maintained-test guard audit | 3 passed; experiments restored |

Workflow cases cover same-column editing, pending approval rejection, exact
worker registration, missing receipts, pause/withdrawal, revision/hash races,
competing requests, idempotent/lost receipts, used-message rejection, correction
of a rejected source, opaque OpenCode IDs, partial-edit retries and multiple
plan snapshots. The real Flask status endpoint returns HTTP 409 with a focused
pending-revision explanation and retains Plan Review. SQLite online backup into
a disposable restored database passed integrity/foreign-key checks and preserved
request state and historical plan hash/author snapshots.

The CLI integration invoked the registered MCP function and checked its ordinary
integer/string schema, then used actual network-isolated Docker helpers and
SQLite databases with both client identities. It checked same-turn dispatch
acknowledgment, duplicate-free repeated requests, retained plan/owner preflight,
Unicode, private request-file input and absence of queued self-messages. Its
human-authority adapter is explicitly a fake fixture, not evidence of a real
client's human request. Disposable volumes and checkouts were removed afterward.

Read-only inspection of the installed clients also passed: this Codex chat's
exact current human approval was retrieved through the native desktop socket;
an exact historical OpenCode session/message was retrieved through its installed
API. Both retained exact IDs, user roles, full text and source timestamps.
The historical OpenCode message was not used to authorize ticket work. Native
history is bounded to ten recent turns; missing/older/truncated content blocks
verification. Codex CLI source reads were covered with protocol fixtures, not a
new live owning CLI session.

No fresh real client session has yet revised a plan through the deployed tool,
and OpenCode Code Mode end-to-end acceptance remains required. Ordinary MCP
schema/CLI checks and read-only source inspection do not substitute for it.

## Rollout gate

No production board migration, runtime reload, client configuration mutation,
real ticket revision or claim change was performed in this task. Deployment
needs human authorization under the workspace rules and the approved plan.

After authorization: take an online board backup, build/start only Kanban with
migration 011, reload its supervisor, and reconnect both MCP clients. Run real
Codex and OpenCode revisions using explicitly authorized acceptance tickets and
fresh human requests sent after each saved plan. Verify exact session/message
identity, browser visibility, blocked approval while editing, new plan approval,
restart persistence and a disposable restored backup. Preserve the named volume,
public origin/authentication, unrelated claims and current product tickets.
Only then record live acceptance and mark rollout complete.

Client-source verification relies on local client records and operating-system
ownership checks; it is not cryptographic proof of physical human presence.
Workers must never create a prompt via an API to manufacture authorization, use
agent/tool/delegated text, or substitute the newest session for an exact ID.

## Host integration outcome

After explicit authorization, the integrating host deployed migration 011 and
verified real Codex and OpenCode Code Mode revisions against disposable live
fixtures. Both edited Unicode plans, reported ready, repeated requests without
duplicates, and read each other's persisted hashes after a web restart. Online
backup restoration passed all-table fingerprints. Pending approval was blocked;
post-ready gates were rehearsed without committing implementation authority.

The host retained exact source validation after an OpenCode caller normalized
escaped text incorrectly; a structured request then passed. The Codex test used
a fixture-scoped relay and temporary per-tool settings instead of a rejected
broad automatic-approval flag. No global settings were changed. The additional
relay scope regressions passed (3 tests). Interactive browser automation was
unavailable; rendered HTML and browser HTTP routes were checked instead.

Host-specific deployment IDs, live ticket/session IDs, backups and detailed
receipts are maintained by the integrating repository, outside this publishable
project. Earlier pending-rollout statements above describe the isolated stage.
