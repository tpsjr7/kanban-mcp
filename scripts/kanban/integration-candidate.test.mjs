import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, mkdirSync, rmSync, readFileSync, chmodSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { git, prepareCandidate, verifyCandidate } from './integration-candidate.mjs';
import { publishCandidate, publicationState } from './integration-publication.mjs';
import { saveJournal, readJournal, recordAttempt } from './integration-journal.mjs';
function fixture(t, withIndex = false) {
  const directory = mkdtempSync(join(tmpdir(), 'kanban-candidate-test-'));
  const repo = join(directory, 'repo');
  mkdirSync(repo);
  git(repo, ['init', '-b', 'main']);
  git(repo, ['config', 'user.name', 'Fixture']);
  git(repo, ['config', 'user.email', 'fixture@localhost']);
  writeFileSync(join(repo, 'feature.txt'), 'base\n');
  if (withIndex) writeFileSync(join(repo, 'index.md'), 'heading\nold row\nanchor\nfollowing\n');
  git(repo, ['add', '.']);
  git(repo, ['commit', '-m', 'base']);
  git(repo, ['checkout', '-b', 'task']);
  writeFileSync(join(repo, 'feature.txt'), 'approved\n');
  writeFileSync(join(repo, 'added.txt'), 'approved addition\n');
  if (withIndex) writeFileSync(join(repo, 'index.md'), 'heading\nold row\nanchor\ntask row\nfollowing\n');
  git(repo, ['add', '.']);
  git(repo, ['commit', '-m', 'approved task']);
  const approved = git(repo, ['rev-parse', 'HEAD']).trim();
  git(repo, ['checkout', 'main']);
  writeFileSync(join(repo, 'unrelated.txt'), 'new main\n');
  if (withIndex) writeFileSync(join(repo, 'index.md'), 'heading\nnew main row\nold row\nanchor\nfollowing\n');
  git(repo, ['add', '.']);
  git(repo, ['commit', '-m', 'unrelated main']);
  t.after(() => {
    for (const row of git(repo, ['worktree', 'list', '--porcelain']).split('\n')) {
      if (row.startsWith('worktree ') && row.slice(9) !== repo) {
        git(repo, ['worktree', 'remove', '--force', row.slice(9)]);
      }
    }
    rmSync(directory, { recursive: true, force: true });
  });
  return { repo, approved, directory, prepare: () => prepareCandidate({ repo, approved,
    directory: join(directory, 'attempt') }) };
}
test('candidate preserves exact approved task and current main without publishing', t => {
  const f = fixture(t);
  const before = git(f.repo, ['rev-parse', 'HEAD']).trim();
  const candidate = f.prepare();
  assert.equal(git(f.repo, ['rev-parse', 'HEAD']).trim(), before);
  assert.equal(candidate.main, before);
  assert.equal(git(f.repo, ['show', `${candidate.candidate}:feature.txt`]), 'approved\n');
  assert.equal(git(f.repo, ['show', `${candidate.candidate}:unrelated.txt`]), 'new main\n');
  assert.equal(git(f.repo, ['rev-parse', 'task']).trim(), f.approved);
  verifyCandidate(f.repo, candidate);
});
test('fenced publication installs exactly the tested tree and records boundaries', async t => {
  const f = fixture(t);
  const candidate = f.prepare();
  const phases = [];
  await publishCandidate({ repo: f.repo, attempt: candidate, gate: async () => {},
    record: async phase => phases.push(phase) });
  assert.equal(publicationState(f.repo, candidate), 'installed');
  assert.deepEqual(phases, ['publishing', 'installed']);
});
test('a concurrent ref update is never overwritten', async t => {
  const f = fixture(t);
  const candidate = f.prepare();
  let changed = false;
  await assert.rejects(publishCandidate({ repo: f.repo, attempt: candidate,
    record: async () => {}, gate: async () => {
      if (!changed) {
        git(f.repo, ['commit', '--allow-empty', '-m', 'concurrent main']);
        changed = true;
      }
    } }));
  assert.notEqual(git(f.repo, ['rev-parse', 'HEAD']).trim(), candidate.candidate);
  assert.equal(readFileSync(join(f.repo, 'feature.txt'), 'utf8'), 'base\n');
});
test('crash between worktree and ref publication remains uncertain without reset', async t => {
  const f = fixture(t);
  const candidate = f.prepare();
  await assert.rejects(publishCandidate({ repo: f.repo, attempt: candidate,
    gate: async () => {}, record: async () => {},
    afterWorktree: async () => { throw Error('simulated crash'); } }), /simulated crash/);
  assert.equal(git(f.repo, ['rev-parse', 'HEAD']).trim(), candidate.main);
  assert.equal(publicationState(f.repo, candidate), 'uncertain');
  assert.equal(readFileSync(join(f.repo, 'feature.txt'), 'utf8'), 'approved\n');
});
test('dirty primary blocks publication and preserves user files', async t => {
  const f = fixture(t);
  const candidate = f.prepare();
  writeFileSync(join(f.repo, 'user.txt'), 'uncommitted user work');
  await assert.rejects(publishCandidate({ repo: f.repo, attempt: candidate,
    gate: async () => {}, record: async () => {} }), /dirty/);
  assert.equal(readFileSync(join(f.repo, 'user.txt'), 'utf8'), 'uncommitted user work');
});
test('conflicting main changes fail before primary or approved refs change', t => {
  const f = fixture(t);
  writeFileSync(join(f.repo, 'feature.txt'), 'conflicting main\n');
  git(f.repo, ['add', '.']);
  git(f.repo, ['commit', '-m', 'conflict']);
  const before = git(f.repo, ['rev-parse', 'HEAD']).trim();
  assert.throws(f.prepare);
  assert.equal(git(f.repo, ['rev-parse', 'HEAD']).trim(), before);
  assert.equal(git(f.repo, ['rev-parse', 'task']).trim(), f.approved);
});
test('private journal retains exact phase without inventing completion', t => {
  const f = fixture(t);
  const path = join(f.directory, 'journal.json');
  saveJournal(path, { phase: 'publishing', approved: f.approved });
  assert.equal(readJournal(path).phase, 'publishing');
  saveJournal(path, { phase: 'installed', approved: f.approved });
  assert.equal(readJournal(path).phase, 'installed');
});

