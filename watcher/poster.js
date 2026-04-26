const DEFAULT = {
  batchSize: 50,
  maxRetries: 5,
  queueCap: 1000,
  baseBackoffMs: 1000,
  maxBackoffMs: 30000,
};

export class Poster {
  constructor(opts) {
    this.endpoint = opts.endpoint;
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch;
    this.batchSize = opts.batchSize ?? DEFAULT.batchSize;
    this.maxRetries = opts.maxRetries ?? DEFAULT.maxRetries;
    this.queueCap = opts.queueCap ?? DEFAULT.queueCap;
    this.baseBackoffMs = opts.baseBackoffMs ?? DEFAULT.baseBackoffMs;
    this.maxBackoffMs = opts.maxBackoffMs ?? DEFAULT.maxBackoffMs;
    this.sleepMs = opts.sleepMs ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
    this._queue = [];
  }

  queueSize() { return this._queue.length; }
  peekQueue() { return [...this._queue]; }

  async enqueue(record) {
    this._queue.push(record);
    while (this._queue.length > this.queueCap) {
      this._queue.shift();
    }
  }

  async flush() {
    if (this._queue.length === 0) return;
    const drained = this._queue.splice(0, this._queue.length);
    try {
      await this.send(drained);
    } catch (err) {
      // Push back unsent records (oldest first) and respect cap
      this._queue.unshift(...drained);
      while (this._queue.length > this.queueCap) this._queue.shift();
      throw err;
    }
  }

  async send(records) {
    for (let i = 0; i < records.length; i += this.batchSize) {
      const batch = records.slice(i, i + this.batchSize);
      await this._sendBatch(batch);
    }
  }

  async _sendBatch(batch) {
    let attempt = 0;
    let backoff = this.baseBackoffMs;
    while (true) {
      try {
        const r = await this.fetchImpl(this.endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ events: batch }),
        });
        if (r.ok) return await r.json();
        if (r.status >= 400 && r.status < 500) {
          // Validation errors: log + drop, don't retry
          const detail = await r.json().catch(() => ({}));
          console.error(`[poster] ${r.status} dropped ${batch.length} records:`, detail);
          return;
        }
        // 5xx: retry
        throw new Error(`HTTP ${r.status}`);
      } catch (err) {
        if (attempt >= this.maxRetries) throw err;
        attempt += 1;
        await this.sleepMs(backoff);
        backoff = Math.min(backoff * 2, this.maxBackoffMs);
      }
    }
  }
}
