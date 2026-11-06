import { spawn } from 'node:child_process';
import { mkdir, readFile, open, realpath, writeFile, lstat } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { control } from './control.mjs';
import { resolveIdentity, command } from './transport.mjs';
import { instanceInfo, claimOwner } from './evidence.mjs';

const daemon = fileURLToPath(new URL('./daemon.mjs', import.meta.url));
const state = process.env.KANBAN_AUTOMATION_STATE || join(homedir(), '.local/state/kanban-mcp/automation');
const help = `Kanban workflow commands:
  manager set --chat TITLE | --id ID; manager show; manager clear
  team add NAME --instance SLUG; team list; team show NAME
  team worker add TEAM --client codex --chat TITLE | --id ID
  team worker add TEAM --client opencode --session ID
  team worker remove TEAM --worker WORKER_ID; team remove NAME
  automation desktop-configure --thread ID --turn ID; automation reload
  automation start|status|pause|resume|stop; automation retry TICKET_ID
  automation revise-plan ITEM --client codex|opencode --revision N --hash HASH
    --message ID --request-file FILE --key KEY
  automation preflight DISPATCH_ID --client codex|opencode
  automation retry TICKET_ID --inline DISPATCH_ID --client codex|opencode
  automation native-list; automation native-claim DISPATCH_ID
  automation native-receipt DISPATCH_ID --thread ID
  automation native-failure DISPATCH_ID --thread ID --reason TEXT
All commands print JSON. Existing instance claims are never replaced.`;

