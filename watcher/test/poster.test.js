import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Poster } from '../poster.js';

function makeFakeFetch() {
  const calls = [];
  let mode = 'ok';
  let nextStatus = 200;
  let nextBody = { inserted: 0, skipped: 0 };
  const fetch = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    if (mode === 'refused') throw new Error('ECONNREFUSED');
    if (mode === '400') return { ok: false, status: 400, json: async () => ({ detail: 'bad' }) };
    return { ok: true, status: nextStatus, json: async () => nextBody };
  };
  return {
    fetch,
    calls,
    setMode(m) { mode = m; },
    setNext(status, body) { nextStatus = status; nextBody = body; },
  };
}

test('Poster.send POSTs a single batch to /ingest/usage', async () => {
  const f = makeFakeFetch();
  const p = new Poster({
    endpoint: 'http://127.0.0.1:7878/ingest/usage',
    fetchImpl: f.fetch,
    sleepMs: () => Promise.resolve(),
  });
  await p.send([{ message_uuid: 'u1' }, { message_uuid: 'u2' }]);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].body.events.length, 2);
});

test('Poster batches at batchSize boundary', async () => {
  const f = makeFakeFetch();
  const p = new Poster({ endpoint: 'http://x/ingest', fetchImpl: f.fetch, batchSize: 3, sleepMs: () => Promise.resolve() });
  const recs = Array.from({ length: 7 }, (_, i) => ({ message_uuid: `u${i}` }));
  await p.send(recs);
  assert.equal(f.calls.length, 3);                  // 3 + 3 + 1
  assert.equal(f.calls[0].body.events.length, 3);
  assert.equal(f.calls[2].body.events.length, 1);
});

test('Poster retries on connection refused', async () => {
  const f = makeFakeFetch();
  f.setMode('refused');
  const p = new Poster({
    endpoint: 'http://x/ingest',
    fetchImpl: f.fetch,
    maxRetries: 2,
    sleepMs: () => Promise.resolve(),
  });
  let threw = false;
  try { await p.send([{ message_uuid: 'u1' }]); } catch { threw = true; }
  assert.equal(threw, true);
  assert.equal(f.calls.length, 3);                  // initial + 2 retries
});

test('Poster does not retry on 400 (drops the batch)', async () => {
  const f = makeFakeFetch();
  f.setMode('400');
  const p = new Poster({
    endpoint: 'http://x/ingest',
    fetchImpl: f.fetch,
    maxRetries: 5,
    sleepMs: () => Promise.resolve(),
  });
  // 400 returns peacefully and logs — does not throw, does not retry.
  await p.send([{ message_uuid: 'u1' }]);
  assert.equal(f.calls.length, 1);
});

test('Poster.queue grows under failure and drains on success', async () => {
  const f = makeFakeFetch();
  f.setMode('refused');
  const p = new Poster({
    endpoint: 'http://x/ingest',
    fetchImpl: f.fetch,
    maxRetries: 0,
    queueCap: 10,
    sleepMs: () => Promise.resolve(),
  });
  await p.enqueue({ message_uuid: 'u1' });
  await p.enqueue({ message_uuid: 'u2' });
  assert.equal(p.queueSize(), 2);

  f.setMode('ok');
  await p.flush();
  assert.equal(p.queueSize(), 0);
});

test('Poster queue drops oldest when at cap', async () => {
  const f = makeFakeFetch();
  f.setMode('refused');
  const p = new Poster({
    endpoint: 'http://x/ingest',
    fetchImpl: f.fetch,
    maxRetries: 0,
    queueCap: 2,
    sleepMs: () => Promise.resolve(),
  });
  await p.enqueue({ message_uuid: 'u1' });
  await p.enqueue({ message_uuid: 'u2' });
  await p.enqueue({ message_uuid: 'u3' });          // u1 should be dropped
  assert.equal(p.queueSize(), 2);
  assert.equal(p.peekQueue()[0].message_uuid, 'u2');
});
