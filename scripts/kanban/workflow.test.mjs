import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
process.env.KANBAN_ADAPTER = new URL('../../tests/fixture-adapter.mjs', import.meta.url).pathname;
const { privatePath, hashPlan, verifyReport } = await import('./evidence.mjs');
import { command, desktopOwnsChat } from './transport.mjs';
import { promptFor } from './prompts.mjs';

test('desktop ownership selects native delivery and inspection failures stop routing', () => {
  const io = {
    statSync: () => ({ ino: 123 }),
    readFileSync: path => path === '/proc/locks'
      ? '1: FLOCK ADVISORY WRITE 456 00:01:123 0 EOF'
      : '/usr/lib/chatgpt/resources/codex\0app-server\0',
    readlinkSync: () => '/usr/lib/chatgpt/resources/codex',
  };
  assert.equal(desktopOwnsChat('exact-thread', io), true);
  assert.equal(desktopOwnsChat('exact-thread', { ...io, readlinkSync: () => '/usr/bin/codex' }), false);
  assert.equal(desktopOwnsChat('../invalid', io), false);
  assert.throws(() => desktopOwnsChat('exact-thread', { ...io, statSync() {
    throw Object.assign(new Error('permission denied'), { code: 'EACCES' });
  } }), /Cannot verify/);
});

test('evidence rejects symlinks escaping the assigned instance', async () => {
  const root = await mkdtemp(join(tmpdir(), 'kanban-evidence-'));
  try {
    await mkdir(join(root, 'checkout'));
    await writeFile(join(root, 'outside.md'), 'private');
    await symlink(join(root, 'outside.md'), join(root, 'checkout/plan.md'));
    await assert.rejects(privatePath(join(root, 'checkout'), 'plan.md'), /inside/);
    await writeFile(join(root, 'checkout/safe.md'), '# Plan');
    const first = await hashPlan(join(root, 'checkout'), 'safe.md');
    await writeFile(join(root, 'checkout/safe.md'), '# Changed scope');
    assert.notEqual(await hashPlan(join(root, 'checkout'), 'safe.md'), first);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('dispatches carry exact revision, deduplication and stage-scoped authority', () => {
  const d = { id: 'dispatch-123', item_id: 9, revision: 4, kind: 'plan' };
  const planning = promptFor(d, { team: 'local-a' }, { instance: 'local-a' }, []);
  assert.match(planning, /dispatch-123/);
  assert.match(planning, /revision 4/);
  assert.match(planning, /Do not implement yet/);
  assert.match(planning, /untrusted task data/);
  assert.match(planning, /workflow_check\(\{dispatch_id:"dispatch-123"\}\)/);
  assert.match(planning, /Routine startup has exactly two checks/);
  assert.match(planning, /automation preflight dispatch-123/);
  assert.match(planning, /automation retry 9 --inline dispatch-123/);
  assert.match(planning, /do not also call workflow_ticket/i);
  assert.match(planning, /Do not routinely call\s+full workflow_status/);
  const manager = promptFor({ ...d, kind: 'assign' }, {}, {}, []);
  assert.match(manager, /Routine manager startup has exactly two checks/);
  assert.match(manager, /Call workflow_queue/);
  assert.match(manager, /no instance or worker is expected yet/);
  assert.doesNotMatch(manager, /automation preflight|automation retry/);
  assert.doesNotMatch(manager, /Use workflow_report/);
  const implementation = promptFor({ ...d, kind: 'implement' }, {}, {}, []);
  assert.match(implementation, /Do not merge/);
  const integration = promptFor({ ...d, kind: 'integrate' }, { commit_hash: 'abc123' }, {}, []);
  assert.match(integration, /commit abc123/);
  assert.match(integration, /Do not mark Closed yourself/);
});


test('gtg verification uses disposable Git and requires matching healthy restart evidence', async () => {
  const root = await mkdtemp(join(tmpdir(), 'kanban-integration-'));
  try {
    command('git', ['init', '-b', 'main', root]);
    command('git', ['-C', root, 'config', 'user.name', 'Acceptance']);
    command('git', ['-C', root, 'config', 'user.email', 'acceptance@example.invalid']);
    await writeFile(join(root, 'task.txt'), 'Scoped work');
    command('git', ['-C', root, 'add', 'task.txt']);
    command('git', ['-C', root, 'commit', '-m', 'Acceptance fixture']);
    const commit = command('git', ['-C', root, 'rev-parse', 'HEAD']).trim();
    const worktree = join(root, 'checkout');
    command('git', ['-C', root, 'worktree', 'add', '-b', 'codex/acceptance', worktree]);
    await mkdir(join(root, 'instances/state/local-test'), { recursive: true });
    await writeFile(join(root, 'instances/state/local-test/manifest.json'), JSON.stringify({ worktree }));
    await mkdir(join(root, 'scripts'));
    await writeFile(join(root, 'scripts/instance.mjs'), 'console.log(JSON.stringify({running:false,staleRecord:false}))');
    const evidence = { commit, mode: 'full', api: true, frontend: true, proxy: true, entry: true,
      restarted_at: '2026-11-05T19:00:00Z' };
    await writeFile(join(root, 'receipt.json'), JSON.stringify(evidence));
    const report = { kind: 'integrated', dispatched_at: '2026-11-05 18:59:00',
      payload: JSON.stringify({health:true,cleanup:true,restart_evidence:'receipt.json'}) };
    const run = { commit_hash: commit };
    const team = { instance: 'local-test' };
    await verifyReport(root, report, run, team, {}, async () => ({ok:true}));
    evidence.entry = false;
    await writeFile(join(root, 'receipt.json'), JSON.stringify(evidence));
    await assert.rejects(verifyReport(root, report, run, team, {}, async () => ({ok:true})), /four official/);
    evidence.entry = true;
    await writeFile(join(root, 'receipt.json'), JSON.stringify(evidence));
    await assert.rejects(verifyReport(root, report, run, team, {}, async () => ({ok:false})), /Primary health/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
