/** Host Docker acceptance. All writes use a unique disposable board volume. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';
import { configuration, control, docker, ensureService, inspect, stopService } from './mcp-runtime.mjs';

const runtime = fileURLToPath(new URL('../../', import.meta.url));
const launcher = fileURLToPath(new URL('../kanban.mjs', import.meta.url));
const image = process.env.KANBAN_TEST_IMAGE;

function client(env) {
  const child = spawn(process.execPath, [launcher, 'mcp'], {
    cwd: runtime, env, stdio: ['pipe', 'pipe', 'pipe'],
  });
  const pending = new Map();
  let errors = '';
  child.stderr.on('data', value => { errors += value; });
  createInterface({ input: child.stdout }).on('line', line => {
    const response = JSON.parse(line);
    const request = pending.get(response.id);
    if (request) { pending.delete(response.id); request.resolve(response); }
  });
  child.on('exit', code => {
    for (const request of pending.values()) request.reject(Error(`Client exited ${code}: ${errors}`));
    pending.clear();
  });
  return {
    child,
    request: (id, method, params = {}) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error(`Request timeout: ${errors}`)), 45000);
      pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); },
        reject: error => { clearTimeout(timer); reject(error); } });
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    }),
    raw: value => child.stdin.write(value + '\n'),
  };
}

function data(value) {
  assert.equal(value.error, undefined, JSON.stringify(value));
  return JSON.parse(value.result.content[0].text);
}

async function waitFor(predicate, timeout = 45000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await predicate()) return;
    await delay(200);
  }
  throw Error('Condition did not settle');
}

test('one service supports 20 isolated clients, cleanup, restart and recovery', {
  skip: !image, timeout: 240000,
}, async () => {
  const volume = `kanban-shared-test-${randomUUID()}`;
  const env = { ...process.env, KANBAN_ADAPTER: '', KANBAN_WORKSPACE: runtime,
    KANBAN_PROJECT_DIR: '/workspace/shared-test', KANBAN_MCP_VOLUME: volume,
    KANBAN_MCP_IMAGE: image };
  const config = configuration(runtime, env);
  const clients = [];
  const recovery = `${volume}-restore`;
  try {
    assert.throws(() => docker(['inspect', `kanban-test-absent-${randomUUID()}`], { encoding: null }), /No such (object|container)/i);
    for (let index = 0; index < 20; index++) clients.push(client(env));
    const initialized = await Promise.all(clients.map(value => value.request(1, 'initialize')));
    assert(initialized.every(value => value.result.protocolVersion === '2024-11-05'));
    assert.equal(control(config).connections, 20);
    const matching = docker(['ps', '-aq', '--filter', `volume=${volume}`]).trim().split('\n');
    assert.equal(matching.length, 1, 'one service, no per-client containers');
    assert.equal(inspect(config.name).HostConfig.NetworkMode, 'none');
    assert.throws(() => stopService(config), /20 connected clients/);
    assert.equal(control(config).draining, false);
    await Promise.all(clients.slice(0, 2).map((value, index) => value.request(2, 'tools/call', {
      name: 'set_current_project', arguments: { project_dir: `/session/${index}` },
    })));
    const projects = await Promise.all(clients.slice(0, 2).map(value => value.request(3, 'tools/call', {
      name: 'get_current_project', arguments: {},
    })));
    assert.equal(data(projects[0]).directory_path, '/session/0');
    assert.equal(data(projects[1]).directory_path, '/session/1');
    const item = data(await clients[0].request(4, 'tools/call', {
      name: 'new_item', arguments: { item_type: 'feature', title: 'Shared Unicode 🪴',
        description: 'Plan: docs/SHARED_MCP_PLAN.md; review: https://example.invalid/review/1' },
    }));
    assert.equal(item.success, true);
    const itemID = item.item.id;
    assert(itemID);
    for (const [index, identity] of [['codex', 'codex-opaque'], ['opencode', 'ses_shared']].entries()) {
      const updated = data(await clients[index].request(5, 'tools/call', {
        name: 'add_update', arguments: { content: 'Unicode ✓ ' + identity[0],
          item_ids: String(itemID), client: identity[0], chat_id: identity[1], chat_title: identity[0] },
      }));
      assert.equal(updated.attribution.chat_id, identity[1]);
    }
    const related = data(await clients[0].request(6, 'tools/call', {
      name: 'new_item', arguments: { item_type: 'feature', title: 'Related fixture' },
    }));
    assert.equal(data(await clients[0].request(7, 'tools/call', {
      name: 'add_relationship', arguments: { source_id: itemID,
        target_id: related.item.id, relationship_type: 'relates_to' },
    })).success, true);
    assert.equal(data(await clients[0].request(8, 'tools/call', {
      name: 'link_file', arguments: { item_id: itemID, file_path: 'docs/SHARED_MCP_PLAN.md' },
    })).success, true);
    assert.equal(data(await clients[0].request(9, 'tools/call', {
      name: 'set_status', arguments: { item_id: itemID, status: 'todo' },
    })).success, true);
    clients[0].raw('{broken');
    assert((await clients[0].request(6, 'tools/list')).result.tools.length > 0);
    // EOF, termination, and abrupt host loss must release only their own sessions.
    clients[0].child.stdin.end();
    clients[1].child.kill('SIGTERM');
    clients[2].child.kill('SIGKILL');
    await waitFor(() => control(config).connections === 17);
    for (const value of clients.slice(3)) value.child.stdin.end();
    await waitFor(() => control(config).connections === 0);
    const before = data(await (async () => {
      const value = client(env); clients.push(value);
      return value.request(1, 'tools/call', { name: 'get_item', arguments: { item_id: itemID } });
    })());
    assert.equal(before.item.title, 'Shared Unicode 🪴');
    clients.at(-1).child.stdin.end();
    await waitFor(() => control(config).connections === 0);
    docker(['restart', config.name]);
    await ensureService(config);
    const reconnected = client(env); clients.push(reconnected);
    const after = data(await reconnected.request(1, 'tools/call', {
      name: 'get_item', arguments: { item_id: itemID },
    }));
    assert.deepEqual(after.item, before.item);
    reconnected.child.stdin.end();
    await waitFor(() => control(config).connections === 0);
    const bytes = docker(['run', '--rm', '--network', 'none', '-v', `${volume}:/data`,
      image, 'python', '/opt/kanban/operations.py', 'backup'], { encoding: null });
    docker(['volume', 'create', recovery]);
    const restored = JSON.parse(docker(['run', '--rm', '-i', '--network', 'none',
      '-v', `${recovery}:/data`, image, 'python', '/opt/kanban/operations.py', 'restore-check'], { input: bytes }));
    const source = JSON.parse(docker(['run', '--rm', '--network', 'none',
      '-v', `${volume}:/data`, image, 'python', '-c',
      "import sys,sqlite3,json; sys.path.insert(0,'/opt/kanban'); from operations import fingerprint; print(json.dumps(fingerprint(sqlite3.connect('file:/data/kanban.db?mode=ro',uri=True))))"]));
    assert.deepEqual(restored, source, 'every table, status, identity, relationship and file link survives backup');
    assert.equal(restored.items.rows, 2);
    assert.equal(restored.item_relationships.rows, 1);
    assert.equal(restored.item_files.rows, 1);
    assert.equal(restored.updates.rows, 2);
    stopService(config);
    assert.equal(inspect(config.name), null);
  } finally {
    for (const value of clients) { value.child.stdin.destroy(); value.child.kill('SIGTERM'); }
    await Promise.all(clients.map(value => value.child.exitCode !== null || value.child.signalCode !== null
      ? Promise.resolve() : new Promise(resolve => value.child.once('exit', resolve))));
    await delay(500);
    if (inspect(config.name)) stopService(config, true);
    docker(['volume', 'rm', '-f', volume]);
    docker(['volume', 'rm', '-f', recovery]);
  }
});
