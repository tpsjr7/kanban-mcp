import { readFileSync, statSync, readlinkSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const bridge = fileURLToPath(new URL('./codex_bridge.py', import.meta.url));
export function command(binary, args, options = {}) {
  const result = spawnSync(binary, args, {
    encoding: 'utf8', timeout: 45000, maxBuffer: 8 * 1024 * 1024, ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr || result.stdout || `${binary} failed`);
  return result.stdout;
}
export function opencode(method, path, data) {
  const args = ['api', method, path];
  if (data) args.push('--data', JSON.stringify(data));
  return JSON.parse(command('opencode', args));
}
export function desktopOwnsChat(chatId, io = { statSync, readFileSync, readlinkSync }) {
  if (!/^[a-zA-Z0-9_-]+$/.test(chatId)) return false;
  try {
    const inode = io.statSync(join(homedir(), '.codex/thread-writer-locks', chatId + '.lock')).ino;
    const lock = io.readFileSync('/proc/locks', 'utf8').split('\n').find(line => {
      const fields = line.trim().split(/\s+/);
      return fields[5]?.endsWith(':' + inode) && fields[3] === 'WRITE';
    });
    if (!lock) return false;
    const pid = lock.trim().split(/\s+/)[4];
    const executable = io.readlinkSync(`/proc/${pid}/exe`);
    const args = io.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0');
    return executable.includes('/chatgpt/') && args.includes('app-server');
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ESRCH') return false;
    throw new Error('Cannot verify Codex writer ownership: ' + error.message);
  }
}

export function inspectIdentity(identity) {
  if (identity.client === 'codex') {
    if (desktopOwnsChat(identity.chat_id)) {
      return JSON.parse(command('python', [fileURLToPath(new URL('./desktop_bridge.py', import.meta.url)), 'read', identity.chat_id]));
    }
    const actual = JSON.parse(command('python', [bridge, 'read', identity.chat_id, '--turns', '1', '--text-limit', '100']));
    return { ...actual, route: desktopOwnsChat(identity.chat_id) ? 'native-mcp' : 'cli' };
  }
  const result = opencode('GET', `/api/session/${encodeURIComponent(identity.chat_id)}`).data;
  if (!result || result.id !== identity.chat_id) throw new Error('OpenCode session identity mismatch');
  const active = opencode('GET', '/api/session/active').data || {};
  return { id: result.id, name: result.title, status: { type: active[result.id] ? 'busy' : 'idle' } };
}
export async function resolveIdentity(client, { chat, session, id } = {}) {
  if (!['codex', 'opencode'].includes(client)) throw new Error('Worker client must be codex or opencode');
  let chatId = session || id;
  if (client === 'codex' && !chatId) {
    const source = await readFile(join(homedir(), '.codex/session_index.jsonl'), 'utf8');
    const matches = new Map();
    for (const line of source.trim().split('\n')) {
      const row = JSON.parse(line);
      if (row.thread_name === chat) matches.set(row.id, row);
    }
    if (matches.size !== 1) throw new Error('Chat title is missing or ambiguous; use --id');
    chatId = [...matches.keys()][0];
  }
  if (!chatId) throw new Error('Supply exact chat/session ID');
  const identity = { client, chat_id: chatId, host: 'local' };
  const actual = inspectIdentity(identity);
  if (actual.id !== chatId || chat && actual.name !== chat) throw new Error('Chat identity mismatch');
  return { ...identity, title: actual.name };
}
export function send(identity, prompt, dispatchId) {
  const actual = inspectIdentity(identity);
  if (actual.route === 'native-mcp') {
    if (!['idle', 'notLoaded'].includes(actual.status?.type)) throw new Error('Desktop target busy');
    return JSON.parse(command('python', [fileURLToPath(new URL('./desktop_bridge.py', import.meta.url)), 'send', identity.chat_id, '--message', prompt]));
  }
  if (!['idle', 'notLoaded'].includes(actual.status?.type)) throw new Error('Chat is busy or needs attention');
  if (identity.client === 'opencode') {
    return opencode('POST', `/api/session/${encodeURIComponent(identity.chat_id)}/prompt`, {
      id: 'msg_' + dispatchId.replaceAll('-', ''), text: prompt,
      metadata: { kanban_dispatch_id: dispatchId },
    });
  }
  const output = command('python', [bridge, 'send', identity.chat_id, '--message', prompt]);
  const events = output.trim().split('\n').map(line => JSON.parse(line));
  const accepted = events.find(event => event.event === 'accepted');
  if (!accepted) throw new Error('No delivery receipt; inspect chat before retrying');
  return accepted;
}
