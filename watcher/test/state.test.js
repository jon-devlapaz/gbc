import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadState, saveState } from '../state.js';

function tmp() {
  return mkdtempSync(join(tmpdir(), 'state-test-'));
}

test('loadState returns empty object when file missing', () => {
  const dir = tmp();
  try {
    const s = loadState(join(dir, 'state.json'));
    assert.deepEqual(s, { offsets: {} });
  } finally { rmSync(dir, { recursive: true }); }
});

test('saveState then loadState roundtrip', () => {
  const dir = tmp();
  const path = join(dir, 'state.json');
  try {
    saveState(path, { offsets: { '/a.jsonl': 1024, '/b.jsonl': 0 } });
    const s = loadState(path);
    assert.equal(s.offsets['/a.jsonl'], 1024);
    assert.equal(s.offsets['/b.jsonl'], 0);
  } finally { rmSync(dir, { recursive: true }); }
});

test('loadState handles corrupt file by returning empty', () => {
  const dir = tmp();
  const path = join(dir, 'state.json');
  try {
    writeFileSync(path, 'not json');
    const s = loadState(path);
    assert.deepEqual(s, { offsets: {} });
  } finally { rmSync(dir, { recursive: true }); }
});
