// Development test dependency: npm --prefix frontend ci.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
const require = createRequire(new URL('../package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only' });
const copied = [];
Object.defineProperty(dom.window.navigator, 'clipboard', {
  value: { writeText: async text => copied.push(text) },
});
dom.window.eval(readFileSync(new URL('../kanban_mcp/static/update-attribution.js', import.meta.url), 'utf8'));
const render = identity => dom.window.KanbanAttribution.element(identity);
assert.equal(render({}).textContent, 'Unknown author');
assert.equal(render({ browser_username: 'Jane' }).querySelector('.update-author').textContent, 'Jane');
assert.equal(render({ browser_username: 'Jane' }).querySelector('details'), null);
const unsafe = '<img src=x onerror=alert(1)>'.repeat(8);
const code = render({ source_client: 'codex', agent_name: unsafe,
  chat_id: 'opaque/<id>?test', chat_title: unsafe });
assert.equal(code.querySelector('.update-author').textContent, unsafe);
assert.equal(code.querySelectorAll('img, script').length, 0);
assert.equal(code.querySelector('a').getAttribute('href'), 'codex://threads/opaque%2F%3Cid%3E%3Ftest');
const open = render({ source_client: 'opencode', agent_name: 'OpenCode title',
  chat_title: 'OpenCode title', chat_id: 'ses_exact-id' });
assert.equal(open.querySelector('a'), null);
assert.equal(open.querySelector('code').textContent, 'ses_exact-id');
assert.equal(open.querySelector('summary').textContent, 'Chat');
open.querySelector('button').click();
await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(copied, ['ses_exact-id']);
assert.equal(open.querySelector('button').textContent, 'Copied');
dom.window.close();
console.log('Attribution DOM checks passed: legacy/browser authors, escaped names, safe Codex links, OpenCode IDs and clipboard.');
