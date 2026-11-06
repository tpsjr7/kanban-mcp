/** Atomic private attempt journal; never infers successful side effects. */
import { openSync, writeFileSync, fsyncSync, closeSync, renameSync, readFileSync, lstatSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomUUID } from 'node:crypto';

export function saveJournal(filename, value) {
  const temporary = filename + '.' + randomUUID();
  const fd = openSync(temporary, 'wx', 0o600);
  try {
    writeFileSync(fd, JSON.stringify(value, null, 2) + '\n');
    fsyncSync(fd);
  } finally { closeSync(fd); }
  renameSync(temporary, filename);
  const parent = openSync(dirname(filename), 'r');
  try { fsyncSync(parent); } finally { closeSync(parent); }
}
export function readJournal(filename) {
  const info = lstatSync(filename);
  if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o077)) {
    throw Error('Attempt journal must be a private owned regular file');
  }
  return JSON.parse(readFileSync(filename, 'utf8'));
}

export function recoveryAction(phase, publication) {
  if (phase === 'publishing') return publication === 'installed' ? 'reconcile-publication' : 'manual';
  if (phase === 'restart_pending') return publication === 'installed' ? 'verify-restart-evidence' : 'manual';
  if (phase === 'cleanup_pending') return publication === 'installed' ? 'verify-cleanup-evidence' : 'manual';
  if (phase === 'quiesce_pending') return publication === 'not_installed' ? 'verify-stopped-readers' : 'manual';
  if (phase === 'proof_verified') return publication === 'not_installed' ? 'explicit-test-retry' : 'manual';
  if (phase === 'cleaned' && publication === 'installed') return 'report-integrated';
  return 'inspect';
}
