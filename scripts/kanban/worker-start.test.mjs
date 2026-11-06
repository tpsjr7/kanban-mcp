import test from 'node:test';
import assert from 'node:assert/strict';
import { workerPreflight, retryInline } from './worker-start.mjs';

function fixture(client = 'codex') {
  const id = client === 'codex' ? 'exact-thread' : 'ses_exact_session';
  const check = { found: true, blockers: [], dispatch: {
    kind: 'implement', target: { client, chat_id: id } }, ticket: {
    item_id: 20, instance: 'local-d', worker: `${client}:${id}`, revision: 2,
    plan: { path: '/assigned/plan.md', hash: 'hash' }, commit: 'head' } };
  const calls = [];
  const io = {
    env: { CODEX_THREAD_ID: id, OPENCODE_SESSION_ID: id },
    control: (action, data) => {
      calls.push({ action, data });
      return action === 'workflow_check' ? check : { resumed: true, checkpoint: { current: true } };
    },
    instanceInfo: async () => ({ worktree: '/assigned' }),
    claimOwner: () => id,
    hashPlan: async () => 'hash',
    command: (name, args) => args.includes('status') ? '' : 'head',
  };
  return { id, check, calls, io };
}

test('one host preflight batches identity, claim, Git and plan for both clients', async () => {
  for (const client of ['codex', 'opencode']) {
    const { io, id, calls } = fixture(client);
    const result = await workerPreflight('/root', 'dispatch', client, {}, io);
    assert.equal(result.ready, true);
    assert.equal(result.chat_id, id);
    assert.equal(result.checkout, '/assigned');
    assert.equal(result.plan_hash, 'hash');
    assert.equal(calls.length, 1);
  }
});

test('manager preflight needs identity and current dispatch but never a worker instance', async () => {
  for (const client of ['codex', 'opencode']) {
    const { io, check } = fixture(client);
    check.dispatch.kind = 'assign';
    check.ticket.instance = null;
    check.ticket.worker = null;
    const forbidden = () => { throw new Error('Manager must not inspect a worker checkout'); };
    const managerIO = { ...io, instanceInfo: forbidden, claimOwner: forbidden,
      command: forbidden, hashPlan: forbidden };
    const result = await workerPreflight('/root', 'assign-id', client, {}, managerIO);
    assert.equal(result.ready, true);
    assert.equal(result.role, 'manager');
    assert.equal(result.worker_instance_required, false);
    assert.equal(result.next, 'workflow_queue_then_workflow_assign');
    check.blockers = ['automation_disabled_or_paused'];
    await assert.rejects(workerPreflight('/root', 'assign-id', client, {}, managerIO), /blocked/);
  }
});

test('preflight rejects missing identity, foreign claims, changed plans and reviewed commits', async () => {
  const { io, check } = fixture();
  await assert.rejects(workerPreflight('/root', 'd', 'codex', {}, { ...io, env: {} }), /missing/);
  await assert.rejects(workerPreflight('/root', 'd', 'codex', {}, {
    ...io, env: { CODEX_THREAD_ID: 'foreign' } }), /another worker/);
  await assert.rejects(workerPreflight('/root', 'd', 'codex', {}, {
    ...io, claimOwner: () => 'foreign' }), /foreign claim/);
  await assert.rejects(workerPreflight('/root', 'd', 'codex', {}, {
    ...io, hashPlan: async () => 'changed' }), /plan changed/);
  check.dispatch.kind = 'integrate';
  check.ticket.commit = 'changed';
  await assert.rejects(workerPreflight('/root', 'd', 'codex', {}, io), /Reviewed checkout/);
});

test('inline retry uses runtime identity and one private atomic acknowledgment without messaging', async () => {
  for (const client of ['codex', 'opencode']) {
    const { io, check, calls, id } = fixture(client);
    check.blockers = ['dispatch_failed'];
    const result = await retryInline('/root', 20, 'failed-id', client, io);
    assert.equal(result.resumed, true);
    assert.deepEqual(calls.map(c => c.action), ['workflow_check', 'host_retry_inline']);
    assert.equal(calls[1].data.chat_id, id);
    assert.equal(calls[1].data.id, 'failed-id');
    assert.equal(calls[1].data.plan_hash, 'hash');
    check.blockers.push('stale_revision');
    await assert.rejects(retryInline('/root', 20, 'failed-id', client, io), /blocked/);
  }
});
