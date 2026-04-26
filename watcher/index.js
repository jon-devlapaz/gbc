#!/usr/bin/env node
import { homedir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync, readdirSync } from 'node:fs';
import { watch as ccsniffWatch } from 'ccsniff';
import { loadState, saveState } from './state.js';
import { parseJsonlSince } from './parser.js';
import { Poster } from './poster.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

const ENDPOINT = process.env.CCT_INGEST_URL ?? 'http://127.0.0.1:7878/ingest/usage';
const PROJECTS_DIR = process.env.CCT_PROJECTS_DIR ?? join(homedir(), '.claude', 'projects');
const STATE_PATH = process.env.CCT_STATE_PATH ?? join(__dirname, '..', 'data', '.watcher-state.json');

const state = loadState(STATE_PATH);
const poster = new Poster({ endpoint: ENDPOINT });

let pendingSave = false;
function scheduleSave() {
  if (pendingSave) return;
  pendingSave = true;
  setTimeout(() => { saveState(STATE_PATH, state); pendingSave = false; }, 500);
}

function listAllJsonl(dir) {
  const out = [];
  function walk(d) {
    let entries;
    try { entries = readdirSync(d, { withFileTypes: true }); }
    catch { return; }
    for (const e of entries) {
      const p = join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.isFile() && p.endsWith('.jsonl')) out.push(p);
    }
  }
  walk(dir);
  return out;
}

async function processFile(path) {
  const offset = state.offsets[path] ?? 0;
  const { records, newOffset } = parseJsonlSince(path, offset);
  if (records.length > 0) {
    try {
      await poster.send(records);
      state.offsets[path] = newOffset;
      scheduleSave();
    } catch (err) {
      console.error(`[watcher] send failed for ${path}: ${err.message}`);
      for (const r of records) await poster.enqueue(r);
    }
  } else {
    state.offsets[path] = newOffset;
    scheduleSave();
  }
}

async function backfill() {
  const files = listAllJsonl(PROJECTS_DIR);
  console.log(`[watcher] backfill: scanning ${files.length} JSONL files`);
  const start = Date.now();
  let n = 0;
  for (const f of files) {
    await processFile(f);
    n += 1;
    if (n % 50 === 0) console.log(`[watcher] backfill: ${n}/${files.length}`);
  }
  console.log(`[watcher] backfill complete: ${n} files in ${(Date.now() - start) / 1000}s`);
}

/**
 * Extract the JSONL file path from a ccsniff event payload.
 *
 * ccsniff v1.0.17 event shapes (confirmed by reading source):
 *   conversation_created  → { conversation: { id, title, cwd, file, parentSid, isSubagent }, timestamp }
 *   streaming_start       → { conversationId, conversation, timestamp }
 *   streaming_complete    → { conversationId, conversation, seq, timestamp }
 *   streaming_progress    → { conversationId, conversation, block, role, seq, timestamp }
 *
 * The JSONL path lives at `conversation.file` (NOT `.path` or `.jsonlPath`).
 * The plan assumed different key names — this implementation uses the real shape.
 * The fallback chain below handles both the real shape and any hypothetical future changes.
 */
function extractPath(arg) {
  if (!arg) return null;
  if (typeof arg === 'string') return arg.endsWith('.jsonl') ? arg : null;
  // Real ccsniff v1.0.17 key: conversation.file
  return arg.conversation?.file
      ?? arg.conversation?.path
      ?? arg.conversation?.jsonlPath
      ?? arg.path
      ?? arg.jsonlPath
      ?? arg.file
      ?? null;
}

async function liveLoop() {
  if (!existsSync(PROJECTS_DIR)) {
    console.error(`[watcher] PROJECTS_DIR does not exist: ${PROJECTS_DIR}`);
    process.exit(1);
  }

  // watch() calls new JsonlWatcher(projectsDir).start() — already running on return.
  let watcher;
  try {
    watcher = ccsniffWatch(PROJECTS_DIR);
  } catch (err) {
    console.error(`[watcher] failed to start ccsniff: ${err.message}`);
    process.exit(1);
  }

  const onEvent = async (arg) => {
    const path = extractPath(arg);
    if (path) await processFile(path);
  };

  // ccsniff v1.0.17 emits: conversation_created, streaming_start, streaming_progress,
  // streaming_complete, streaming_error, error.
  // We care about events that signal new data was written; streaming_complete and
  // conversation_created are the highest-signal ones. streaming_start fires before
  // data lands so it's lower value, but subscribing costs nothing.
  for (const evt of ['streaming_complete', 'streaming_start', 'conversation_created']) {
    watcher.on(evt, onEvent);
  }

  watcher.on('error', (err) => {
    console.error('[watcher] ccsniff error:', err);
  });

  // Periodic flush of pending queue (in case FastAPI was down)
  setInterval(() => { poster.flush().catch(() => {}); }, 5000);

  // Periodic safety re-scan in case ccsniff missed an event
  setInterval(async () => {
    const files = listAllJsonl(PROJECTS_DIR);
    for (const f of files) await processFile(f);
  }, 30_000);

  process.on('SIGINT', () => {
    console.log('\n[watcher] stopping');
    try { watcher.stop(); } catch {}
    saveState(STATE_PATH, state);
    process.exit(0);
  });

  console.log(`[watcher] live: watching ${PROJECTS_DIR}`);
  console.log(`[watcher] posting to ${ENDPOINT}`);
}

await backfill();
await liveLoop();
