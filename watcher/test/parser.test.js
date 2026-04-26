import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseJsonlSince, parentSessionFromPath, sessionIdFromPath } from '../parser.js';

function tmp() { return mkdtempSync(join(tmpdir(), 'parse-test-')); }

const sample = (overrides = {}) => ({
  uuid: 'm-1',
  type: 'assistant',
  timestamp: '2026-04-25T10:00:00Z',
  message: {
    model: 'claude-opus-4-7',
    usage: {
      input_tokens: 100,
      output_tokens: 50,
      cache_read_input_tokens: 1000,
      cache_creation_input_tokens: 2000,
      cache_creation: { ephemeral_5m_input_tokens: 500, ephemeral_1h_input_tokens: 1500 },
      service_tier: 'standard',
    },
  },
  ...overrides,
});

test('sessionIdFromPath extracts top-level uuid', () => {
  assert.equal(
    sessionIdFromPath('/Users/x/.claude/projects/-Users-x/abc-123.jsonl'),
    'abc-123'
  );
});

test('sessionIdFromPath extracts subagent uuid (own id, not parent)', () => {
  assert.equal(
    sessionIdFromPath('/Users/x/.claude/projects/-Users-x/parent-uuid/subagents/agent-deadbeef.jsonl'),
    'agent-deadbeef'
  );
});

test('parentSessionFromPath returns null for top-level', () => {
  assert.equal(
    parentSessionFromPath('/Users/x/.claude/projects/-Users-x/abc-123.jsonl'),
    null
  );
});

test('parentSessionFromPath returns parent uuid for subagents', () => {
  assert.equal(
    parentSessionFromPath('/Users/x/.claude/projects/-Users-x/parent-uuid/subagents/agent-deadbeef.jsonl'),
    'parent-uuid'
  );
});

test('parseJsonlSince yields one record from one assistant line', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    writeFileSync(path, JSON.stringify(sample()) + '\n');
    const out = parseJsonlSince(path, 0);
    assert.equal(out.records.length, 1);
    const r = out.records[0];
    assert.equal(r.message_uuid, 'm-1');
    assert.equal(r.model, 'claude-opus-4-7');
    assert.equal(r.input_tokens, 100);
    assert.equal(r.output_tokens, 50);
    assert.equal(r.cache_creation_5m_tokens, 500);
    assert.equal(r.cache_creation_1h_tokens, 1500);
    assert.equal(r.cache_read_tokens, 1000);
    assert.equal(r.service_tier, 'standard');
    assert.equal(r.session_id, 'sess-1');
    assert.equal(r.parent_session_id, null);
    assert.equal(out.newOffset, Buffer.byteLength(JSON.stringify(sample()) + '\n', 'utf8'));
  } finally { rmSync(dir, { recursive: true }); }
});

test('parseJsonlSince skips non-assistant and missing-usage lines', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    const lines = [
      JSON.stringify({ type: 'user', message: { content: 'hi' } }),
      JSON.stringify(sample({ uuid: 'm-1' })),
      JSON.stringify({ type: 'assistant', uuid: 'm-no-usage', message: { model: 'm', content: [] } }),
      JSON.stringify(sample({ uuid: 'm-2' })),
    ];
    writeFileSync(path, lines.join('\n') + '\n');
    const out = parseJsonlSince(path, 0);
    assert.equal(out.records.length, 2);
    assert.equal(out.records[0].message_uuid, 'm-1');
    assert.equal(out.records[1].message_uuid, 'm-2');
  } finally { rmSync(dir, { recursive: true }); }
});

test('parseJsonlSince resumes from offset', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    const line1 = JSON.stringify(sample({ uuid: 'm-1' })) + '\n';
    const line2 = JSON.stringify(sample({ uuid: 'm-2' })) + '\n';
    writeFileSync(path, line1 + line2);
    const off1 = Buffer.byteLength(line1, 'utf8');
    const out = parseJsonlSince(path, off1);
    assert.equal(out.records.length, 1);
    assert.equal(out.records[0].message_uuid, 'm-2');
  } finally { rmSync(dir, { recursive: true }); }
});

test('parseJsonlSince skips malformed lines gracefully', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    const lines = [
      'not-json',
      JSON.stringify(sample({ uuid: 'm-1' })),
    ];
    writeFileSync(path, lines.join('\n') + '\n');
    const out = parseJsonlSince(path, 0);
    assert.equal(out.records.length, 1);
  } finally { rmSync(dir, { recursive: true }); }
});

test('parseJsonlSince treats truncated file (offset > size) as reset', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    writeFileSync(path, JSON.stringify(sample({ uuid: 'm-1' })) + '\n');
    // Pretend we had read further than the file is now
    const out = parseJsonlSince(path, 99999);
    assert.equal(out.records.length, 1);
    assert.equal(out.records[0].message_uuid, 'm-1');
  } finally { rmSync(dir, { recursive: true }); }
});
