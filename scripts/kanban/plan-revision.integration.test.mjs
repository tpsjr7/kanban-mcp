/** Real host CLI + Docker/database integration; source authority uses a fake adapter. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID, createHash } from 'node:crypto';
import { command } from './transport.mjs';
const runtime = fileURLToPath(new URL('../../', import.meta.url));
const image = process.env.KANBAN_TEST_IMAGE;

test('CLI revisions stay in Plan Review and acknowledge inline for both clients', { skip: !image }, async () => {
  const root = await mkdtemp(join(tmpdir(), 'kanban-plan-cli-'));
  const volume = 'kanban-plan-test-' + randomUUID();
  const docker = args => command('docker', args);
  const git = (...args) => command('git', ['-C', root, ...args]);
  const runPython = (program, env = {}) => JSON.parse(docker([
    'run', '--rm', '--network', 'none', '-v', `${volume}:/data`,
    ...Object.entries(env).flatMap(([key, value]) => ['-e', `${key}=${value}`]),
    image, 'python', '-c', program,
  ]));
  try {
    docker(['volume', 'create', volume]);
    docker(['run', '--rm', '--network', 'none', '--user', '0:0', '-v', `${volume}:/data`,
      image, 'chown', '10001:10001', '/data']);
    git('init', '-b', 'main');
    git('config', 'user.name', 'Acceptance');
    git('config', 'user.email', 'fixture@localhost');
    await mkdir(join(root, 'docs'));
    const text = '# Original plan\nUnicode: café 🎵\n';
    const hash = createHash('sha256').update(text).digest('hex');
    await writeFile(join(root, 'docs/plan.md'), text);
    git('add', '.');
    git('commit', '-m', 'Disposable plan');
    for (const client of ['codex', 'opencode']) {
      const chat = client === 'codex' ? 'fixture-exact-thread' : 'ses_fixture_exact';
      const instance = 'local-' + client;
      const worktree = join(root, instance);
      git('worktree', 'add', '--detach', worktree, 'main');
      git('worktree', 'lock', '--reason', 'music-gen-instance-owner-v1:' + JSON.stringify({ owner: chat }), worktree);
      const state = join(root, 'instances/state', instance);
      await mkdir(state, { recursive: true });
      await writeFile(join(state, 'manifest.json'), JSON.stringify({ worktree }));
      const fixture = runPython(`
import os,json
from kanban_mcp.core import KanbanDB,KanbanMCPServer
from kanban_mcp.setup import auto_migrate
from kanban_mcp import team_runtime as r
client=os.environ['TEST_CLIENT']; chat=os.environ['TEST_CHAT']; team='local-'+client
server=KanbanMCPServer(); db=server.db; auto_migrate(db._backend)
project=db.ensure_project('/test/plan-revision')
r.setup(db,project)
r.configure(db,project,'manager_set',{'identity':{'client':client,'chat_id':'manager-'+chat}})
r.configure(db,project,'team_add',{'name':team,'instance':team})
r.configure(db,project,'worker_add',{'team':team,'identity':{'client':client,'chat_id':chat}})
r.configure(db,project,'arm',{})
item=db.create_item(project,'feature','Disposable plan revision')
token=r.browser_user.set('fixture-human')
try: db.set_status(item,'needs_plan')
finally: r.browser_user.reset(token)
r.consume(db,project)
plan=r.assign(db,project,'manager-'+chat,item,team,client+':'+chat)
r.host_action(db,'delivery',{'id':plan['dispatch_id'],'state':'delivered'})
for kind,payload in [('started',{}),('plan_ready',{'plan':'docs/plan.md'})]:
 ack=r.report(db,client,chat,plan['dispatch_id'],kind,payload)
 r.host_action(db,'verify_report',{'id':ack['report_id'],'plan_hash':os.environ['TEST_HASH'],'plan_path':'docs/plan.md'})
tool=server.tools['workflow_revise_plan']
assert tool['inputSchema']['properties']['expected_revision']['type']=='integer'
request=tool['function'](client,chat,item,0,os.environ['TEST_HASH'],
 'human-exact-'+client,'Revise Unicode café 🎵; no implementation.','exact-key')
assert request['state']=='pending'
print(json.dumps({'item_id':item,'tool_parameters':server.tools['workflow_revise_plan'].get('parameters')}))`,
        { TEST_CLIENT: client, TEST_CHAT: chat, TEST_HASH: hash, KANBAN_PROJECT_DIR: '/test/plan-revision' });
      const env = { ...process.env, KANBAN_WORKSPACE: root, KANBAN_MCP_IMAGE: image,
        KANBAN_MCP_VOLUME: volume, KANBAN_PROJECT_DIR: '/test/plan-revision',
        KANBAN_ADAPTER: join(runtime, 'tests/fixture-adapter.mjs'),
        [client === 'codex' ? 'CODEX_THREAD_ID' : 'OPENCODE_SESSION_ID']: chat };
      const cli = args => JSON.parse(command(process.execPath,
        [join(runtime, 'scripts/kanban.mjs'), 'automation', ...args], { env }));
      const requestFile = join(root, client + '-request.txt');
      await writeFile(requestFile, 'Revise Unicode café 🎵; no implementation.', { mode: 0o600 });
      const requestArgs = client === 'opencode' ? ['--request-file', requestFile]
        : ['--request', 'Revise Unicode café 🎵; no implementation.'];
      const args = ['revise-plan', String(fixture.item_id), '--client', client,
        '--revision', '0', '--hash', hash, '--message', 'human-exact-' + client,
        ...requestArgs, '--key', 'exact-key'];
      const result = cli(args);
      assert.equal(result.checkpoint.current, true);
      assert.equal(result.checkpoint.ticket.status, 'plan_review');
      assert.equal(result.checkpoint.dispatch.kind, 'revise_plan');
      assert.equal(result.checkpoint.dispatch.state, 'acknowledged');
      assert.equal(result.checkpoint.dispatch.target.chat_id, chat);
      assert.equal(result.checkpoint.ticket.revision, 1);
      const duplicate = cli(args);
      assert.equal(duplicate.checkpoint.dispatch.id, result.dispatch_id);
      assert.equal(duplicate.duplicate, true);
      const preflight = cli(['preflight', result.dispatch_id, '--client', client]);
      assert.equal(preflight.ready, true);
      assert.equal(preflight.plan_hash, hash);
      assert.equal(runPython(`
import json
from kanban_mcp.core import KanbanDB
from kanban_mcp import team_runtime as r
db=KanbanDB(); p=db.ensure_project('/test/plan-revision')
print(json.dumps(r.consume(db,p)))`).filter(d => d.kind === 'revise_plan').length, 0,
      'Inline request must not queue a redundant chat message');
    }
  } finally {
    docker(['volume', 'rm', volume]);
    await rm(root, { recursive: true, force: true });
  }
});
