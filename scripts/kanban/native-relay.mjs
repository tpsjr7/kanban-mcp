import { assertRevisionDelivery } from './plan-revision.mjs';
import { control } from './control.mjs';
import { promptFor } from './prompts.mjs';
import { instanceInfo, claimOwner, hashPlan } from './evidence.mjs';
import { command } from './transport.mjs';
import { verifiedDaemon } from './commands.mjs';

export async function nativeRelay(root, verb, id) {
  if (!await verifiedDaemon(root)) return { dispatches: [], stopped: true };
  const snapshot = control('snapshot');
  const settings = snapshot.orchestration_settings[0];
  if (!settings?.enabled || settings.paused) return { dispatches: [], paused: true };
  const available = [];
  for (const team of snapshot.teams) {
    try {
      const manifest = await instanceInfo(root, team.instance);
      const occupied = snapshot.tickets.some(run => run.team === team.name && run.phase !== 'closed');
      const workers = snapshot.team_workers.filter(worker => worker.team === team.name).map(worker => worker.id);
      available.push({ team: team.name, available: !occupied && !claimOwner(manifest.worktree) && workers.length > 0,
        workers });
    } catch (error) { available.push({ team: team.name, available: false, reason: error.message }); }
  }
  const pending = snapshot.dispatches.filter(d => ['native_pending', 'native_submitted'].includes(d.state));
  if (verb === 'native-list') return { dispatches: pending.map(d => ({
    id: d.id, state: d.state, target: JSON.parse(d.target), item_id: d.item_id, revision: d.revision,
  })) };
  const dispatch = pending.find(d => d.id === id);
  if (!dispatch || dispatch.state !== 'native_pending') throw new Error('Dispatch already submitted; inspect target history first');
  const run = snapshot.tickets.find(run => run.item_id === dispatch.item_id);
  if (!run || run.revision !== dispatch.revision || run.cancel_pending && dispatch.kind !== 'cancel') {
    throw new Error('Dispatch is stale or cancelled');
  }
  const team = snapshot.teams.find(team => team.name === run.team);
  const target = JSON.parse(dispatch.target);
  if (team) {
    const manifest = await instanceInfo(root, team.instance);
    team.worktree = manifest.worktree;
    const owner = claimOwner(manifest.worktree);
    if (owner && owner !== target.chat_id) throw new Error('Foreign instance claim');
    if (dispatch.kind === 'plan' && !owner &&
        command('git', ['-C', manifest.worktree, 'status', '--porcelain']).trim()) {
      throw new Error('Unclaimed instance has work requiring review');
    }
    if (dispatch.kind === 'revise_plan') {
      const check = control('workflow_check', { dispatch_id: dispatch.id });
      assertRevisionDelivery(check, 'native_pending');
      if (owner !== target.chat_id || (!check.plan_revision_request?.editing_started
          && await hashPlan(manifest.worktree, run.plan) !== run.plan_hash)) {
        throw Error('Plan revision owner/hash changed');
      }
    }
    if (dispatch.kind === 'implement' || dispatch.kind === 'integrate') {
      if (await hashPlan(manifest.worktree, run.plan) !== run.plan_hash) throw new Error('Plan changed');
      if (run.plan_approved !== run.revision) throw new Error('Plan approval missing');
    }
    if (dispatch.kind === 'integrate') {
      if (run.merge_approved !== run.revision ||
          command('git', ['-C', manifest.worktree, 'rev-parse', 'HEAD']).trim() !== run.commit_hash ||
          command('git', ['-C', manifest.worktree, 'status', '--porcelain']).trim()) {
        throw new Error('Reviewed commit or approval changed');
      }
      if (!control('host_lease', { item_id: dispatch.item_id }).acquired) throw new Error('Integration busy');
    }
  }
  const result = control('host_native_claim', { id });
  if (!result.claimed) throw new Error('Dispatch changed before claim');
  return { id, target, prompt: promptFor(dispatch, run, team, available) };
}
