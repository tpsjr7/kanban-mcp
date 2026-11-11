/** Portable fail-closed lifecycle lock; never reap another process's lock. */
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';

export async function withLifecycleLock(identity, action, base = join(homedir(), '.local/state/kanban-mcp')) {
  await mkdir(base, { recursive: true, mode: 0o700 });
  const key = createHash('sha256').update(identity).digest('hex').slice(0, 16);
  const directory = join(base, `lifecycle-${key}.lock`);
  const record = join(directory, 'owner.json');
  const token = randomUUID();
  try { await mkdir(directory, { mode: 0o700 }); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    throw new Error(`Another lifecycle command holds ${directory}. Inspect owner.json and process ownership; stale locks require manual reconciliation.`);
  }
  try {
    await writeFile(record, JSON.stringify({ token, pid: process.pid, created: new Date().toISOString() }), {
      flag: 'wx', mode: 0o600,
    });
    return await action();
  } finally {
    const owner = JSON.parse(await readFile(record, 'utf8'));
    if (owner.token !== token) throw new Error(`Lifecycle lock ownership changed: ${directory}`);
    await rm(directory, { recursive: true });
  }
}
