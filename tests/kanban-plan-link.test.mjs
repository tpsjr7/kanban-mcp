import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
const require = createRequire(new URL('../package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<section id="workflow-details"></section>', {
  url: 'http://localhost/?project=test', runScripts: 'outside-only',
});
const window = dom.window;
window.kanbanColumns = { columns: [] };
let ticket;
window.fetch = async () => ({ ok: true, json: async () => ({
  tickets: [ticket], team_workers: [], orchestration_settings: [], reports: [],
}) });
window.eval(readFileSync(new URL('../kanban_mcp/static/update-attribution.js', import.meta.url), 'utf8'));
window.eval(readFileSync(new URL('../kanban_mcp/static/workflow.js', import.meta.url), 'utf8'));
async function render(fields) {
  ticket = { item_id: 1, phase: 'plan_review', ...fields };
  await window.KanbanWorkflow.load(1);
  return window.document.getElementById('workflow-details');
}
const path = '/workspace/music_gen/docs/Long plan — 音楽.md';
let panel = await render({ plan_path: path, plan_client: 'codex',
  plan_chat_id: 'opaque/<id>', plan_chat_title: '<img src=x>', plan_text: 'FULL CONTENT MUST NOT RENDER' });
assert.equal(panel.querySelector('code').textContent, path);
assert.equal(panel.querySelector('a').getAttribute('href'), 'codex://threads/opaque%2F%3Cid%3E');
assert.equal(panel.querySelector('a').textContent, '<img src=x>');
assert.equal(panel.querySelectorAll('img, script, pre').length, 0);
assert.ok(!panel.textContent.includes('FULL CONTENT'));
panel = await render({ plan: 'docs/legacy.md' });
assert.equal(panel.querySelector('code').textContent, 'docs/legacy.md');
assert.equal(panel.querySelector('a'), null);
panel = await render({ plan_client: 'opencode', plan_chat_id: 'ses_opaque', plan_chat_title: 'OpenCode Planner' });
assert.equal(panel.querySelector('code').textContent, 'ses_opaque');
assert.equal(panel.querySelector('a'), null);
panel = await render({});
assert.equal(panel.querySelector('code, a'), null);
console.log('Plan path, creator links, escaping and historical/OpenCode fallbacks passed');
