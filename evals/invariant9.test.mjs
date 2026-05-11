// evals/invariant9.test.mjs
// Release gate for invariant 9: the watch helper must never emit, log, or
// persist message body content. Three failure modes are covered:
//
//   T1 (happy path): /events is well-formed; watch emits only metadata.
//   T2 (relay regression): /events leaks `text` field; watch must reject
//      and exit with invariant9_violation. Body bytes must not appear in
//      stdout or in the state file.
//   T3 (close + mutual close): /events emits peer close; watch must switch
//      to polling /join and exit with mutual_close.
//
// Per the v4 plan (Phase 1 done-criteria), this test must pass before
// skill/SKILL.md is allowed to reference `clawroom watch`.

process.env.CLAWROOM_STATE_DIR = `/tmp/clawroom-v4-invariant9-test-${process.pid}`;

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

// Dynamic imports so CLAWROOM_STATE_DIR is captured at module load.
const { watchEvents } = await import('../skill/lib/watch.mjs');
const { initState, readState } = await import('../skill/lib/state.mjs');
const { STATE_DIR } = await import('../skill/lib/types.mjs');

const TEST_TOKEN = 'host_invariant9_test_token';
const LEAKED_BODY_SENTINEL = 'BODY_SENTINEL_LEAKED_PEER_TEXT_SHOULD_NEVER_APPEAR';

/**
 * Start a mock relay HTTP server. Routes:
 *   GET /threads/:id/events?after=N -> serves from event queue, marks served events as drained.
 *   GET /threads/:id/join          -> serves close_state from current state.
 *
 * @param {Object} args
 * @param {Array<{id:number, from:string, kind:string, ts:number, text?:string}>} args.events
 * @param {() => {host_closed: boolean, guest_closed: boolean, closed: boolean}} args.closeState
 */
function startMockRelay({ events, closeState }) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const auth = req.headers['authorization'] || '';
    if (!auth.startsWith('Bearer ')) {
      res.writeHead(401); res.end('{}');
      return;
    }
    const eventsMatch = /\/threads\/[^/]+\/events$/.exec(url.pathname);
    const joinMatch = /\/threads\/[^/]+\/join$/.exec(url.pathname);
    if (eventsMatch) {
      const after = Number(url.searchParams.get('after') || '-1');
      const due = events.filter((e) => e.id > after);
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(due));
      return;
    }
    if (joinMatch) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ close_state: closeState() }));
      return;
    }
    res.writeHead(404); res.end('{}');
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const addr = /** @type {any} */ (server.address());
      resolve({ server, url: `http://127.0.0.1:${addr.port}` });
    });
  });
}

function cleanState(room_id, role) {
  try { fs.unlinkSync(path.join(STATE_DIR, `${room_id}-${role}.state.json`)); } catch {}
}

test('invariant9 T1 happy path: watch emits only metadata, no text anywhere', async () => {
  const room_id = `t_invariant9_T1_${process.pid}`;
  cleanState(room_id, 'host');
  initState({ room_id, role: 'host', host_token: TEST_TOKEN });

  let mutualClose = false;
  const { server, url } = await startMockRelay({
    events: [
      { id: 0, from: 'host', kind: 'message', ts: 1 },     // own echo — watch should skip
      { id: 1, from: 'guest', kind: 'message', ts: 2 },
      { id: 2, from: 'guest', kind: 'close', ts: 3 },
    ],
    closeState: () => ({ host_closed: true, guest_closed: mutualClose, closed: mutualClose }),
  });
  setTimeout(() => { mutualClose = true; }, 50);

  /** @type {string[]} */
  const lines = [];
  const result = await watchEvents({
    relay: url,
    room_id,
    role: 'host',
    token: TEST_TOKEN,
    emit: (l) => lines.push(l),
  });
  server.close();

  // Watch reached mutual close.
  assert.equal(result.exit_reason, 'mutual_close', `expected mutual_close, got ${result.exit_reason}`);
  assert.equal(result.closed_mutually, true);

  // Watch emitted exactly: event_available for guest message, close_available for guest close, mutual_close marker.
  assert.ok(lines.some((l) => l.startsWith('event_available ')), 'expected event_available line');
  assert.ok(lines.some((l) => l.startsWith('close_available ')), 'expected close_available line');
  assert.ok(lines.includes('mutual_close'), 'expected mutual_close marker');

  // Watch did NOT echo own messages.
  for (const line of lines) {
    assert.ok(!line.includes('"from":"host"'), `own role leaked: ${line}`);
  }

  // No line contains text-like keys.
  for (const line of lines) {
    assert.ok(!/"text"\s*:/.test(line), `text field leaked into stdout: ${line}`);
  }

  // State file does not contain message text.
  const stateRaw = fs.readFileSync(path.join(STATE_DIR, `${room_id}-host.state.json`), 'utf8');
  assert.ok(!stateRaw.includes(LEAKED_BODY_SENTINEL), 'sentinel should never appear in state file');
  assert.ok(!/"text"\s*:/.test(stateRaw), 'state file must not record message text');
});

