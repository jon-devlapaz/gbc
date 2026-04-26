import { existsSync, statSync, openSync, readSync, closeSync } from 'node:fs';
import { basename, sep } from 'node:path';

/**
 * Extract session_id from JSONL path.
 * Top-level: ~/.claude/projects/<dir>/<uuid>.jsonl → <uuid>
 * Subagent:  ~/.claude/projects/<dir>/<parent>/subagents/agent-XXX.jsonl → agent-XXX
 */
export function sessionIdFromPath(path) {
  return basename(path, '.jsonl');
}

/**
 * Extract parent_session_id (or null).
 * If path is .../<parent>/subagents/<agent>.jsonl, parent is "<parent>".
 */
export function parentSessionFromPath(path) {
  const parts = path.split(sep);
  const subIdx = parts.indexOf('subagents');
  if (subIdx > 0) return parts[subIdx - 1];
  return null;
}

/**
 * Read JSONL bytes from `startOffset` to end-of-file.
 * Yield usage records for each `type=assistant` line with `message.usage`.
 *
 * Returns { records: [...], newOffset: number }.
 *
 * Truncation handling: if `startOffset` > current file size, we treat the
 * file as rotated and re-read from 0.
 */
export function parseJsonlSince(path, startOffset) {
  if (!existsSync(path)) return { records: [], newOffset: startOffset };
  const size = statSync(path).size;
  let from = startOffset;
  if (from > size) from = 0;        // truncated/rotated
  if (from === size) return { records: [], newOffset: size };

  const fd = openSync(path, 'r');
  try {
    const len = size - from;
    const buf = Buffer.alloc(len);
    readSync(fd, buf, 0, len, from);
    const text = buf.toString('utf8');
    const lines = text.split('\n');
    // last element is '' if the file ends in \n, or a partial line if not
    const records = [];
    let consumed = 0;
    for (let i = 0; i < lines.length - 1; i++) {
      const line = lines[i];
      consumed += Buffer.byteLength(line, 'utf8') + 1;  // +1 for \n
      if (!line) continue;
      const rec = parseLine(line, path);
      if (rec) records.push(rec);
    }
    // partial trailing line — leave it for next read
    return { records, newOffset: from + consumed };
  } finally {
    closeSync(fd);
  }
}

function parseLine(line, path) {
  let obj;
  try { obj = JSON.parse(line); } catch { return null; }
  if (obj.type !== 'assistant') return null;
  const msg = obj.message;
  if (!msg || !msg.usage) return null;
  const u = msg.usage;
  const cc = u.cache_creation || {};
  return {
    message_uuid: obj.uuid,
    session_id: sessionIdFromPath(path),
    parent_session_id: parentSessionFromPath(path),
    jsonl_path: path,
    ts: obj.timestamp,
    model: msg.model,
    service_tier: u.service_tier ?? null,
    input_tokens: u.input_tokens ?? 0,
    output_tokens: u.output_tokens ?? 0,
    cache_creation_5m_tokens: cc.ephemeral_5m_input_tokens ?? 0,
    cache_creation_1h_tokens: cc.ephemeral_1h_input_tokens ?? 0,
    cache_read_tokens: u.cache_read_input_tokens ?? 0,
  };
}
