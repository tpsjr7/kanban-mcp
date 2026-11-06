/** Fenced publication: ref transaction and worktree updates are separate states. */
import { lstatSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { git, assertClean, verifyCandidate } from './integration-candidate.mjs';

export function publicationState(repo, attempt) {
  const head = git(repo, ['rev-parse', 'HEAD']).trim();
  const index = git(repo, ['write-tree']).trim();
  const modified = git(repo, ['diff-files', '--name-only']).trim();
  if (head === attempt.candidate && index === attempt.tree && !modified
      && !git(repo, ['status', '--porcelain']).trim()) return 'installed';
  if (head === attempt.main && index === git(repo, ['rev-parse', `${attempt.main}^{tree}`]).trim()
      && !git(repo, ['status', '--porcelain']).trim()) return 'not_installed';
  return 'uncertain';
}

export function assertNoFileCollisions(repo, attempt) {
  const changes = git(repo, ['diff', '--name-status', '--no-renames', '-z', attempt.main, attempt.candidate]).split('\0');
  for (let i = 0; i < changes.length - 1; i += 2) {
    const path = changes[i + 1];
    if (changes[i] === 'A') {
      try { lstatSync(join(repo, path)); throw Error('Existing file collides with candidate: ' + path); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    let parent = dirname(path);
    while (parent !== '.') {
      try {
        if (!lstatSync(join(repo, parent)).isDirectory()) throw Error('Unsafe candidate parent: ' + parent);
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
      parent = dirname(parent);
    }
  }
}

export async function publishCandidate({ repo, attempt, gate, record, beforeWorktree, afterWorktree }) {
  verifyCandidate(repo, attempt);
  assertClean(repo);
  assertNoFileCollisions(repo, attempt);
  if (git(repo, ['symbolic-ref', 'HEAD']).trim() !== 'refs/heads/main') throw Error('Primary must be on main');
  if (git(repo, ['rev-parse', 'HEAD']).trim() !== attempt.main) throw Error('Primary advanced; rebuild and retest');
  await gate();
  const child = spawn('git', ['-C', repo, 'update-ref', '--stdin'], {
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const lines = [];
  const pending = [];
  let failure;
  let stderr = '';
  child.stderr.on('data', data => { stderr = (stderr + data).slice(-8192); });
  const fail = error => {
    failure = error;
    for (const request of pending.splice(0)) request.reject(error);
  };
  child.on('error', fail);
  child.stdin.on('error', fail);
  child.on('exit', code => {
    if (code) fail(Error(stderr || `Ref transaction exited ${code}`));
  });
  createInterface({ input: child.stdout }).on('line', line => {
    const next = pending.shift();
    if (next) next.resolve(line);
    else lines.push(line);
  });
  const exchange = async (commands, expected) => {
    if (failure) throw failure;
    const response = new Promise((resolve, reject) => {
      if (lines.length) resolve(lines.shift());
      else pending.push({ resolve, reject });
    });
    child.stdin.write(commands);
    let timer;
    try {
      const line = await Promise.race([response, new Promise((_, reject) => {
        timer = setTimeout(() => reject(Error('Ref transaction timed out')), 10000);
      })]);
      if (line !== expected) throw Error('Unexpected ref transaction response: ' + line);
    } finally { clearTimeout(timer); }
  };
  let committed = false;
  try {
    await exchange('start\n', 'start: ok');
    await exchange(`update refs/heads/main ${attempt.candidate} ${attempt.main}\nprepare\n`, 'prepare: ok');
    // The expected-old ref is now locked. Never unlink this or another writer's lock.
    await gate();
    assertClean(repo);
    assertNoFileCollisions(repo, attempt);
    if (beforeWorktree) await beforeWorktree();
    await record('publishing');
    git(repo, ['read-tree', '-u', '-m', attempt.main, attempt.candidate]);
    if (git(repo, ['write-tree']).trim() !== attempt.tree
        || git(repo, ['diff-files', '--name-only']).trim()) throw Error('Primary worktree update is incomplete');
    if (afterWorktree) await afterWorktree();
    await gate();
    await exchange('commit\n', 'commit: ok');
    committed = true;
    if (publicationState(repo, attempt) !== 'installed') throw Error('Published ref/worktree disagree');
    await record('installed');
  } finally {
    child.stdin.end();
    if (!committed) child.kill('SIGTERM');
  }
}
