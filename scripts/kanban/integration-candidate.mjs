/** Immutable task + current main -> independently verified integration tree. */
import { execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync, readFileSync, lstatSync } from 'node:fs';
import { join, resolve } from 'node:path';

export const policyVersion = 'verified-candidate-v1';
const safeConfig = ['-c', 'core.hooksPath=/dev/null', '-c', 'rerere.enabled=false',
  '-c', 'core.attributesFile=/dev/null', '-c', 'merge.renormalize=false', '-c', 'core.autocrlf=false', '-c', 'commit.gpgSign=false'];
export function git(repo, args, options = {}) {
  const { env = {}, ...rest } = options;
  const controlled = { ...process.env, ...env };
  for (const key of Object.keys(controlled)) {
    if (key.startsWith('GIT_CONFIG_') || ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR',
      'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_EXTERNAL_DIFF',
      'GIT_NAMESPACE'].includes(key)) delete controlled[key];
  }
  if (!env.GIT_INDEX_FILE) delete controlled.GIT_INDEX_FILE;

  return execFileSync('git', [...safeConfig, '-C', repo, ...args], {
    encoding: 'utf8', timeout: 45000, maxBuffer: 32 * 1024 * 1024,
    env: { ...controlled, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
      GIT_ATTR_NOSYSTEM: '1', GIT_NO_REPLACE_OBJECTS: '1',
      GIT_AUTHOR_NAME: 'Kanban integration', GIT_AUTHOR_EMAIL: 'kanban@localhost',
      GIT_COMMITTER_NAME: 'Kanban integration', GIT_COMMITTER_EMAIL: 'kanban@localhost',
      }, ...rest,
  });
}
export function assertClean(repo) {
  if (git(repo, ['status', '--porcelain']).trim()) throw Error('Checkout is dirty; preserve its files');
  if (git(repo, ['ls-files', '-u']).trim()) throw Error('Unresolved index');
}
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

function supportedChanges(repo, base, approved) {
  const names = git(repo, ['diff', '--name-status', '-M', '-z', base, approved]).split('\0');
  for (let i = 0; i < names.length - 1; i += 2) {
    if (!['A', 'M', 'D'].includes(names[i])) throw Error('Unsupported rename/copy change');
  }
  const raw = git(repo, ['diff', '--raw', '--no-abbrev', '--no-renames', '-z', base, approved]).split('\0');
  const paths = [];
  for (let i = 0; i < raw.length - 1; i += 2) {
    const [oldMode, newMode] = raw[i].slice(1).split(' ');
    if (![oldMode, newMode].every(mode => ['000000', '100644'].includes(mode))) {
      throw Error('Unsupported symlink, submodule or mode change');
    }
    paths.push(raw[i + 1]);
  }
  if (git(repo, ['diff', '--numstat', '--no-renames', base, approved]).split('\n')
    .some(line => line.startsWith('-\t-\t'))) throw Error('Binary task changes require review');
  return paths;
}