test('invariant9 T2 hostile relay: events with text field must be rejected', async () => {
  const room_id = `t_invariant9_T2_${process.pid}`;
  cleanState(room_id, 'host');
  initState({ room_id, role: 'host', host_token: TEST_TOKEN });

  const { server, url } = await startMockRelay({
    events: [
      // HOSTILE: relay leaked text. makeWatchEvent must reject.
      { id: 1, from: 'guest', kind: 'message', ts: 1, text: LEAKED_BODY_SENTINEL },
    ],
    closeState: () => ({ host_closed: false, guest_closed: false, closed: false }),
  });

  /** @type {string[]} */
  const lines = [];
  const result = await watchEvents({
    relay: url,
    room_id,
    role: 'host',
    token: TEST_TOKEN,
    emit: (l) => lines.push(l),
  });
  server.close();

  // Watch must exit with invariant9_violation, NOT mutual_close.
  assert.equal(result.exit_reason, 'invariant9_violation', `expected invariant9_violation, got ${result.exit_reason}`);

  // The sentinel body bytes must not appear anywhere in emitted lines.
  for (const line of lines) {
    assert.ok(!line.includes(LEAKED_BODY_SENTINEL), `body sentinel leaked into stdout: ${line}`);
    assert.ok(!/"text"\s*:/.test(line), `text field leaked: ${line}`);
  }
  // Must have emitted the violation marker.
  assert.ok(lines.some((l) => l.startsWith('error invariant9_violation')), 'expected invariant9 violation marker');

  // State file must not contain the sentinel either.
  const stateRaw = fs.readFileSync(path.join(STATE_DIR, `${room_id}-host.state.json`), 'utf8');
  assert.ok(!stateRaw.includes(LEAKED_BODY_SENTINEL), 'sentinel must not reach state file');
});

test('invariant9 T3 mutual close switch: after peer close, watch polls /join not /events', async () => {
  const room_id = `t_invariant9_T3_${process.pid}`;
  cleanState(room_id, 'host');
  initState({ room_id, role: 'host', host_token: TEST_TOKEN });

  let eventsCallCount = 0;
  let joinCallCount = 0;
  let closed = false;

  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (!req.headers['authorization']?.startsWith('Bearer ')) {
      res.writeHead(401); res.end('{}'); return;
    }
    if (/\/events$/.test(url.pathname)) {
      eventsCallCount++;
      const after = Number(url.searchParams.get('after') || '-1');
      const all = [{ id: 1, from: 'guest', kind: 'close', ts: 1 }];
      const due = all.filter((e) => e.id > after);
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(due));
      return;
    }
    if (/\/join$/.test(url.pathname)) {
      joinCallCount++;
      // First /join: not yet mutual (race). Subsequent: mutual.
      const cs = joinCallCount >= 2 ? { host_closed: true, guest_closed: true, closed: true } : { host_closed: false, guest_closed: true, closed: false };
      if (joinCallCount >= 2) closed = true;
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ close_state: cs }));
      return;
    }
    res.writeHead(404); res.end('{}');
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${/** @type {any} */ (server.address()).port}`;

  /** @type {string[]} */
  const lines = [];
  const result = await watchEvents({
    relay: url,
    room_id,
    role: 'host',
    token: TEST_TOKEN,
    emit: (l) => lines.push(l),
  });
  server.close();

  assert.equal(result.exit_reason, 'mutual_close');
  assert.ok(joinCallCount >= 2, `expected at least 2 /join polls after peer close, got ${joinCallCount}`);
  // Watch should NOT have called /events after seeing close (it should switch to /join).
  // We saw close on the first /events call. Subsequent polls should be /join.
  // Anything more than 1 /events call after close indicates we did not switch.
  // Watch may legitimately have made 1 /events call to see the close event.
  assert.ok(eventsCallCount <= 1, `watch did not switch to /join after peer close (events called ${eventsCallCount} times)`);
});
