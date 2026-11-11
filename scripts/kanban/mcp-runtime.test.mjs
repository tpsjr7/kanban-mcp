import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { assertCompatible, configuration } from './mcp-runtime.mjs';
import { withLifecycleLock } from './lifecycle-lock.mjs';

function fixture(config) {
  return {
    Image: 'image-id', Mounts: [{ Destination: '/data', Name: config.volume, RW: true }],
    Config: {
      Env: [`KANBAN_PROJECT_DIR=${config.project}`, 'KANBAN_SQLITE_PATH=/data/kanban.db', 'KANBAN_BACKEND=sqlite'],
      User: '10001:10001', Cmd: ['python', '-m', 'kanban_mcp.shared_runtime', 'serve'],
      Labels: Object.fromEntries(Object.entries({ role: 'mcp-service', protocol: '1',
        owner: config.owner, project: config.project, volume: config.volume })
        .map(([key, value]) => ['ai.yolofi.kanban.' + key, value])),
    },
    HostConfig: { NetworkMode: 'none', SecurityOpt: ['no-new-privileges'], CapDrop: ['ALL'] },
  };
}

test('board identity is stable across platforms and unrelated stack names', () => {
  const env = { KANBAN_MCP_VOLUME: 'shared-board' };
  assert.equal(configuration('C:\\board', env).name, configuration('/board', env).name);
  assert.notEqual(configuration('/board', env).name, configuration('/board', {}).name);
});

test('service reuse rejects changed ownership, image, project, volume or isolation', () => {
  const config = configuration('/board', {});
  assert.doesNotThrow(() => assertCompatible(fixture(config), config, 'image-id'));
  for (const alter of [
    value => { value.Config.Labels['ai.yolofi.kanban.owner'] = '/foreign'; },
    value => { value.Config.Labels['ai.yolofi.kanban.project'] = '/foreign'; },
    value => { value.Image = 'old-image'; },
    value => { value.Mounts[0].Name = 'foreign'; },
    value => { value.Config.User = '0'; },
    value => { value.Config.Env = []; },
    value => { value.HostConfig.NetworkMode = 'bridge'; },
    value => { value.HostConfig.CapDrop = []; },
    value => { value.HostConfig.SecurityOpt = []; },
    value => { value.Config.Cmd = ['kanban-mcp']; },
  ]) {
    const value = fixture(config);
    alter(value);
    assert.throws(() => assertCompatible(value, config, 'image-id'), /incompatible/);
  }
});

test('portable lifecycle lock excludes competitors and releases after failures', async () => {
  const base = await mkdtemp(join(tmpdir(), 'kanban-lock-'));
  try {
    await withLifecycleLock('board', async () => {
      await assert.rejects(withLifecycleLock('board', () => {}, base), /holds/);
    }, base);
    await assert.rejects(withLifecycleLock('board', () => { throw Error('fixture'); }, base), /fixture/);
    assert.equal(await withLifecycleLock('board', () => 7, base), 7);
    await assert.rejects(readFile(join(base, 'not-a-lock')), /ENOENT/);
  } finally { await rm(base, { recursive: true, force: true }); }
});


test('changed lifecycle ownership is retained for reconciliation', async () => {
  const base = await mkdtemp(join(tmpdir(), 'kanban-lock-owner-'));
  try {
    await assert.rejects(withLifecycleLock('board', async () => {
      const [directory] = await readdir(base);
      await writeFile(join(base, directory, 'owner.json'), JSON.stringify({ token: 'foreign' }));
    }, base), /ownership changed/);
    const [directory] = await readdir(base);
    assert.equal(JSON.parse(await readFile(join(base, directory, 'owner.json'), 'utf8')).token, 'foreign');
  } finally { await rm(base, { recursive: true, force: true }); }
});
