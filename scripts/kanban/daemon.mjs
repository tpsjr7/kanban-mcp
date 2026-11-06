import { createHash } from 'node:crypto';
import { armPlanRevision, assertRevisionDelivery } from './plan-revision.mjs';
import { promptFor } from './prompts.mjs';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { persistentControl } from './control.mjs';
import { command, inspectIdentity, send } from './transport.mjs';
import { instanceInfo, claimOwner, hashPlan, verifyReport } from './evidence.mjs';

const root = process.env.KANBAN_CHECKOUT;
const state = process.env.KANBAN_AUTOMATION_STATE;
if (!root || !state) throw new Error('Start through npm run kanban automation start');
const rpc = persistentControl();
let stopped = false;
process.on('SIGTERM', () => { stopped = true; rpc.close(); });
process.on('SIGINT', () => { stopped = true; rpc.close(); });
const project = createHash('sha256').update(process.env.KANBAN_PROJECT_DIR || '/workspace/project').digest('hex').slice(0, 16);
const marker = process.env.KANBAN_DAEMON_TOKEN;
await writeFile(join(state, 'daemon.json'), JSON.stringify({ pid: process.pid, root, marker }), { mode: 0o600 });


while (!stopped) {
  try {
    const snapshot = await rpc.call('snapshot');
    const settings = snapshot.orchestration_settings[0];
    if (settings?.enabled && !settings.paused) {
      for (const request of await rpc.call('plan_revision_pending')) {
        try {
          await armPlanRevision(root, request.id);
        } catch (error) {
          await rpc.call('host_plan_revision_reject', { id: request.id, reason: error.message });
        }
      }
      const availability = [];
      for (const team of snapshot.teams) {
        try {
          const manifest = await instanceInfo(root, team.instance);
          const owner = claimOwner(manifest.worktree);
          const members = snapshot.team_workers.filter(w => w.team === team.name);
          const occupied = snapshot.tickets.some(r => r.team === team.name && r.phase !== 'closed');
          availability.push({ team: team.name, available: !owner && !occupied && members.length > 0,
            owner, workers: members.map(w => w.id) });
        } catch (error) { availability.push({ team: team.name, available: false, reason: error.message }); }
      }
      await rpc.call('host_capacity', { project, available: availability.filter(t => t.available).map(t => t.team) });
      for (const d of await rpc.call('poll')) {
        const current = await rpc.call('snapshot');
        const run = current.tickets.find(r => r.item_id === d.item_id);
        const team = current.teams.find(t => t.name === run?.team);
        try {
          if ((run?.cancel_pending && d.kind !== 'cancel') || run && run.revision !== d.revision) continue;
          const identity = JSON.parse(d.target);
          const chat = inspectIdentity(identity);
          if (!['idle', 'notLoaded'].includes(chat.status?.type)) throw new Error('Chat busy or awaiting attention');
          if (team) {
            const instance = await instanceInfo(root, team.instance);
            team.worktree = instance.worktree;
            const owner = claimOwner(instance.worktree);
            if (owner && owner !== identity.chat_id) throw new Error('Assigned instance has a foreign claim');
            if (d.kind === 'plan' && !owner && command('git', ['-C', instance.worktree, 'status', '--porcelain']).trim()) {
              throw new Error('Unclaimed instance has work requiring review');
            }
            if (d.kind === 'revise_plan') {
              const check = await rpc.call('workflow_check', { dispatch_id: d.id });
              assertRevisionDelivery(check, 'queued');
              if (owner !== identity.chat_id || (!check.plan_revision_request?.editing_started
                  && await hashPlan(instance.worktree, run.plan) !== run.plan_hash)) {
                throw Error('Plan revision owner/hash changed');
              }
            }
            if (d.kind === 'implement' && (run.plan_approved !== run.revision ||
                await hashPlan(instance.worktree, run.plan) !== run.plan_hash)) throw new Error('Plan approval is stale');
            if (d.kind === 'integrate') {
              if (command('git', ['-C', instance.worktree, 'status', '--porcelain']).trim() ||
                  await hashPlan(instance.worktree, run.plan) !== run.plan_hash) {
                throw new Error('Reviewed checkout or approved plan changed');
              }
              if (run.merge_approved !== run.revision || command('git', ['-C', instance.worktree, 'rev-parse', 'HEAD']).trim() !== run.commit_hash) {
                throw new Error('Reviewed commit or approval changed');
              }
              if (!(await rpc.call('host_lease', { item_id: d.item_id })).acquired) continue;
            }
          }
          const desktop = chat.route === 'native-mcp';
          if (desktop) await rpc.call('host_native_queue', { id: d.id });
          if (!(await rpc.call(desktop ? 'host_native_claim' : 'host_submitted', { id: d.id })).claimed) continue;
          try {
            const receipt = send(identity, promptFor(d, run, team, availability), d.id);
            if (desktop) await rpc.call('host_native_receipt', { id: d.id, thread_id: identity.chat_id });
            else await rpc.call('host_delivery', { id: d.id, state: 'delivered', receipt });
          } catch (error) {
            await rpc.call('host_delivery', { id: d.id, state: 'unknown', error: error.message });
          }
        } catch (error) {
          await rpc.call('host_block', { item_id: d.item_id, reason: error.message });
        }
      }
      for (const report of await rpc.call('reports')) {
        const current = await rpc.call('snapshot');
        const run = current.tickets.find(r => r.item_id === report.item_id);
        const team = current.teams.find(t => t.name === run?.team);
        const worker = current.team_workers.find(w => w.id === report.worker_id);
        try {
          if (!run || !team || !worker) throw new Error('Assignment missing');
          const evidence = await verifyReport(root, report, run, team, worker);
          await rpc.call('host_verify_report', { id: report.id, ...evidence });
        } catch (error) {
          await rpc.call('host_block', { item_id: report.item_id, reason: error.message });
        }
      }
    }
    await writeFile(join(state, 'heartbeat.json'), JSON.stringify({ time: Date.now(), root }), { mode: 0o600 });
  } catch (error) {
    console.error(new Date().toISOString(), error.message);
  }
  if (!stopped) await new Promise(done => setTimeout(done, 3000));
}