function checkAttributes(repo, revision, paths, index) {
  const env = { ...process.env, GIT_INDEX_FILE: index,
    GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null' };
  git(repo, ['read-tree', revision], { env });
  const attributes = git(repo, ['check-attr', '--cached', '-z', 'merge', 'filter',
    'working-tree-encoding', 'ident', 'text', 'eol', '--stdin'], {
    env, input: paths.join('\0') + '\0',
  }).split('\0');
  for (let i = 0; i < attributes.length - 1; i += 3) {
    if (attributes[i + 2] !== 'unspecified') throw Error('Task attributes need explicit review');
  }
}

export function prepareCandidate({ repo, approved, directory, zeroContextPaths = [], validateZeroContext }) {
  repo = resolve(repo);
  assertClean(repo);
  if (git(repo, ['branch', '--show-current']).trim() !== 'main') throw Error('Primary must be on main');
  const main = git(repo, ['rev-parse', 'HEAD']).trim();
  approved = git(repo, ['rev-parse', `${approved}^{commit}`]).trim();
  const bases = git(repo, ['merge-base', '--all', main, approved]).trim().split('\n');
  if (bases.length !== 1 || !bases[0]) throw Error('A unique merge base is required');
  const base = bases[0];
  if (base === approved) throw Error('Task is already an ancestor; reconcile existing installation');
  mkdirSync(directory, { recursive: false, mode: 0o700 });
  const paths = supportedChanges(repo, base, approved);
  const zero = paths.filter(path => zeroContextPaths.includes(path));
  if (zero.length && !validateZeroContext) throw Error('Zero-context policy validator required');
  const index = join(directory, 'proof-index');
  checkAttributes(repo, approved, paths, join(directory, 'attributes-a'));
  checkAttributes(repo, main, paths, join(directory, 'attributes-b'));
  let configuredDrivers = '';
  try {
    configuredDrivers = git(repo, ['config', '--local', '--get-regexp', '^merge\\..*\\.driver$'],
      { stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (error) {
    if (error.status !== 1) throw error;
  }
  if (configuredDrivers.trim()) throw Error('Custom merge drivers require review');
  const checkout = join(directory, 'checkout');
  git(repo, ['worktree', 'add', '--detach', checkout, main]);
  git(checkout, ['merge', '--no-commit', '--no-ff', '-s', 'ort', approved]);
  const mergeTree = git(checkout, ['write-tree']).trim();
  git(repo, ['read-tree', main], { env: { ...process.env, GIT_INDEX_FILE: index } });
  const patches = [];
  const groups = [{ context: 3, paths: paths.filter(path => !zero.includes(path)) },
    { context: 0, paths: zero }];
  for (const group of groups) {
    if (!group.paths.length) continue;
    const patch = git(repo, ['diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv',
      '--no-renames', `--unified=${group.context}`, base, approved, '--', ...group.paths]);
    const filename = join(directory, `delta-${group.context}.patch`);
    writeFileSync(filename, patch, { mode: 0o600, flag: 'wx' });
    git(repo, ['apply', '--cached', '--whitespace=nowarn',
      ...(group.context === 0 ? ['--unidiff-zero'] : []), filename], {
      env: { ...process.env, GIT_INDEX_FILE: index },
    });
    patches.push({ context: group.context, paths: group.paths, sha256: digest(patch) });
  }
  const proofTree = git(repo, ['write-tree'], { env: { ...process.env, GIT_INDEX_FILE: index } }).trim();
  if (proofTree !== mergeTree) throw Error('Independent task delta and merge trees differ');
  for (const path of zero) {
    validateZeroContext({ path, base: git(repo, ['show', `${base}:${path}`]),
      approved: git(repo, ['show', `${approved}:${path}`]), main: git(repo, ['show', `${main}:${path}`]),
      candidate: git(repo, ['show', `${mergeTree}:${path}`]) });
  }
  const candidate = git(repo, ['commit-tree', mergeTree, '-p', main, '-p', approved], {
    input: `Kanban integration of approved task ${approved}\n`,
  }).trim();
  const candidateRef = 'refs/kanban/candidates/' + randomUUID();
  git(repo, ['update-ref', candidateRef, candidate, '0'.repeat(40)]);
  git(checkout, ['merge', '--abort']);
  git(checkout, ['checkout', '--detach', candidate]);
  const result = { policy: policyVersion, approved, base, main, candidate, tree: mergeTree,
    checkout, directory, patches, candidateRef, phase: 'proof_verified' };
  verifyCandidate(repo, result);
  return result;
}

export function verifyCandidate(repo, attempt) {
  const parents = git(repo, ['show', '-s', '--format=%P', attempt.candidate]).trim();
  if (parents !== `${attempt.main} ${attempt.approved}`) throw Error('Candidate parents changed');
  if (git(repo, ['rev-parse', `${attempt.candidate}^{tree}`]).trim() !== attempt.tree) {
    throw Error('Candidate tree changed');
  }
  if (git(attempt.checkout, ['rev-parse', 'HEAD']).trim() !== attempt.candidate) throw Error('Candidate checkout changed');
  assertClean(attempt.checkout);
  for (const patch of attempt.patches) {
    const path = join(attempt.directory, `delta-${patch.context}.patch`);
    if (!lstatSync(path).isFile() || digest(readFileSync(path)) !== patch.sha256) throw Error('Task patch changed');
  }
}

export function cleanupCandidate(repo, attempt) {
  if (resolve(attempt.checkout) !== join(resolve(attempt.directory), 'checkout')) throw Error('Unexpected candidate checkout path');
  const entries = git(repo, ['worktree', 'list', '--porcelain']).split('\n\n');
  const entry = entries.find(value => value.startsWith('worktree ' + attempt.checkout + '\n'));
  if (entry) {
    if (!entry.split('\n').includes('HEAD ' + attempt.candidate)) throw Error('Candidate cleanup identity changed');
    try {
      if (!lstatSync(attempt.checkout).isDirectory()) throw Error('Candidate checkout is not a directory');
      assertClean(attempt.checkout);
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    git(repo, ['worktree', 'remove', '--force', attempt.checkout]);
  } else {
    try { lstatSync(attempt.checkout); throw Error('Unregistered candidate files require inspection'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  if (attempt.candidateRef) {
    if (!attempt.candidateRef.startsWith('refs/kanban/candidates/')) throw Error('Unexpected candidate ref');
    let value;
    try { value = git(repo, ['rev-parse', '--verify', attempt.candidateRef]).trim(); }
    catch (error) { if (error.status !== 128) throw error; }
    if (value) {
      if (value !== attempt.candidate) throw Error('Candidate ref changed');
      git(repo, ['update-ref', '-d', attempt.candidateRef, attempt.candidate]);
    }
  }
}
