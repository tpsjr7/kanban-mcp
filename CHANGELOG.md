# Unreleased: verified integration candidates

- Add independent exact-delta/merge-tree proof and immutable candidate parents.
- Fence publication with an expected-old Git ref transaction and explicit
  worktree/crash states; preserve dirty/untracked data and unknown outcomes.
- Add migration 010 for private candidate phase/evidence records and require
  verified candidate completion before closing those integration attempts.
- Keep runtime quiescence, testing, restart and cleanup in the trusted adapter;
  candidate scripts contain no music_gen/evaluation dependency.

# Unreleased: fetch missing task content

- Require workers to call get_item when the description or acceptance criteria
  are unavailable. Compact workflow responses omit those fields intentionally.
- Clarify Codex/OpenCode guidance: fetch content before reporting a blocker;
  do not stop, repeat workflow_ticket or request a new dispatch for the omission.

# Unreleased: distinguish manager and worker startup

- Manager assignment prompts use workflow_check plus workflow_queue before a
  worker exists; do not require worker host preflight or worker reports.
- Legacy manager preflight returns its role and next action without inspecting
  an instance. Worker ownership and evidence checks remain required.
- Reject stale manager registrations and already-assigned manager dispatches.

# Unreleased: discover rotating per-user desktop sockets

- Discover legacy and per-user app-tools directories dynamically from the UID.
- Verify the desktop-advertised pipe path as a hint using live listener/process
  ownership, socket type and private permissions; never select by recency.
- Continue scanning after individual IPC descriptors close during enumeration.
- Preserve compatibility checks, bounded calls and uncertain-send protection.

# Unreleased: retained-assignment planning re-entry

- Migration 009 permits human Backlog → Needs Plan requests for retained workers.
- Keep planning blocked until the exact stop is verified and no integration lease
  remains. Preserve rapid browser round trips without approving later scope edits.
- Retain the original assignment and existing explicit-release/reassignment path.

# Unreleased: two-check startup and inline retry

- Batch host identity, instance claim, Git and approval evidence checks in
  automation preflight; avoid redundant workflow/ticket reads at startup.
- Add an exact-worker inline retry for host-verified failures. Atomically create
  one acknowledged child dispatch without a socket send or a new chat turn.
- Preserve revision, human approval, uncertain-delivery, ownership and integration
  lease gates, and retain ordinary operator retry for idle-worker dispatch.

# Unreleased: focused agent workflow tools

- Add workflow_check, workflow_ticket and paginated workflow_queue, omitting full
  plan text, unrelated tickets and historical event/dispatch payloads.
- Return compact assignment and report acknowledgments, including report state.
- Use focused checkpoints in Codex/OpenCode instructions and dispatch prompts;
  retain workflow_status for explicit diagnostics and host orchestration.

# Unreleased: resume requested replanning

- Resume a recorded human Needs Plan request after its current cancellation is
  verified, retaining the same worker, instance claim and committed work.
- Preserve the originating browser event and require fresh plan approval.
- Reject stale stop reports and acknowledge only their exact cancellation.

# Unreleased: managed ticket deletion

- Add SQLite migration 008 to clean up workflow dependencies during item deletion.
- Reject deletion of active assignments or integration leases, including active descendants, with a browser conflict response.
- Preserve transactional rollback and unrelated tickets.

# Changelog

## v0.2.0

### Breaking Changes
- Default database backend changed from MySQL to SQLite
- `install.sh --auto` now sets up SQLite (use `--auto --mysql` for MySQL)
- `install.ps1 -Auto` now sets up SQLite (use `-Auto -MySQL` for MySQL)

### Added
- SQLite backend — zero-config default, no database server required
- Pluggable database backend abstraction (`DatabaseBackend` base class)
- `--mysql` flag for install.sh / `-MySQL` switch for install.ps1
- `[mysql]` extra for MySQL connector dependency
- `[full]` extra combining `[mysql]` and `[semantic]`
- `KANBAN_BACKEND` env var to force backend selection
- `KANBAN_SQLITE_PATH` env var to customize SQLite database location
- Functional test infrastructure (Docker-based, 4 scenarios)
- `install-linux-mysql` CI job for MySQL install path

### Fixed
- web.py: 8 raw SQL queries now use `db._sql()` for cross-backend
  placeholder translation
- SQLite schema: datetime columns use TIMESTAMP type for proper parsing
- SQLite schema: ON DELETE CASCADE on items/updates foreign keys
- Python 3.12+ deprecation warning for sqlite3 timestamp converter

### Changed
- Install scripts default to SQLite (MySQL opt-in via flag)
- CI install jobs use SQLite by default
- macOS CI no longer requires Homebrew MySQL

## Plan Review revisions

- Add focused revision requests and host-verified human message receipts for
  Codex and OpenCode, preserving the assigned instance and Plan Review column.
- Fence Todo approval while editing, retain plan snapshot history, support
  same-turn acknowledgment and retry, and preserve history in SQLite backups.
- Add additive migration 011; existing clients must reconnect for the new tool.
