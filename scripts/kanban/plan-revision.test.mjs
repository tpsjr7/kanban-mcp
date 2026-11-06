import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyHumanMessage } from './message-authority.mjs';
import { revisePlanInline, armPlanRevision, assertRevisionDelivery } from './plan-revision.mjs';

const request = { id: 'request-1', item_id: 7, client: 'codex', chat_id: 'thread-exact',
  message_id: 'message-exact', request_text: 'Remove helper text. Preserve Unicode: café 🎵.',
  request_key: 'key', expected_revision: 2, expected_hash: 'exact-plan' };
const message = { id: request.message_id, type: 'userMessage',
  content: [{ type: 'text', text: request.request_text }] };
function desktop(row = message, id = request.chat_id) {
  return { desktopOwnsChat: () => true, command: () => JSON.stringify({ id, messages: [row] }) };
}
test('desktop checks exact user message, complete Unicode text and target', () => {
  const receipt = verifyHumanMessage(request, desktop());
  assert.equal(receipt.message_id, request.message_id);
  assert.equal(receipt.role, 'user');
  assert.equal(receipt.route, 'codex-desktop');
  assert.match(receipt.sha256, /^[a-f0-9]{64}$/);
  for (const row of [{ ...message, type: 'agentMessage' }, { ...message, id: 'other' },
    { ...message, synthetic: true }, { ...message, text: 'truncated' },
    { ...message, text: '<codex_delegation>request</codex_delegation>' }]) {
    assert.throws(() => verifyHumanMessage(request, desktop(row)), /human user/);
  }
  assert.throws(() => verifyHumanMessage(request, desktop(message, 'wrong')), /target mismatch/);
});
test('CLI inspects the exact thread and retains message IDs', () => {
  let args;
  const receipt = verifyHumanMessage(request, {
    desktopOwnsChat: () => false, codexHasWriter: () => true,
    command: (_, supplied) => {
      args = supplied;
      return JSON.stringify({ id: request.chat_id, turns: [{ messages: [
        { id: request.message_id, type: 'userMessage', text: request.request_text },
      ] }] });
    },
  });
  assert.equal(receipt.route, 'codex-cli');
  assert.equal(args[2], request.chat_id);
  assert.ok(args.includes('16001'));
});
test('OpenCode reads exact opaque session/message endpoints and rejects assistant text', () => {
  const input = { ...request, client: 'opencode', chat_id: 'ses_non_uuid' };
  const calls = [];
  const io = { opencode: (method, path) => {
    calls.push([method, path]);
    return { data: path.endsWith('/message/' + input.message_id)
      ? { id: input.message_id, type: 'user', text: input.request_text }
      : { id: input.chat_id } };
  } };
  assert.equal(verifyHumanMessage(input, io).route, 'opencode');
  assert.deepEqual(calls, [['GET', '/api/session/ses_non_uuid'],
    ['GET', '/api/session/ses_non_uuid/message/message-exact']]);
  assert.throws(() => verifyHumanMessage(input, { opencode: (_, path) => ({ data:
    path.includes('/message/') ? { id: input.message_id, type: 'assistant', text: input.request_text }
      : { id: input.chat_id } }) }), /human user/);
});
test('both clients get inline checkpoints without sending a message or replacing identity', async () => {
  for (const client of ['codex', 'opencode']) {
    const data = { ...request, client, chat_id: client === 'opencode' ? 'ses_exact' : request.chat_id };
    const calls = [];
    const io = {
      env: { [client === 'codex' ? 'CODEX_THREAD_ID' : 'OPENCODE_SESSION_ID']: data.chat_id },
      control: (action, payload) => {
        calls.push([action, payload]);
        if (action === 'plan_revision_request') return { id: data.id };
        if (action === 'plan_revision_get') return { ...data, state: 'pending' };
        return { checkpoint: { current: true } };
      },
      verifyPlanRevision: async () => ({ owner: data.chat_id, plan_hash: data.expected_hash,
        receipt: { verified: true } }),
    };
    assert.equal((await revisePlanInline('/fixture', data, io)).checkpoint.current, true);
    assert.deepEqual(calls.map(call => call[0]),
      ['plan_revision_request', 'plan_revision_get', 'host_plan_revision_arm']);
    assert.equal(calls[2][1].inline, true);
    await assert.rejects(revisePlanInline('/fixture', data, { ...io, env: {} }), /runtime identity/);
  }
});
test('lost arm receipts return the existing checkpoint without rearming or rereading edited plan', async () => {
  const calls = [];
  const result = await armPlanRevision('/fixture', 'request', true, {
    control: (action) => { calls.push(action); return action === 'plan_revision_get'
      ? { id: 'request', state: 'active' } : { duplicate: true }; },
    verifyPlanRevision: () => { throw Error('must not reread edited plan'); },
  });
  assert.equal(result.duplicate, true);
  assert.deepEqual(calls, ['plan_revision_get', 'host_plan_revision_arm']);
});

test('an old user message cannot authorize editing a newer saved plan', () => {
  const input = { ...request, plan_saved_at: '2026-11-06 20:00:00' };
  assert.throws(() => verifyHumanMessage(input, desktop({ ...message,
    source_time: Date.parse('2026-11-06T19:59:59Z') / 1000 })), /predates/);
  assert.throws(() => verifyHumanMessage(input, desktop(message)), /no verified time/);
  assert.equal(verifyHumanMessage(input, desktop({ ...message,
    source_time: Date.parse('2026-11-06T20:00:01Z') / 1000 })).role, 'user');
});

test('delivery permits only the expected queued blocker, retaining all approval/pause gates', () => {
  for (const state of ['queued', 'native_pending']) {
    const check = { blockers: ['dispatch_' + state], plan_revision_request: { id: 'verified' } };
    assert.doesNotThrow(() => assertRevisionDelivery(check, state));
    assert.throws(() => assertRevisionDelivery({ ...check, blockers: [...check.blockers, 'stale_revision'] }, state), /blocked/);
    assert.throws(() => assertRevisionDelivery({ ...check, plan_revision_request: null }, state), /blocked/);
  }
});