test('unsupported binary, attribute, mode and colliding additions fail closed', t => {
  for (const kind of ['binary', 'attributes', 'mode', 'collision']) {
    const f = fixture(t);
    if (kind === 'collision') {
      writeFileSync(join(f.repo, 'added.txt'), 'foreign main addition\n');
      git(f.repo, ['add', '.']);
      git(f.repo, ['commit', '-m', 'colliding addition']);
      assert.throws(f.prepare);
      continue;
    }
    git(f.repo, ['checkout', 'task']);
    if (kind === 'binary') writeFileSync(join(f.repo, 'binary.bin'), Buffer.from([0, 1, 2]));
    if (kind === 'attributes') writeFileSync(join(f.repo, '.gitattributes'), '*.txt filter=custom\n');
    if (kind === 'mode') chmodSync(join(f.repo, 'feature.txt'), 0o755);
    git(f.repo, ['add', '.']);
    git(f.repo, ['commit', '-m', kind]);
    const approved = git(f.repo, ['rev-parse', 'HEAD']).trim();
    git(f.repo, ['checkout', 'main']);
    assert.throws(() => prepareCandidate({ repo: f.repo, approved, directory: join(f.directory, 'rejected') }));
  }
});
test('untracked filesystem collision is preserved even though candidate proof succeeded', async t => {
  const f = fixture(t);
  const attempt = f.prepare();
  writeFileSync(join(f.repo, 'added.txt'), 'user content\n');
  await assert.rejects(publishCandidate({ repo: f.repo, attempt, gate: async () => {}, record: async () => {} }));
  assert.equal(readFileSync(join(f.repo, 'added.txt'), 'utf8'), 'user content\n');
});
test('recovery never automatically repeats an uncertain restart or partial publication', async () => {
  const { recoveryAction } = await import('./integration-journal.mjs');
  assert.equal(recoveryAction('publishing', 'uncertain'), 'manual');
  assert.equal(recoveryAction('publishing', 'installed'), 'reconcile-publication');
  assert.equal(recoveryAction('restart_pending', 'installed'), 'verify-restart-evidence');
  assert.equal(recoveryAction('cleanup_pending', 'installed'), 'verify-cleanup-evidence');
  assert.equal(recoveryAction('cleaned', 'installed'), 'report-integrated');
});

test('ignored filesystem collision is never overwritten by publication', async t => {
  const f = fixture(t);
  writeFileSync(join(f.repo, '.gitignore'), 'added.txt\n');
  git(f.repo, ['add', '.gitignore']);
  git(f.repo, ['commit', '-m', 'ignore private file']);
  const attempt = f.prepare();
  writeFileSync(join(f.repo, 'added.txt'), 'ignored user file\n');
  await assert.rejects(publishCandidate({ repo: f.repo, attempt, gate: async () => {}, record: async () => {} }), /collides/);
  assert.equal(readFileSync(join(f.repo, 'added.txt'), 'utf8'), 'ignored user file\n');
});

test('candidate cleanup is exact and idempotent after a completed installation', async t => {
  const { cleanupCandidate } = await import('./integration-candidate.mjs');
  const f = fixture(t);
  const attempt = f.prepare();
  await publishCandidate({ repo: f.repo, attempt, gate: async () => {}, record: async () => {} });
  cleanupCandidate(f.repo, attempt);
  cleanupCandidate(f.repo, attempt);
  assert.equal(git(f.repo, ['rev-parse', 'HEAD']).trim(), attempt.candidate);
});

test('documentation exception retains an anchor when main shifts insertion lines', t => {
  const f = fixture(t, true);
  const candidate = prepareCandidate({ repo: f.repo, approved: f.approved,
    directory: join(f.directory, 'anchored'), zeroContextPaths: ['index.md'],
    validateZeroContext: ({ candidate }) => {
      assert.equal(candidate, 'heading\nnew main row\nold row\nanchor\ntask row\nfollowing\n');
    } });
  assert.equal(candidate.patches.find(patch => patch.paths.includes('index.md')).context, 1);
});

test('initial identity survives a lost first board receipt without creating another attempt', async t => {
  const directory = mkdtempSync(join(tmpdir(), 'kanban-journal-crash-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const filename = join(directory, 'attempt.json');
  const attempt = { id: 'exact-attempt', dispatch_id: 'exact-dispatch' };
  let board;
  await assert.rejects(recordAttempt(filename, attempt, 'prepared', record => {
    board = { ...record };
    throw Error('crash after first board write');
  }), /crash/);
  assert.equal(readJournal(filename).id, board.id);
  assert.equal(readJournal(filename).dispatch_id, board.dispatch_id);
  await recordAttempt(filename, readJournal(filename), 'prepared', record => {
    assert.equal(record.id, board.id);
  });
  assert.equal(readJournal(filename).phase, 'prepared');
});
