/** Host-side local startup checks and same-turn retries. Never sends chat messages. */
import { control } from './control.mjs';
import { command } from './transport.mjs';
import { instanceInfo, claimOwner, hashPlan } from './evidence.mjs';

const defaults = { control, command, instanceInfo, claimOwner, hashPlan, env: process.env };

export async function workerPreflight(root, dispatchId, client, options = {}, dependencies = {}) {
  const io = { ...defaults, ...dependencies };
  if (!['codex', 'opencode'].includes(client)) throw new Error('Supply --client codex or opencode');
  const chatId = io.env[client === 'codex' ? 'CODEX_THREAD_ID' : 'OPENCODE_SESSION_ID'];
  if (!chatId) throw new Error('Exact current runtime chat/session ID is missing');
  const check = io.control('workflow_check', { dispatch_id: dispatchId });
  if (!check.found) throw new Error('Dispatch not found');
  const target = check.dispatch.target;
  if (target.client !== client || target.chat_id !== chatId) throw new Error('Dispatch targets another worker');
  const allowed = options.failed ? ['dispatch_failed'] : [];
  if (JSON.stringify(check.blockers) !== JSON.stringify(allowed)) {
    throw new Error('Workflow blocked: ' + check.blockers.join(', '));
  }
  const ticket = check.ticket;
  if (check.dispatch.kind === 'assign') {
    if (options.failed) throw new Error('Manager assignments use workflow_queue and workflow_assign, not worker inline retry');
    return { ready: true, role: 'manager', client, chat_id: chatId,
      dispatch_id: dispatchId, item_id: ticket.item_id, revision: ticket.revision,
      next: 'workflow_queue_then_workflow_assign', worker_instance_required: false };
  }
  if (!ticket.instance || !ticket.worker) throw new Error('Assigned worker instance required');
  const instance = await io.instanceInfo(root, ticket.instance);
  const owner = io.claimOwner(instance.worktree);
  if (owner && owner !== chatId) throw new Error('Assigned instance has a foreign claim');
  const git = (...args) => io.command('git', ['-C', instance.worktree, ...args]).trim();
  const head = git('rev-parse', 'HEAD');
  const dirty = Boolean(git('status', '--porcelain'));
  const kind = check.dispatch.kind;
  if (!owner && dirty && kind !== 'cancel') throw new Error('Unclaimed instance has work requiring review');
  if (['implement', 'integrate'].includes(kind) && owner !== chatId) {
    throw new Error('Worker must retain its exact instance claim');
  }
  let planHash = null;
  if (['implement', 'integrate'].includes(kind)) {
    planHash = await io.hashPlan(instance.worktree, ticket.plan.path);
    if (planHash !== ticket.plan.hash) throw new Error('Approved plan changed');
  }
  if (kind === 'integrate' && (head !== ticket.commit || dirty)) {
    throw new Error('Reviewed checkout or commit changed');
  }
  return { ready: true, client, chat_id: chatId, dispatch_id: dispatchId,
    item_id: ticket.item_id, revision: ticket.revision, checkout: instance.worktree,
    owner, head, dirty, plan_hash: planHash,
    primary_main: io.command('git', ['-C', root, 'rev-parse', 'main']).trim() };
}

export async function retryInline(root, itemId, dispatchId, client, dependencies = {}) {
  const io = { ...defaults, ...dependencies };
  const local = await workerPreflight(root, dispatchId, client, { failed: true }, io);
  if (local.item_id !== itemId) throw new Error('Ticket does not match the exact failed dispatch');
  // The database rechecks revision, pause, approvals, lease and competing retries
  // atomically after these filesystem checks. It creates an acknowledged child,
  // preserving the failed attempt and its original browser approval provenance.
  return io.control('host_retry_inline', { id: dispatchId, item_id: itemId, client,
    chat_id: local.chat_id, revision: local.revision, head: local.head,
    plan_hash: local.plan_hash });
}
