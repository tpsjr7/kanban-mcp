/** Real CLI/Docker acceptance with an isolated database and disposable Git claims. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { command } from './transport.mjs';

const runtime = fileURLToPath(new URL('../../', import.meta.url));
const image = process.env.KANBAN_TEST_IMAGE;

test('real preflight and inline retry stay in-turn for both clients without a daemon',
  { skip: !image }, async () => {
    const root = await mkdtemp(join(tmpdir(), 'kanban-worker-cli-'));
    const volume = 'kanban-worker-test-' + randomUUID();
    const docker = args => command('docker', args);
    const git = (...args) => command('git', ['-C', root, ...args]);
    const runPython = (program, extraEnv = {}) => {
      const options = Object.entries(extraEnv).flatMap(([key, value]) => ['-e', `${key}=${value}`]);
      return JSON.parse(docker(['run', '--rm', '--network', 'none', '-v', `${volume}:/data`,
        ...options, image, 'python', '-c', program]));
    };
    try {
      docker(['volume', 'create', volume]);
      docker(['run', '--rm', '--network', 'none', '--user', '0:0', '-v', `${volume}:/data`,
        image, 'chown', '10001:10001', '/data']);
      git('init', '-b', 'main');
      git('config', 'user.name', 'Acceptance');
      git('config', 'user.email', 'acceptance@example.invalid');
      await writeFile(join(root, 'fixture.txt'), 'Disposable acceptance');
      git('add', 'fixture.txt');
      git('commit', '-m', 'Fixture');
      for (const client of ['codex', 'opencode']) {
        const id = client === 'codex' ? 'exact-fixture-thread' : 'ses_exact_fixture';
        const instance = 'local-' + client;
        const worktree = join(root, instance);
        git('worktree', 'add', '--detach', worktree, 'main');
        git('worktree', 'lock', '--reason', 'music-gen-instance-owner-v1:' + JSON.stringify({ owner: id }), worktree);
        const state = join(root, 'instances/state', instance);
        await mkdir(state, { recursive: true });
        await writeFile(join(state, 'manifest.json'), JSON.stringify({ worktree }));
        const request = runPython(`
import os,json
from kanban_mcp.core import KanbanDB
from kanban_mcp.setup import auto_migrate
from kanban_mcp import team_runtime as r
client=os.environ['TEST_CLIENT']; chat=os.environ['TEST_ID']; team='local-'+client
db=KanbanDB(); auto_migrate(db._backend); project=db.ensure_project('/test/worker-inline')
r.setup(db,project)
r.configure(db,project,'manager_set',{'identity':{'client':client,'chat_id':'manager-'+chat}})
r.configure(db,project,'team_add',{'name':team,'instance':team})
r.configure(db,project,'worker_add',{'team':team,'identity':{'client':client,'chat_id':chat}})
r.configure(db,project,'arm',{})
item=db.create_item(project,'feature','Fixture')
token=r.browser_user.set('human')
try: db.set_status(item,'needs_plan')
finally: r.browser_user.reset(token)
dispatch=next(d for d in r.consume(db,project) if d['kind']=='assign')
r.host_action(db,'delivery',{'id':dispatch['id'],'state':'delivered'})
print(json.dumps({'item_id':item,'dispatch_id':dispatch['id']}))`, { TEST_CLIENT: client, TEST_ID: id });
        const env = { ...process.env, KANBAN_WORKSPACE: root, KANBAN_MCP_IMAGE: image,
          KANBAN_MCP_VOLUME: volume, KANBAN_PROJECT_DIR: '/test/worker-inline',
          KANBAN_ADAPTER: join(runtime, 'tests/fixture-adapter.mjs'),
          CODEX_THREAD_ID: id, OPENCODE_SESSION_ID: id };
        const cli = args => JSON.parse(command(process.execPath,
          [join(runtime, 'scripts/kanban.mjs'), 'automation', ...args], { env }));
        const managerEnv = { ...env, CODEX_THREAD_ID: 'manager-' + id,
          OPENCODE_SESSION_ID: 'manager-' + id };
        const manager = JSON.parse(command(process.execPath,
          [join(runtime, 'scripts/kanban.mjs'), 'automation', 'preflight', request.dispatch_id,
            '--client', client], { env: managerEnv }));
        assert.equal(manager.ready, true);
        assert.equal(manager.role, 'manager');
        assert.equal(manager.worker_instance_required, false);
        const fixture = runPython(`
import os,json
from kanban_mcp.core import KanbanDB
from kanban_mcp import team_runtime as r
db=KanbanDB()
project=db.ensure_project('/test/worker-inline')
client=os.environ['TEST_CLIENT']
chat=os.environ['TEST_ID']
ack=r.assign(db,project,'manager-'+chat,int(os.environ['TEST_ITEM']),'local-'+client,client+':'+chat)
r.host_action(db,'delivery',{'id':ack['dispatch_id'],'state':'delivered'})
print(json.dumps(ack))`, { TEST_CLIENT: client, TEST_ID: id, TEST_ITEM: request.item_id });
        const preflight = cli(['preflight', fixture.dispatch_id, '--client', client]);
        assert.equal(preflight.ready, true);
        assert.equal(preflight.owner, id);
        runPython(`
import os,json
from kanban_mcp.core import KanbanDB
from kanban_mcp import team_runtime as r
db=KanbanDB()
ack=r.report(db,os.environ['TEST_CLIENT'],os.environ['TEST_ID'],os.environ['TEST_DISPATCH'],
             'failed',{'reason':'Disposable failure'})
r.host_action(db,'verify_report',{'id':ack['report_id']})
print(json.dumps(ack))`, { TEST_CLIENT: client, TEST_ID: id, TEST_DISPATCH: fixture.dispatch_id });
        const args = ['retry', String(fixture.item_id), '--inline', fixture.dispatch_id, '--client', client];
        const resumed = cli(args);
        assert.equal(resumed.resumed, true);
        assert.equal(resumed.checkpoint.current, true);
        assert.equal(resumed.checkpoint.dispatch.state, 'acknowledged');
        assert.equal(resumed.checkpoint.dispatch.target.chat_id, id);
        const duplicate = cli(args);
        assert.equal(duplicate.duplicate, true);
        assert.equal(duplicate.checkpoint.dispatch.id, resumed.checkpoint.dispatch.id);
        const queued = runPython(`
import json
from kanban_mcp.core import KanbanDB
from kanban_mcp import team_runtime as r
db=KanbanDB(); project=db.ensure_project('/test/worker-inline')
print(json.dumps(r.consume(db,project)))`);
        assert.equal(queued.length, 0, 'Inline retry must never queue a chat delivery');
      }
    } finally {
      docker(['volume', 'rm', volume]);
      await rm(root, { recursive: true, force: true });
    }
  });
