/** Host verification and same-turn requests. No chat messages or arbitrary commands. */
import { control } from './control.mjs';
import { verifyPlanRevision } from './evidence.mjs';


export async function armPlanRevision(root, requestId, inline = false, dependencies = {}) {
  const io = { control, verifyPlanRevision, ...dependencies };
  const request = io.control('plan_revision_get', { id: requestId });
  if (!request) throw Error('Plan revision request not found');
  if (request.state === 'rejected') throw Error(request.reason || 'Request rejected');
  if (['active', 'completed'].includes(request.state)) {
    return io.control('host_plan_revision_arm', { id: requestId, inline });
  }
  const evidence = await io.verifyPlanRevision(root, request);
  return io.control('host_plan_revision_arm', { id: requestId, inline, ...evidence });
}

export async function revisePlanInline(root, data, dependencies = {}) {
  const io = { control, env: process.env, ...dependencies };
  const expectedId = io.env[data.client === 'opencode' ? 'OPENCODE_SESSION_ID' : 'CODEX_THREAD_ID'];
  if (!expectedId || expectedId !== data.chat_id) throw Error('Exact current runtime identity required');
  const request = io.control('plan_revision_request', data);
  return armPlanRevision(root, request.id, true, io);
}

export function assertRevisionDelivery(check, state) {
  if (JSON.stringify(check.blockers) !== JSON.stringify(['dispatch_' + state])
      || !check.plan_revision_request) throw Error('Plan revision delivery is stale or blocked');
}
