// evals/watch-once.test.mjs
// Codex Phase 2 pass 2 P2: lock the watch --once contract with a real
// behavioral test, so it can't silently regress to "exit on first event"
// or "long-poll forever".
//
// Contract being tested:
//   T1: --once exits with `once_event_emitted` after AT LEAST ONE peer
//       event is emitted in a poll iteration.
//   T2: when the poll batch contains multiple peer events, --once emits
//       ALL of them before exit (drains the batch).
//   T3: --once skips own-role events (filter applies before exit gate).
//   T4: state.last_event_cursor is persisted to the max id seen, even
//       across exit (so subsequent --once resumes correctly).
//   T5: subsequent --once invocation from the persisted cursor only emits
//       new peer events (not the ones already drained in T2).

process.env.CLAWROOM_STATE_DIR = `/tmp/clawroom-v4-watch-once-test-${process.pid}`;

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const { watchEvents } = await import('../skill/lib/watch.mjs');
const { initState, readState } = await import('../skill/lib/state.mjs');
const { STATE_DIR } = await import('../skill/lib/types.mjs');

const TOKEN = 'host_watch_once_test_token';

/** Start a mock relay that serves a fixed batch on the first /events poll, then empties. */
function startMockRelay({ events, mutualCloseAfter = false }) {
  let firstPoll = true;
  let mutuallyClosed = false;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (!req.headers['authorization']?.startsWith('Bearer ')) {
      res.writeHead(401); res.end('{}'); return;
    }
    if (/\/events$/.test(url.pathname)) {
      const after = Number(url.searchParams.get('after') || '-1');
      const due = firstPoll ? events.filter((e) => e.id > after) : [];
      firstPoll = false;
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(due));
      return;
    }
    if (/\/join$/.test(url.pathname)) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({
        close_state: { host_closed: false, guest_closed: mutuallyClosed, closed: mutuallyClosed },
      }));
      return;
    }
    res.writeHead(404); res.end('{}');
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const port = /** @type {any} */ (server.address()).port;
      resolve({ server, url: `http://127.0.0.1:${port}` });
    });
  });
}

function cleanState(room_id, role) {
  try { fs.unlinkSync(path.join(STATE_DIR, `${room_id}-${role}.state.json`)); } catch {}
}

test('watch --once T1+T2+T3: drains full peer-event batch and exits with once_event_emitted', async () => {
  const room_id = `t_watch_once_T123_${process.pid}`;
  cleanState(room_id, 'host');
  initState({ room_id, role: 'host', host_token: TOKEN });

  const { server, url } = await startMockRelay({
    events: [
      { id: 0, from: 'host',  kind: 'message', ts: 1 },  // own — must be filtered
      { id: 1, from: 'guest', kind: 'message', ts: 2 },  // peer #1
      { id: 2, from: 'guest', kind: 'message', ts: 3 },  // peer #2 — must also drain
    ],
  });

  /** @type {string[]} */
  const lines = [];
  const result = await watchEvents({
    relay: url,
    room_id,
    role: 'host',
    token: TOKEN,
    once: true,
    emit: (l) => lines.push(l),
  });
  server.close();

  assert.equal(result.exit_reason, 'once_event_emitted',
    `expected once_event_emitted, got ${result.exit_reason}`);

  // Should have emitted BOTH peer events, not just the first.
  const eventLines = lines.filter((l) => l.startsWith('event_available '));
  assert.equal(eventLines.length, 2,
    `expected 2 peer event lines (batch drain), got ${eventLines.length}: ${JSON.stringify(lines)}`);

  // No own-role event should appear.
  for (const line of lines) {
    assert.ok(!line.includes('"from":"host"'), `own role leaked: ${line}`);
  }
});

test('watch --once T4: cursor persisted to max id in the batch', async () => {
  const room_id = `t_watch_once_T4_${process.pid}`;
  cleanState(room_id, 'host');
  initState({ room_id, role: 'host', host_token: TOKEN });

  const { server, url } = await startMockRelay({
    events: [
      { id: 7, from: 'guest', kind: 'message', ts: 1 },
      { id: 9, from: 'guest', kind: 'message', ts: 2 },
    ],
  });

  await watchEvents({
    relay: url, room_id, role: 'host', token: TOKEN, once: true,
    emit: () => {},
  });
  server.close();

  const stateAfter = readState(room_id, 'host');
  assert.equal(stateAfter.last_event_cursor, 9,
    `expected cursor=9 (max id in batch), got ${stateAfter.last_event_cursor}`);
});

test('watch --once T5: subsequent invocation resumes from persisted cursor', async () => {
  const room_id = `t_watch_once_T5_${process.pid}`;
  cleanState(room_id, 'host');
  initState({ room_id, role: 'host', host_token: TOKEN });

  // First call: drain ids 1-2.
  const first = await startMockRelay({
    events: [
      { id: 1, from: 'guest', kind: 'message', ts: 1 },
      { id: 2, from: 'guest', kind: 'message', ts: 2 },
    ],
  });
  /** @type {string[]} */
  const firstLines = [];
  await watchEvents({
    relay: first.url, room_id, role: 'host', token: TOKEN, once: true,
    emit: (l) => firstLines.push(l),
  });
  first.server.close();
  assert.equal(firstLines.filter((l) => l.startsWith('event_available ')).length, 2);

  // Second call: relay has events 3+4. Only those should be emitted.
  const second = await startMockRelay({
    events: [
      { id: 1, from: 'guest', kind: 'message', ts: 1 },  // old, < cursor
      { id: 2, from: 'guest', kind: 'message', ts: 2 },  // old, < cursor
      { id: 3, from: 'guest', kind: 'message', ts: 3 },  // new
      { id: 4, from: 'guest', kind: 'message', ts: 4 },  // new
    ],
  });
  /** @type {string[]} */
  const secondLines = [];
  await watchEvents({
    relay: second.url, room_id, role: 'host', token: TOKEN, once: true,
    emit: (l) => secondLines.push(l),
  });
  second.server.close();

  const eventLines = secondLines.filter((l) => l.startsWith('event_available '));
  assert.equal(eventLines.length, 2,
    `second --once should emit only the NEW events (id 3,4), got ${eventLines.length}`);
  for (const line of eventLines) {
    // None of the old (already-drained) ids should show up.
    assert.ok(!line.includes('"id":1'), `old event id=1 leaked: ${line}`);
    assert.ok(!line.includes('"id":2'), `old event id=2 leaked: ${line}`);
  }
});
