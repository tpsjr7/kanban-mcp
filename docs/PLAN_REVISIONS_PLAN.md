# Revise a plan in Plan Review

Status: implementation approved and completed; isolated acceptance passed; live rollout pending.
Updated: 2026-11-06. Owner: Kanban Agent.

## Behavior

A human can ask the assigned worker in its current Codex or OpenCode chat to
revise a saved plan while its ticket remains in Plan Review. The worker requests
a focused planning revision and edits the plan in its retained instance. No
Needs Plan round trip, manager reassignment, implementation or deployment occurs.

The current workflow completes a planning dispatch when plan_ready is verified.
Its completed identity cannot authorize a later edit, and existing planning
checks only accept Needs Plan/Planning. Reusing that dispatch or editing the file
without replacing the board's saved hash would leave stale review evidence.

## Request and authority

Introduce a focused request operation, with matching ordinary MCP arguments and
a host CLI route, for the exact assigned worker. Include client, exact chat or
session ID, item ID, expected workflow revision and plan hash, request text,
idempotency key and an exact reference to the human's chat request.

A ticket description, agent-authored update, model summary, or request-source
string alone does not establish human authority. The host must verify the exact
human request and target using a supported client-specific source before arming
an executable revision. Preserve a durable verification receipt. Codex must use
the exact chat/message; OpenCode must use the exact session/message, with opaque
ses_ identifiers accepted. Never infer identity from the newest active session.
If verification is unavailable, return a focused blocker and leave the old plan
and approvals untouched. Do not weaken this gate for one client.

Before implementation, establish and test the installed clients' exact-message
verification paths. Do not claim that an MCP identity argument authenticates a
human. A browser-authenticated request can be an explicit fallback, but cannot
silently substitute for the requested chat interaction.

## Durable revision and approval fence

- Accept only managed Plan Review tickets with the same registered team, worker,
  instance and current verified plan, no pending cancellation, integration lease
  or conflicting active dispatch. Verify instance ownership before editing.
- Atomically record the verified human request, increment the workflow revision,
  clear approvals and mark plan revision pending. Preserve the previous saved
  plan, author, path/hash and revision as review history.
- Create an exact-target plan-revision dispatch tied to that request. Keep the
  visible column Plan Review while it runs. The normal worker startup remains
  two checks; its checkpoint includes the revision request and plan reference.
- While revision is pending, reject browser Plan Review to Todo, stale approval
  events and unrelated agent plan submissions. Never dispatch implementation
  against the previous hash. Request creation and approval must serialize so
  either the revision wins or a prior approval causes revision to be rejected.
- The worker edits only the requested plan scope. A plan_ready report for the
  new revision invokes host verification of the assigned checkout, Markdown
  path, content/hash, exact dispatch identity and unchanged request authority.
  Once verified, replace the current plan snapshot and clear pending state,
  retaining Plan Review. Human Plan Review to Todo then approves the new hash.
- Repeated request/report calls are idempotent. A changed or concurrent request
  cannot reuse an old verification receipt or race an active writer. A failure
  retains the assignment and pending state, with a focused same-revision retry;
  it never restores stale approval automatically.
- Pause, withdrawal, reassignment and cancellation invalidate revision work using
  the existing verified-stop protocol. New plan scope still needs human review.
  No plan revision authorizes code changes, merge, deployment or workspace rules.

## Implementation ownership

Independent Kanban owns the additive migration/request history, focused MCP
schema and CLI protocol, dispatch checks, prompts, browser approval fence,
report validation, compact retrieval and reusable tests. Preserve existing
migration IDs and historical attribution. Include revision records in backups.

music_gen owns only the adapter's instance/claim and client-message authority
verification, plus shared guidance and integration acceptance. Keep the runtime
independent of music_gen and the retired evaluation checkout.

Document both clients' ordinary calls, exact identity/message discovery, retry
and cancellation. OpenCode uses its discovered Code Mode execute catalog. No
connection-wide identity or Codex-only arguments are required.

## Acceptance and rollout

Use disposable databases and fake adapters first. Cover both clients, exact and
foreign identities, missing/agent-authored/stale human requests, changed plan
hashes, lost receipts, duplicate requests/reports, approval races, unchanged
column, multiple revisions, pending/failure/retry states, pause/withdrawal and
verified stop. Confirm historical author/path/hash snapshots survive backup and
disposable restore. Existing planning, implementation and integration must pass.

Run real Codex and OpenCode plan-revision sessions through their normal tools.
Each must verify its own exact session and human request, revise the plan, and
observe the resulting path/hash in browser and compact tools. Confirm Todo is
blocked during editing and works after verification of the revised plan. Do not
modify a real ticket solely for acceptance without human authorization.

Commit source/tests/guidance in their owning repositories. Back up the live
board before migration and restart only Kanban after deployment authorization.
Record precise test and rollout results; distinguish protocol tests from real
client verification. No live ticket, runtime, approval or claim changes are
part of writing this plan.

## Implementation record

See [acceptance](PLAN_REVISIONS_ACCEPTANCE.md) for committed implementation IDs,
isolated checks, actual source-message inspection and the remaining live rollout
gates. The production board has not been migrated or restarted for this feature.
