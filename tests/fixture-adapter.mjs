import { readFile, realpath } from 'node:fs/promises';
import { join, resolve, relative, isAbsolute } from 'node:path';
import { createHash } from 'node:crypto';
import { command } from '../scripts/kanban/transport.mjs';

export async function instanceInfo(root, slug) {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(slug)) throw new Error('Invalid instance slug');
  return JSON.parse(await readFile(join(root, 'instances/state', slug, 'manifest.json'), 'utf8'));
}
export function claimOwner(worktree) {
  const rows = command('git', ['-C', worktree, 'worktree', 'list', '--porcelain']);
  const entry = rows.split('\n\n').find(row => row.startsWith('worktree ' + worktree + '\n'));
  const lock = entry?.split('\n').find(row => row.startsWith('locked'));
  if (!lock) return null;
  let value = lock.slice(7);
  if (value.startsWith('"')) value = JSON.parse(value);
  if (!value.startsWith('music-gen-instance-owner-v1:')) throw new Error('Unrecognized foreign claim');
  return JSON.parse(value.slice('music-gen-instance-owner-v1:'.length)).owner;
}
export async function privatePath(worktree, filename) {
  const full = await realpath(resolve(worktree, filename));
  const inside = relative(await realpath(worktree), full);
  if (inside.startsWith('..') || isAbsolute(inside)) throw new Error('Evidence must be inside assigned checkout');
  return full;
}
export async function hashPlan(worktree, plan) {
  const path = await privatePath(worktree, plan);
  if (!path.endsWith('.md')) throw new Error('Plan must be a Markdown file');
  return createHash('sha256').update(await readFile(path)).digest('hex');
}
export async function verifyReport(root, report, run, team, worker, fetchImpl = fetch) {
  const manifest = await instanceInfo(root, team.instance);
  const payload = JSON.parse(report.payload);
  const owner = claimOwner(manifest.worktree);
  if (!['integrated', 'stopped', 'failed'].includes(report.kind) && owner !== worker.chat_id) {
    throw new Error('Worker must claim its assigned instance before reporting');
  }
  if (report.kind === 'plan_ready') {
    const planPath = await privatePath(manifest.worktree, payload.plan);
    const text = await readFile(planPath, 'utf8');
    if (text.length > 262144) throw new Error('Plan exceeds 256 KiB');
    return { plan_hash: await hashPlan(manifest.worktree, payload.plan),
      plan_text: text, plan_path: planPath };
  }
  if (report.kind === 'preview_ready') {
    const head = command('git', ['-C', manifest.worktree, 'rev-parse', 'HEAD']).trim();
    if (payload.commit !== head || !/^[a-f0-9]{40}$/.test(head)) throw new Error('Preview must match exact task commit');
    if (command('git', ['-C', manifest.worktree, 'status', '--porcelain']).trim()) throw new Error('Preview checkout must be clean');
    if (!Array.isArray(payload.checks) || !payload.checks.length || payload.checks.some(c => c.result !== 'passed')) {
      throw new Error('Required check results are missing');
    }
    for (const check of payload.checks) await privatePath(manifest.worktree, check.evidence);
    const url = command('node', [join(root, 'scripts/instance.mjs'), 'url', team.instance]).trim();
    if (url !== payload.preview || !url.startsWith('https://')) throw new Error('Use the verified published instance URL');
    const response = await fetchImpl(url, { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error('Published preview is not healthy');
    if (await hashPlan(manifest.worktree, run.plan) !== run.plan_hash) throw new Error('Approved plan changed');
  }
  if (report.kind === 'integrated') {
    if (owner !== null) throw new Error('Instance claim must be released after successful cleanup');
    const lifecycle = JSON.parse(command('node', [join(root, 'scripts/instance.mjs'), 'status', team.instance]));
    if (lifecycle.running !== false || lifecycle.staleRecord) throw new Error('Instance must be stopped cleanly');
    command('git', ['-C', root, 'merge-base', '--is-ancestor', run.commit_hash, 'main']);
    const main = command('git', ['-C', root, 'rev-parse', 'main']).trim();
    if (command('git', ['-C', manifest.worktree, 'rev-parse', 'HEAD']).trim() !== main) {
      throw new Error('Named instance must be synchronized with main');
    }
    if (command('git', ['-C', manifest.worktree, 'status', '--porcelain']).trim()) throw new Error('Instance cleanup incomplete');
    if (payload.health !== true || payload.cleanup !== true || !payload.restart_evidence) throw new Error('gtg evidence missing');
    const receiptPath = await privatePath(root, payload.restart_evidence);
    const receipt = JSON.parse(await readFile(receiptPath, 'utf8'));
    if (receipt.commit !== main || !['frontend', 'full'].includes(receipt.mode) ||
        !['api', 'frontend', 'proxy', 'entry'].every(probe => receipt[probe] === true) ||
        !Number.isFinite(Date.parse(receipt.restarted_at))) {
      throw new Error('Restart receipt must match main and all four official health probes');
    }
    if (report.dispatched_at && Date.parse(receipt.restarted_at) < Date.parse(report.dispatched_at + 'Z')) {
      throw new Error('Restart receipt predates integration approval');
    }
    const response = await fetchImpl('https://example.invalid', { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error('Primary health verification failed');
  }
  return {};
}

// Disposable authority fixture only. Production adapters must read the exact
// client-owned human message; these receipts do not validate a real user.
export async function verifyPlanRevision(root, request) {
  const { control } = await import('../scripts/kanban/control.mjs');
  const ticket = control('workflow_ticket', { item_id: request.item_id });
  const instance = await instanceInfo(root, ticket.instance);
  const owner = claimOwner(instance.worktree);
  const plan_hash = await hashPlan(instance.worktree, ticket.plan.path);
  if (owner !== request.chat_id || plan_hash !== request.expected_hash) {
    throw Error('Fixture owner/hash mismatch');
  }
  return { owner, plan_hash, receipt: {
    client: request.client, chat_id: request.chat_id, message_id: request.message_id,
    role: 'user', source_time: Date.now() / 1000, sha256: createHash('sha256').update(request.request_text).digest('hex'),
    verified_at: new Date().toISOString(), route: 'fake-adapter-acceptance',
  } };
}
