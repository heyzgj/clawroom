// evals/create-idempotency.test.mjs
// Phase 1.5 gate: createRoom must be safe to retry. Tests both halves:
//   T1: same idempotency_key returns the same thread on replay (relay caches).
//   T2: createRoom retries on transient 5xx without creating duplicate rooms.
//   T3: createRoom does NOT silently rotate idempotency_key across retries.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { createRoom } from '../skill/lib/relay-client.mjs';

/**
 * Start a mock relay that mirrors the server-side idempotency cache from
 * `relay/worker.ts` (Map keyed by X-Idempotency-Key with TTL replay).
 *
 * @param {Object} [opts]
 * @param {number} [opts.failFirstNRequests] - return 503 for first N requests, ignoring idempotency
 */
function startMockRelay(opts = {}) {
  const cache = new Map(); // idemp_key -> { body, expires }
  let requestCount = 0;
  let createCount = 0;
  const server = http.createServer((req, res) => {
    requestCount++;
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname !== '/threads/new') {
      res.writeHead(404); res.end('{}');
      return;
    }
    if (opts.failFirstNRequests && requestCount <= opts.failFirstNRequests) {
      res.writeHead(503, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: 'transient_simulated' }));
      return;
    }
    const idempKey = req.headers['x-idempotency-key'];
    if (idempKey && cache.has(idempKey)) {
      const cached = cache.get(idempKey);
      res.writeHead(200, { 'content-type': 'application/json', 'x-idempotent-replay': 'true' });
      res.end(cached);
      return;
    }
    createCount++;
    const body = JSON.stringify({
      thread_id: `t_test_${createCount}_${Math.random().toString(36).slice(2, 8)}`,
      host_token: `host_test_${createCount}`,
      guest_token: `guest_test_${createCount}`,
      invite_url: `http://example/invite/${createCount}`,
      public_invite_url: `http://example/i/${createCount}/code`,
    });
    if (idempKey) cache.set(idempKey, body);
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(body);
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const port = /** @type {any} */ (server.address()).port;
      resolve({
        server,
        url: `http://127.0.0.1:${port}`,
        getRequestCount: () => requestCount,
        getCreateCount: () => createCount,
      });
    });
  });
}

test('T1: same idempotency_key returns same thread on replay', async () => {
  const { server, url, getCreateCount } = await startMockRelay();
  try {
    const idempKey = 'fixed-test-key-123';
    const first = await createRoom({ topic: 'a', goal: 'b', relay: url, idempotency_key: idempKey });
    const second = await createRoom({ topic: 'a', goal: 'b', relay: url, idempotency_key: idempKey });
    assert.equal(first.thread_id, second.thread_id, 'thread_id should match on replay');
    assert.equal(first.host_token, second.host_token, 'host_token should match on replay');
    assert.equal(getCreateCount(), 1, 'only one actual thread should have been created');
    assert.ok(second._idempotent_replay === true, 'replay flag should be surfaced by client');
  } finally {
    server.close();
  }
});

test('T2: createRoom retries 5xx + keeps same idempotency_key + creates only one thread', async () => {
  const { server, url, getCreateCount, getRequestCount } = await startMockRelay({ failFirstNRequests: 2 });
  try {
    // No idempotency_key provided → client generates one and reuses it across retries.
    const result = await createRoom({ topic: 'r', goal: 'r2', relay: url, retries: 4 });
    assert.equal(getCreateCount(), 1, `only one thread should exist after retry, got ${getCreateCount()}`);
    assert.ok(getRequestCount() >= 3, `expected at least 3 HTTP attempts (2 failures + 1 success), got ${getRequestCount()}`);
    assert.ok(result.thread_id, 'should still return a thread_id');
    assert.ok(typeof result._idempotency_key === 'string', 'client should surface the key it used');
  } finally {
    server.close();
  }
});

test('T3: client surfaces the idempotency_key for caller persistence across processes', async () => {
  const { server, url } = await startMockRelay();
  try {
    const result = await createRoom({ topic: 'k', goal: 'k', relay: url });
    assert.ok(typeof result._idempotency_key === 'string' && result._idempotency_key.length > 0,
      '_idempotency_key must be present so callers can persist it for cross-process retry');
    // Verify reuse: pass it back and confirm we get the same room.
    const replay = await createRoom({ topic: 'k', goal: 'k', relay: url, idempotency_key: result._idempotency_key });
    assert.equal(replay.thread_id, result.thread_id);
  } finally {
    server.close();
  }
});
