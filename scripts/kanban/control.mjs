import { spawnSync, spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

export function argumentsForControl() {
  return ['run', '--rm', '-i', '--network', 'none', '--cap-drop', 'ALL',
    '--security-opt', 'no-new-privileges',
    '-v', `${process.env.KANBAN_MCP_VOLUME || 'kanban-data'}:/data`,
    '-e', `KANBAN_PROJECT_DIR=${process.env.KANBAN_PROJECT_DIR || '/workspace/project'}`,
    process.env.KANBAN_MCP_IMAGE || 'kanban-mcp:local',
    'python', '/opt/kanban/team_control.py'];
}

export function control(action, data = {}) {
  const result = spawnSync('docker', argumentsForControl(), {
    input: JSON.stringify({ action, data }), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stdout || result.stderr);
  const value = JSON.parse(result.stdout);
  if (value.error) throw new Error(value.error);
  return value;
}

export function persistentControl() {
  const child = spawn('docker', [...argumentsForControl(), '--serve'], { stdio: ['pipe', 'pipe', 'inherit'] });
  const pending = [];
  let failure;
  const fail = error => {
    failure = error;
    for (const next of pending.splice(0)) next.reject(error);
  };
  child.on('error', fail);
  child.stdin.on('error', fail);
  createInterface({ input: child.stdout }).on('line', line => {
    const next = pending.shift();
    if (!next) return;
    try {
      const value = JSON.parse(line);
      if (value.error) next.reject(new Error(value.error));
      else next.resolve(value.result);
    } catch (error) { next.reject(error); }
  });
  child.on('exit', () => fail(new Error('Board helper exited')));
  return {
    call: (action, data = {}) => new Promise((resolve, reject) => {
      if (failure) { reject(failure); return; }
      pending.push({ resolve, reject });
      child.stdin.write(JSON.stringify({ action, data }) + '\n');
    }),
    close: () => child.stdin.end(),
  };
}
