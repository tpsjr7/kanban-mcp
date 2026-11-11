import { spawnSync } from 'node:child_process';
import { mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';
import { configuration, connect, ensureService, serviceStatus, stopService } from './kanban/mcp-runtime.mjs';
import { withLifecycleLock } from './kanban/lifecycle-lock.mjs';
const adapter = process.env.KANBAN_ADAPTER ? await import(pathToFileURL(process.env.KANBAN_ADAPTER).href) : null;

export const image = process.env.KANBAN_MCP_IMAGE || 'kanban-mcp:local';
export const volume = process.env.KANBAN_MCP_VOLUME || 'kanban-data';
const runtime = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = process.env.KANBAN_WORKSPACE || runtime;
const web = process.env.KANBAN_CONTAINER || 'kanban-web';

function docker(args, options = {}) {
  const result = spawnSync('docker', args, {
    cwd: root, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr || `Docker exited ${result.status}`);
  return result.stdout;
}

export function containerArguments(command, boardVolume = volume, boardImage = image) {
  return ['run', '--rm', '-i', '--network', 'none', '--cap-drop', 'ALL',
    '--security-opt', 'no-new-privileges', '-v', `${boardVolume}:/data`, '-e', `KANBAN_PROJECT_DIR=${process.env.KANBAN_PROJECT_DIR || '/workspace/project'}`, boardImage, ...command];
}

function inspect() {
  const result = spawnSync('docker', ['container', 'inspect', web], { encoding: 'utf8' });
  if (result.status === 0) return JSON.parse(result.stdout)[0];
  if (result.stderr?.includes('No such container')) return null;
  throw new Error(result.stderr || 'Docker is unavailable');
}

export function assertOwner(container, workspace) {
  const owner = container?.Config?.Labels?.['ai.yolofi.kanban.owner'];
  if (container && owner !== workspace) {
    throw new Error(`Kanban is owned by ${owner || 'an unknown checkout'}; run lifecycle commands there.`);
  }
}

function compose(args, workspace) {
  docker(['compose', '-f', join(runtime, 'deploy/compose.yaml'), ...args], {
    env: { ...process.env, KANBAN_OWNER: workspace }, stdio: 'inherit',
  });
}

async function main(argv, locked = false) {
  const [action, argument] = argv;
  if (!action || action === '--help') {
    console.log('Usage: npm run kanban -- up|down|status|mcp|mcp-stop [--force]|backup|restore-check FILE|seed FILE');
    console.log('Workflow: manager|team|automation --help');
    return;
  }
  if (['manager', 'team', 'automation'].includes(action)) {
    const { workflowCommand } = await import('./kanban/commands.mjs');
    return workflowCommand(root, argv);
  }
  const expected = ['restore-check', 'seed'].includes(action) || (action === 'mcp-stop' && argument === '--force') ? 2 : 1;
  if (argv.length !== expected) throw new Error('Incorrect Kanban command arguments');
  const workspace = await realpath(root);
  const mcp = configuration(workspace);
  if (action === 'mcp') {
    const service = await ensureService(mcp);
    process.exitCode = await connect(mcp, service.id);
    return;
  }
  if (action === 'mcp-stop') {
    stopService(mcp, argument === '--force');
    console.log('Shared MCP service stopped; board data preserved.');
    return;
  }
  if (!['up', 'down', 'status', 'backup', 'restore-check', 'seed'].includes(action)) {
    throw new Error('Unknown Kanban action; use --help');
  }
  if (!locked && action !== 'status') {
    return withLifecycleLock(volume, () => main(argv, true));
  }
  const current = inspect();
  if (action === 'status') {
    console.log(JSON.stringify({
      running: current?.State?.Running ?? false, health: current?.State?.Health?.Status,
      owner: current?.Config?.Labels?.['ai.yolofi.kanban.owner'], volume,
      local: 'http://127.0.0.1:18120', public: 'https://kanban.flippyface.com',
      mcp: serviceStatus(mcp),
    }, null, 2));
    return;
  }
  assertOwner(current, workspace);
  if (action === 'up') {
    compose(['build', '--pull', 'web'], workspace);
    compose(['up', '-d', '--wait', '--wait-timeout', '90', 'web'], workspace);
    const response = await fetch('http://127.0.0.1:18120/api/items');
    if (!response.ok || !Array.isArray((await response.json()).items)) {
      throw new Error('Local board did not return its item list');
    }
    if (adapter) await adapter.publish();
    else console.log('Local board: http://127.0.0.1:18120');
    const { startConfigured } = await import('./kanban/commands.mjs');
    await startConfigured(root);
  } else if (action === 'down') {
    const { stopConfigured } = await import('./kanban/commands.mjs');
    await stopConfigured(root);
    try { await adapter?.stopPublication(); }
    catch (error) { if (!error.message.startsWith('SSH tunnel settings are missing')) throw error; }
    if (current) compose(['down'], workspace);
    console.log('Kanban web and tunnel stopped; database and MCP processes preserved.');
  } else if (action === 'backup') {
    const directory = join(root, 'data/backups/kanban');
    await mkdir(directory, { recursive: true, mode: 0o700 });
    const path = join(directory, `${new Date().toISOString().replaceAll(':', '-')}-${randomUUID()}.db`);
    const bytes = docker(containerArguments(['python', '/opt/kanban/operations.py', 'backup']), { encoding: null });
    await writeFile(path, bytes, { mode: 0o600, flag: 'wx' });
    console.log(path);
  } else if (action === 'restore-check') {
    if (!argument) throw new Error('Specify a backup database');
    const disposable = `music-gen-kanban-restore-${randomUUID()}`;
    docker(['volume', 'create', disposable]);
    try {
      console.log(docker(containerArguments(['python', '/opt/kanban/operations.py', 'restore-check'], disposable), {
        input: await readFile(resolve(argument)),
      }));
    } finally { docker(['volume', 'rm', disposable]); }
  } else if (action === 'seed') {
    if (!argument) throw new Error('Specify the reviewed backlog JSON file');
    console.log(docker(containerArguments(['python', '/opt/kanban/operations.py', 'seed']), {
      input: await readFile(resolve(argument)),
    }));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch(error => {
    console.error(`[kanban] ${error.message}`);
    process.exitCode = 1;
  });
}
