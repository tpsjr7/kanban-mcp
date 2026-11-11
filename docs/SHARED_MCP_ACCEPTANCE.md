# Shared MCP acceptance

Updated: 2026-11-10. Status: Linux checks passed; Windows execution unverified.

## Runtime

One named, non-root container serves independent stdio sessions over its private
internal Unix socket. Host Docker-exec relays forward MCP and renew bounded process
leases. No host socket mount, TCP port or periodic model request is needed.
Migrations initialize before accepting clients under the same database-volume
lock as web initialization. Database calls are serialized; project state, request
IDs and per-update identity remain independent.

Image protocol labels prevent starting old images as shared services. Container
reuse validates role, owner, image, volume, canonical database/project environment
and security settings. Lifecycle stop targets the verified immutable container ID
and uses an atomic service-side drain gate. Ordinary stop refuses connected
clients. Explicit force supports controlled disconnect and unhealthy-service
recovery. Old anonymous per-client containers are inventoried without pruning.

## Checked behavior

- 20 concurrent clients create exactly one container, including startup name
  contention. Docker can reserve a name before inspection sees its object; the
  launcher now waits for that specific reservation before validating/reusing it.
- Independent session project state, overlapping JSON-RPC IDs, Unicode and
  interleaved Codex/OpenCode authors pass. Invalid messages leave other sessions
  available. EOF, SIGTERM and killed launchers release the correct sessions.
- Five socket tests cover identity isolation, duplicate session rejection,
  malformed requests, abandoned-lease expiry and atomic drain behavior.
  Ownership/image/security checks and portable directory-lock tests pass,
  including retaining a changed owner token for manual reconciliation.
- Restart/reconnect preserves records. Online backup restoration into a disposable
  volume passes integrity and foreign-key checks; every table hash/count matches,
  including tickets/statuses, attributed updates, relationships and file links.
- A private fresh Git checkout without a parent integration, evaluation archive,
  experiments, node_modules or host Python environment builds and passes the
  Docker acceptance. Image helper file and directory modes are normalized, so a
  restrictive host checkout umask does not prevent non-root operation.
- Existing Python attribution, columns, timelines and workflow regressions pass.
  Real installed Codex CLI and OpenCode Code Mode sessions each create an update,
  verify their exact current ID and read the other's attribution on disposable
  storage. Selected host-specific receipts stay outside this repository.

## Limits

Windows uses Docker Desktop Linux containers through ordinary stdio/Docker exec;
there is no dependency on Windows Unix-socket mounts or host flock for normal
board/MCP lifecycle. No Windows host was available, so native launch/permissions,
shutdown and path behavior remain unverified. Host chat automation retains its
separate Linux prerequisites. Long synchronous tool calls serialize other calls
and can defer disconnect/lease cleanup until the call returns.

Connected legacy clients require reconnection. Do not infer abandonment from age
or image. No desktop restart or busy-chat interruption was performed for adoption.
