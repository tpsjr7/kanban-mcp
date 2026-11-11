/** One network-isolated MCP service per board; portable stdio exec connections. */
import { spawn, spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const label = 'ai.yolofi.kanban.';
const moduleName = 'kanban_mcp.shared_runtime';
const maxLine = 4 * 1024 * 1024;

export function configuration(workspace, env = process.env) {
  const volume = env.KANBAN_MCP_VOLUME || 'kanban-data';
  return {
    owner: resolve(workspace), volume, image: env.KANBAN_MCP_IMAGE || 'kanban-mcp:local',
    project: env.KANBAN_PROJECT_DIR || '/workspace/project',
    projectName: env.KANBAN_PROJECT_NAME || 'project',
    name: `kanban-mcp-${createHash('sha256').update(volume).digest('hex').slice(0, 16)}`,
  };
}

export function docker(args, options = {}) {
  const result = spawnSync('docker', args, {
    encoding: 'utf8', timeout: 15000, maxBuffer: 20 * 1024 * 1024, ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(String(result.stderr || '').trim() || `Docker command failed (${result.status})`);
  return result.stdout;
}

export function inspect(name) {
  const result = spawnSync('docker', ['container', 'inspect', name], {
    encoding: 'utf8', timeout: 15000,
  });
  if (result.error) throw result.error;
  if (result.status === 0) return JSON.parse(result.stdout)[0];
  if (/No such (object|container)/i.test(result.stderr)) return null;
  throw new Error(result.stderr || 'Docker is unavailable');
}

export function assertCompatible(container, config, imageID) {
  const labels = container.Config.Labels || {};
  const expected = {
    role: 'mcp-service', protocol: '1', owner: config.owner,
    project: config.project, volume: config.volume,
  };
  for (const [key, value] of Object.entries(expected)) {
    if (labels[label + key] !== value) {
      throw new Error(`MCP service ${config.name} has incompatible ${key}; inspect it before replacement`);
    }
  }
  const mount = container.Mounts.find(value => value.Destination === '/data');
  const command = ['python', '-m', moduleName, 'serve'];
  const security = container.HostConfig.SecurityOpt || [];
  const capabilities = container.HostConfig.CapDrop || [];
  const environment = new Map((container.Config.Env || []).map(value => {
    const index = value.indexOf('=');
    return [value.slice(0, index), value.slice(index + 1)];
  }));
  if (container.Image !== imageID || mount?.Name !== config.volume || !mount?.RW
      || container.HostConfig.NetworkMode !== 'none'
      || container.Config.User !== '10001:10001'
      || environment.get('KANBAN_PROJECT_DIR') !== config.project
      || environment.get('KANBAN_SQLITE_PATH') !== '/data/kanban.db'
      || environment.get('KANBAN_BACKEND') !== 'sqlite'
      || !security.includes('no-new-privileges')
      || !capabilities.some(value => value.toUpperCase() === 'ALL')
      || JSON.stringify(container.Config.Cmd) !== JSON.stringify(command)) {
    throw new Error(`MCP service ${config.name} has incompatible image, storage or security settings`);
  }
}

function imageID(config) {
  return docker(['image', 'inspect', config.image, '--format', '{{.Id}}']).trim();
}

export function control(config, action = 'status', force = false, target = config.name) {
  const args = ['exec', target, 'python', '-m', moduleName, action];
  if (force) args.push('--force');
  const result = JSON.parse(docker(args));
  if (result.protocol !== 1 || result.project !== config.project) {
    throw new Error('MCP service returned an incompatible status');
  }
  return result;
}

export async function ensureService(config) {
  const id = imageID(config);
  const imageInfo = JSON.parse(docker(['image', 'inspect', id]))[0];
  if (imageInfo.Config.Labels?.[label + 'mcp-protocol'] !== '1') {
    throw new Error('Build the current Kanban Docker image before connecting MCP; shared runtime support is missing');
  }
  let current = inspect(config.name);
  if (!current) {
    // Docker creation is the cross-process/cross-platform compare-and-create.
    const labels = {
      role: 'mcp-service', protocol: '1', owner: config.owner,
      volume: config.volume, project: config.project,
    };
    const args = ['create', '--name', config.name, '--init', '--network', 'none',
      '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
      '--restart', 'unless-stopped', '-v', `${config.volume}:/data`,
      '-e', `KANBAN_PROJECT_DIR=${config.project}`,
      '-e', `KANBAN_PROJECT_NAME=${config.projectName}`];
    for (const [key, value] of Object.entries(labels)) args.push('--label', label + key + '=' + value);
    args.push(config.image, 'python', '-m', moduleName, 'serve');
    try { docker(args); }
    catch (error) {
      if (!error.message.includes('already in use')) throw error;
      // Docker can reserve the name before inspect can see the new object.
      const deadline = Date.now() + 5000;
      do {
        current = inspect(config.name);
        if (!current) await delay(100);
      } while (!current && Date.now() < deadline);
      if (!current) throw error;
    }
    current ??= inspect(config.name);
  }
  assertCompatible(current, config, id);
  if (!current.State.Running) docker(['start', current.Id]);
  let failure;
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const status = control(config, 'status', false, current.Id);
      if (status.draining) throw new Error('MCP service is draining; stop and reconnect it');
      return { ...status, id: current.Id };
    } catch (error) {
      failure = error;
      if (error.message.includes('is draining')) throw error;
      if (!inspect(config.name)?.State.Running) break;
      await delay(250);
    }
  }
  throw new Error(`MCP service is not ready: ${failure?.message}`);
}

export function legacyContainers(config) {
  const ids = docker(['ps', '-q', '--filter', `volume=${config.volume}`]).trim().split(/\s+/).filter(Boolean);
  if (!ids.length) return [];
  return JSON.parse(docker(['inspect', ...ids])).filter(container =>
    JSON.stringify(container.Config.Cmd) === JSON.stringify(['kanban-mcp'])
    && container.Mounts.some(mount => mount.Name === config.volume && mount.Destination === '/data')
  ).map(container => ({ id: container.Id, name: container.Name.slice(1),
    started: container.State.StartedAt, ownership: 'legacy-untracked' }));
}

export function serviceStatus(config) {
  const current = inspect(config.name);
  if (!current) return { name: config.name, running: false, legacy: legacyContainers(config) };
  assertCompatible(current, config, imageID(config));
  return {
    name: config.name, running: current.State.Running, image: current.Image,
    legacy: legacyContainers(config),
    ...(current.State.Running ? control(config, 'status', false, current.Id) : {}),
  };
}

export function stopService(config, force = false) {
  const current = inspect(config.name);
  if (!current) return;
  assertCompatible(current, config, imageID(config));
  if (current.State.Running) {
    let status;
    try { status = control(config, 'drain', force, current.Id); }
    catch (error) {
      if (!force) throw new Error(`Cannot verify MCP connections: ${error.message}; inspect before using --force`);
    }
    if (status && !status.draining) {
      throw new Error(`MCP has ${status.connections} connected clients; reconnect/close them or explicitly use mcp-stop --force`);
    }
    docker(['stop', '--time', '10', current.Id]);
  }
  docker(['rm', current.Id]);
}

export function connect(config, serviceID) {
  return new Promise((resolve, reject) => {
    const session = randomUUID();
    const parent = process.ppid;
    const child = spawn('docker', ['exec', '-i', serviceID, 'python', '-m',
      moduleName, 'relay', '--session', session], { stdio: ['pipe', 'inherit', 'inherit'] });
    let ending = false;
    let killTimer;
    let input = '';
    const send = value => child.stdin.write(JSON.stringify(value) + '\n');
    const finish = () => {
      if (ending) return;
      ending = true;
      clearInterval(lease);
      child.stdin.end();
      process.stdin.pause();
      killTimer = setTimeout(() => child.kill('SIGKILL'), 5000);
      killTimer.unref();
    };
    const onData = chunk => {
      input += chunk;
      let index;
      while ((index = input.indexOf('\n')) !== -1) {
        const line = input.slice(0, index);
        input = input.slice(index + 1);
        if (Buffer.byteLength(line) > maxLine) {
          console.error('[kanban] MCP request exceeds 4 MiB');
          finish();
          return;
        }
        if (!send({ op: 'message', line })) process.stdin.pause();
      }
      if (Buffer.byteLength(input) > maxLine) finish();
    };
    child.stdin.on('drain', () => { if (!ending) process.stdin.resume(); });
    child.stdin.on('error', finish);
    const onEnd = () => {
      if (input && !ending) send({ op: 'message', line: input });
      finish();
    };
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', onData);
    process.stdin.on('end', onEnd);
    process.stdin.on('error', finish);
    process.on('SIGTERM', finish);
    process.on('SIGINT', finish);
    const lease = setInterval(() => {
      if (process.ppid !== parent) finish();
      else if (!ending) send({ op: 'lease' });
    }, 5000);
    // No model work: these tiny frames only detect abandoned exec attachments.
    child.on('error', reject);
    child.on('close', code => {
      clearInterval(lease);
      clearTimeout(killTimer);
      process.stdin.off('data', onData);
      process.stdin.off('end', onEnd);
      process.stdin.off('error', finish);
      process.stdin.pause();
      process.off('SIGTERM', finish);
      process.off('SIGINT', finish);
      resolve(code ?? 1);
    });
  });
}