function parse(args) {
  const words = [], flags = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--json') continue;
    if (args[i].startsWith('--')) {
      const key = args[i].slice(2);
      if (!args[i + 1] || args[i + 1].startsWith('--')) throw new Error('Missing --' + key + ' value');
      flags[key] = args[++i];
    } else words.push(args[i]);
  }
  return { words, flags };
}
export async function verifiedDaemon(root) {
  try {
    const record = JSON.parse(await readFile(join(state, 'daemon.json'), 'utf8'));
    const env = await readFile(`/proc/${record.pid}/environ`, 'utf8');
    const cmd = await readFile(`/proc/${record.pid}/cmdline`, 'utf8');
    if (record.root !== root || !env.split('\0').includes('KANBAN_DAEMON_TOKEN=' + record.marker) ||
        !cmd.split('\0').includes(daemon)) return null;
    return record;
  } catch { return null; }
}
export async function startConfigured(root) {
  if (!process.env.KANBAN_ADAPTER) return { running: false, reason: 'No automation adapter configured' };
  const snapshot = control('snapshot');
  const config = snapshot.orchestration_settings[0];
  if (!config?.enabled || config.paused) return { running: false, reason: 'Not enabled or paused' };
  const current = await verifiedDaemon(root);
  if (current) return { running: true, pid: current.pid };
  await mkdir(state, { recursive: true, mode: 0o700 });
  const log = await open(join(state, 'daemon.log'), 'a', 0o600);
  const child = spawn('flock', ['-n', join(state, 'daemon.lock'), process.execPath, daemon], {
    detached: true, stdio: ['ignore', log.fd, log.fd], cwd: root,
    env: { ...process.env, KANBAN_CHECKOUT: root, KANBAN_AUTOMATION_STATE: state,
      KANBAN_DAEMON_TOKEN: randomUUID() },
  });
  child.unref();
  await log.close();
  for (let attempt = 0; attempt < 20; attempt++) {
    await new Promise(done => setTimeout(done, 100));
    const running = await verifiedDaemon(root);
    if (running) return { running: true, pid: running.pid };
  }
  throw new Error('Automation did not start; inspect ' + join(state, 'daemon.log'));
}
export async function stopConfigured(root) {
  const record = await verifiedDaemon(root);
  if (record) process.kill(record.pid, 'SIGTERM');
  return { stopping: Boolean(record) };
}
async function withAvailability(root, snapshot) {
  for (const team of snapshot.teams) {
    try {
      const manifest = await instanceInfo(root, team.instance);
      team.claim_owner = claimOwner(manifest.worktree);
      team.available = !team.claim_owner && !snapshot.tickets.some(r => r.team === team.name && r.phase !== 'closed');
    } catch (error) { team.available = false; team.reason = error.message; }
  }
  return snapshot;
}
export async function workflowCommand(root, argv) {
  if (argv.includes('--help')) { console.log(help); return; }
  root = await realpath(root);
  const { words, flags } = parse(argv);
  const [area, verb, name] = words;
  let result;
  if (area === 'manager') {
    if (verb === 'set') {
      const identity = await resolveIdentity('codex', flags);
      control('setup');
      result = control('manager_set', { identity });
    } else if (verb === 'clear') result = control('manager_clear');
    else if (verb === 'show') result = control('snapshot').orchestration_settings;
  } else if (area === 'team') {
    if (verb === 'add') {
      if (!name || !flags.instance) throw new Error('Supply team name and --instance');
      await instanceInfo(root, flags.instance);
      result = control('team_add', { name, instance: flags.instance });
    } else if (verb === 'remove') result = control('team_remove', { name });
    else if (verb === 'list' || verb === 'show') {
      result = await withAvailability(root, control('snapshot'));
      if (verb === 'show') result.teams = result.teams.filter(team => team.name === name);
    } else if (verb === 'worker') {
      const team = words[3];
      if (name === 'add') {
        const identity = await resolveIdentity(flags.client, flags);
        result = control('worker_add', { team, identity });
      } else if (name === 'remove') result = control('worker_remove', { worker: flags.worker, team });
    }
  } else if (area === 'automation') {
    if (verb === 'desktop-configure') {
      if (!flags.thread || !flags.turn) throw new Error('Supply exact --thread and --turn runtime IDs');
      await mkdir(state, {recursive:true, mode:0o700});
      await writeFile(join(state,'desktop-caller.json'), JSON.stringify({thread_id:flags.thread,turn_id:flags.turn}), {mode:0o600});
      result = {configured:true, caller:flags.thread};
    } else if (verb === 'native-list' || verb === 'native-claim') {
      const { nativeRelay } = await import('./native-relay.mjs');
      result = await nativeRelay(root, verb, name);
    } else if (verb === 'native-receipt' || verb === 'native-failure') {
      if (!flags.thread) throw new Error('Supply the exact accepted --thread ID');
      if (verb === 'native-failure' && !flags.reason) throw new Error('Supply --reason');
      result = control('host_native_receipt', { id: name, thread_id: flags.thread,
        error: verb === 'native-failure' ? flags.reason : undefined });
    } else if (verb === 'reload') {
      await stopConfigured(root);
      for (let n=0; n<50 && await verifiedDaemon(root); n++) await new Promise(done=>setTimeout(done,100));
      result = await startConfigured(root);
    } else if (verb === 'start') {
      const settings = control('snapshot').orchestration_settings[0];
      if (!settings?.manager) throw new Error('Configure a manager first');
      if (!settings.enabled) control('arm');
      result = await startConfigured(root);
    } else if (verb === 'status') {
      result = { daemon: await verifiedDaemon(root), ...(await withAvailability(root, control('snapshot'))) };
    } else if (verb === 'pause' || verb === 'resume') {
      result = control(verb);
      if (verb === 'resume') await startConfigured(root);
    } else if (verb === 'stop') {
      control('stop');
      result = await stopConfigured(root);
    } else if (verb === 'release') {
      const snapshot = control('snapshot');
      const run = snapshot.tickets.find(r => r.item_id === Number(name));
      const team = snapshot.teams.find(t => t.name === run?.team);
      if (!run || run.phase !== 'stopped' || !team) throw new Error('Stop acknowledgment missing');
      const manifest = await instanceInfo(root, team.instance);
      if (claimOwner(manifest.worktree)) throw new Error('Matching worker must finish owner-scoped cleanup first');
      if (command('git', ['-C', manifest.worktree, 'status', '--porcelain']).trim()) {
        throw new Error('Preserve and resolve instance changes before release');
      }
      result = control('host_release', { item_id: Number(name) });
    } else if (verb === 'revise-plan') {
      const { revisePlanInline } = await import('./plan-revision.mjs');
      const client = flags.client;
      if (flags.request && flags['request-file']) throw Error('Choose --request or --request-file');
      let text = flags.request;
      if (flags['request-file']) {
        const file = await lstat(flags['request-file']);
        if (!file.isFile() || file.size > 64000) throw Error('Request file must be a small regular UTF-8 file');
        text = await readFile(flags['request-file'], 'utf8');
      }
      result = await revisePlanInline(root, {
        item_id: Number(name), client,
        chat_id: process.env[client === 'opencode' ? 'OPENCODE_SESSION_ID' : 'CODEX_THREAD_ID'],
        expected_revision: Number(flags.revision), expected_hash: flags.hash,
        message_id: flags.message, request_text: text, request_key: flags.key,
      });
    } else if (verb === 'preflight') {
      const { workerPreflight } = await import('./worker-start.mjs');
      result = await workerPreflight(root, name, flags.client);
    } else if (verb === 'retry') {
      if (flags.inline) {
        const { retryInline } = await import('./worker-start.mjs');
        result = await retryInline(root, Number(name), flags.inline, flags.client);
      } else result = control('host_retry', { item_id: Number(name) });
    }
  }
  if (result === undefined) throw new Error(help);
  console.log(JSON.stringify(result, null, 2));
}
