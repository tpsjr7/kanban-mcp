import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

if (!process.env.KANBAN_TIME_TEST_ZONE) {
  for (const zone of ['America/Chicago', 'UTC', 'Asia/Tokyo']) {
    const result = spawnSync(process.execPath, [fileURLToPath(import.meta.url)], {
      env: { ...process.env, TZ: zone, KANBAN_TIME_TEST_ZONE: zone }, stdio: 'inherit',
    });
    assert.equal(result.status, 0, `${zone} date regression`);
  }
} else {
  vm.runInThisContext(readFileSync(new URL('../kanban_mcp/static/browser-time.js', import.meta.url), 'utf8'));
  const time = globalThis.KanbanTime;
  for (const value of ['2026-10-30T15:00:00', '2026-10-30 15:00:00',
    '2026-10-30T15:00:00Z', '2026-10-30T10:00:00-05:00']) {
    assert.equal(time.parse(value).toISOString(), '2026-10-30T15:00:00.000Z');
  }
  assert.equal(time.parse('2026-10-30T15:00:00.123456').getUTCMilliseconds(), 123);
  const now = time.parse('2026-10-30T03:00:00Z');
  assert.equal(time.dayLabel(time.dayKey(now), now), 'Today');
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  assert.equal(time.dayLabel(time.dayKey(yesterday), now), 'Yesterday');
  assert.match(time.dayLabel('2026-10-10', now), /Oct 10/);
  if (process.env.TZ === 'America/Chicago') {
    assert.equal(time.parse('2026-10-30T15:00:00').getHours(), 10);
    assert.equal(time.parse('2026-12-30T15:00:00').getHours(), 9);
    assert.equal(time.dayKey('2026-10-30T03:00:00'), '2026-10-29');
    assert.equal(time.dayKey('2026-10-30T05:00:00'), '2026-10-30');
    assert.equal(time.parse('2026-03-08T07:59:00').getHours(), 1);
    assert.equal(time.parse('2026-03-08T08:00:00').getHours(), 3);
    const first = time.parse('2026-11-01T06:30:00');
    const second = time.parse('2026-11-01T07:30:00');
    assert.equal(first.getHours(), 1);
    assert.equal(second.getHours(), 1);
    assert.notEqual(first.getTimezoneOffset(), second.getTimezoneOffset());
    assert.equal(time.dayLabel('2026-11-01', time.parse('2026-11-02T06:30:00')), 'Yesterday');
  }
  console.log(`${process.env.TZ}: UTC parsing, local calendar grouping and display checks passed`);
}
