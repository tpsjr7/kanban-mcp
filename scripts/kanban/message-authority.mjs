/** Exact source-message inspection; caller metadata alone is not human authority. */
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { command, opencode, desktopOwnsChat, codexHasWriter } from './transport.mjs';

const bridge = name => fileURLToPath(new URL(name, import.meta.url));
export function verifyHumanMessage(request, dependencies = {}) {
  const io = { command, opencode, desktopOwnsChat, codexHasWriter, ...dependencies };
  let message;
  let route;
  if (request.client === 'codex') {
    let history;
    if (io.desktopOwnsChat(request.chat_id) || !io.codexHasWriter(request.chat_id)) {
      history = JSON.parse(io.command('python', [bridge('./desktop_bridge.py'), 'history', request.chat_id]));
      if (history.id !== request.chat_id) throw Error('Codex history target mismatch');
      message = history.messages?.find(row => row.id === request.message_id);
      route = 'codex-desktop';
    } else {
      history = JSON.parse(io.command('python', [bridge('./codex_bridge.py'), 'read', request.chat_id,
        '--turns', '20', '--text-limit', '16001']));
      if (history.id !== request.chat_id) throw Error('Codex history target mismatch');
      message = history.turns?.flatMap(turn => (turn.messages || []).map(row => ({ ...row, source_time: turn.startedAt }))).find(row => row.id === request.message_id);
      route = 'codex-cli';
    }
  } else if (request.client === 'opencode') {
    const session = encodeURIComponent(request.chat_id);
    const id = encodeURIComponent(request.message_id);
    // Exact-message endpoint, never the newest session or message.
    const identity = io.opencode('GET', `/api/session/${session}`).data;
    if (identity?.id !== request.chat_id) throw Error('OpenCode session mismatch');
    message = io.opencode('GET', `/api/session/${session}/message/${id}`).data;
    route = 'opencode';
  } else throw Error('Unsupported client');
  const text = message?.text ?? message?.content?.filter(part => part.type === 'text')
    .map(part => part.text).join('\n');
  if (!message || message.id !== request.message_id || !['user', 'userMessage'].includes(message.type)
      || message.synthetic || message.metadata?.synthetic
      || message.content?.some(part => part.synthetic)
      || text !== request.request_text || typeof text !== 'string' || text.length > 16000
      || /<(codex_delegation|heartbeat|system-reminder|tool_result)\b/i.test(text)) {
    throw Error('Exact human user message is missing, synthetic, truncated or changed');
  }
  let sourceTime = message.source_time ?? message.time?.created;
  if (typeof sourceTime === 'number' && sourceTime > 1e12) sourceTime /= 1000;
  if (request.plan_saved_at) {
    const minimum = Date.parse(request.plan_saved_at.replace(' ', 'T') + 'Z') / 1000;
    if (!Number.isFinite(sourceTime) || !Number.isFinite(minimum) || sourceTime < minimum) {
      throw Error('Human request predates the current saved plan or has no verified time');
    }
  }
  return { source_time: sourceTime, client: request.client, chat_id: request.chat_id, message_id: request.message_id,
    role: 'user', route, sha256: createHash('sha256').update(text).digest('hex'),
    verified_at: new Date().toISOString() };
}

