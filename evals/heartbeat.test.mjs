// evals/heartbeat.test.mjs
// Phase 6.5 P0 — `clawroom heartbeat` is a DUMB wakeup CHECK. It detects room
// state and says whether to wake the primary agent. It NEVER reads message
// bodies, NEVER advances the read cursor, NEVER posts, NEVER decides business.
//
// These tests drive the real CLI subprocess against a mock relay (so the JSON
// contract + exit codes are exercised end-to-end, like post-cursor.test.mjs),
// plus a direct setWakeLease unit test mirroring the setCursor clobber
// regression in post-cursor.test.mjs.

process.env.CLAWROOM_STATE_DIR = `/tmp/clawroom-v4-heartbeat-test-${process.pid}`;

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CLI = path.resolve(__dirname, '../skill/cli/clawroom');

const { initState, readState, setCursor, setWakeLease, setPendingOwnerAsk } =
  await import('../skill/lib/state.mjs');
const { STATE_DIR } = await import('../skill/lib/types.mjs');

const HOST_TOKEN = 'host_heartbeat_token';

/**
 * Mock relay.
 *   opts.events    — array of {id, from, kind, ts} the /events endpoint serves
 *                    (filtered by ?after=N like the real relay).
 *   opts.closeState— object returned as /join body.close_state, or null.
 *   opts.expired   — when true, every request answers 410 thread_expired
 *                    (mirrors worker.ts expiredResponse for an over-TTL room).
 * Tracks whether /messages was ever hit (it must NOT be — heartbeat is
 * metadata-only) and how many times /events and /join were polled.
 */
function startMockRelay(opts = {}) {
  const state = {
    events: opts.events || [],
    closeState: opts.closeState || null,
    expired: Boolean(opts.expired),
    hitMessages: false,
    eventsPolls: 0,
    joinPolls: 0,
  };
  const server = http.createServer(async (req, res) => {
    const { pathname, searchParams } = new URL(req.url, 'http://x');
    if (!req.headers['authorization']?.startsWith('Bearer ')) {
      res.writeHead(401, { 'content-type': 'application/json' });
      res.end('{}');
      return;
    }
    if (state.expired) {
      res.writeHead(410, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: 'thread_expired' }));
      return;
    }
    if (/\/messages$/.test(pathname)) {
      // Heartbeat must NEVER fetch bodies. Record the violation; still answer
      // so a buggy CLI doesn't hang, but the test asserts hitMessages===false.
      state.hitMessages = true;
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('[]');
      return;
    }
    if (/\/events$/.test(pathname)) {
      state.eventsPolls++;
      const after = Number(searchParams.get('after') ?? '-1');
      const rows = state.events.filter((e) => Number(e.id) > after);
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(rows));
      return;
    }
    if (/\/join$/.test(pathname)) {
      state.joinPolls++;
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ close_state: state.closeState }));
      return;
    }
    res.writeHead(404, { 'content-type': 'application/json' });
    res.end('{}');
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const port = /** @type {any} */ (server.address()).port;
      resolve({ server, state, url: `http://127.0.0.1:${port}` });
    });
  });
}

function runCli(args, env = {}) {
  return new Promise((resolve) => {
    const child = spawn(CLI, args, {
      env: { ...process.env, ...env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '', stderr = '';
    child.stdout.on('data', (d) => (stdout += d));
    child.stderr.on('data', (d) => (stderr += d));
    child.on('exit', (code) => resolve({ code, stdout, stderr }));
  });
}

function freshRoom(name, { cursor = -1 } = {}) {
  const room = `t_hb_${name}_${process.pid}`;
  try { fs.unlinkSync(path.join(STATE_DIR, `${room}-host.state.json`)); } catch {}
  initState({ room_id: room, role: 'host', host_token: HOST_TOKEN });
  if (cursor !== -1) {
    const s = readState(room, 'host');
    setCursor(s, cursor);
  }
  return room;
}

function readJsonState(room, role = 'host') {
  return JSON.parse(fs.readFileSync(path.join(STATE_DIR, `${room}-${role}.state.json`), 'utf8'));
}

// Parse the single JSON object the heartbeat prints (printJson uses 2-space
// pretty form, so just JSON.parse the whole stdout).
function parseDecision(stdout) {
  return JSON.parse(stdout);
}

test('no new event → noop/no_new_event (and cursor untouched, no body fetch)', async () => {
  const room = freshRoom('nonew', { cursor: 5 });
  const { server, state, url } = await startMockRelay({ events: [{ id: 5, from: 'guest', kind: 'message', ts: 1 }] });
  try {
    const r = await runCli(['heartbeat', '--room', room, '--role', 'host'], { CLAWROOM_RELAY: url });
    assert.equal(r.code, 0, `default mode noop must exit 0; stderr=${r.stderr}`);
    const d = parseDecision(r.stdout);
    assert.equal(d.action, 'noop');
    assert.equal(d.reason, 'no_new_event');
    assert.equal(d.room, room);
    assert.equal(d.role, 'host');
    assert.equal(d.event_id, null);
    assert.equal(state.hitMessages, false, 'heartbeat must NOT fetch message bodies');
    assert.equal(readJsonState(room).last_event_cursor, 5, 'heartbeat must NOT advance the cursor');
  } finally {
    server.close();
  }
});

test('peer message event → wake_agent/peer_event with correct event_id + lease written', async () => {
  const room = freshRoom('peer', { cursor: 2 });
  const { server, state, url } = await startMockRelay({
    events: [{ id: 3, from: 'guest', kind: 'message', ts: 10 }],
  });
  try {
    const r = await runCli(['heartbeat', '--room', room, '--role', 'host'], { CLAWROOM_RELAY: url });
    assert.equal(r.code, 0, `stderr=${r.stderr}`);
    const d = parseDecision(r.stdout);
    assert.equal(d.action, 'wake_agent');
    assert.equal(d.reason, 'peer_event');
    assert.equal(d.event_id, 3);
    assert.equal(state.hitMessages, false);
    const s = readJsonState(room);
    assert.equal(s.last_event_cursor, 2, 'cursor still untouched by a wake');
    assert.equal(s.last_wakeup_event_id, 3, 'lease records the woken event id');
    assert.equal(typeof s.wakeup_inflight_until, 'string', 'lease records an inflight-until time');
  } finally {
    server.close();
  }
});

test('only our own event since cursor → noop/self_event', async () => {
  const room = freshRoom('self', { cursor: 2 });
  const { server, url } = await startMockRelay({
    events: [{ id: 3, from: 'host', kind: 'message', ts: 10 }],
  });
  try {
    const r = await runCli(['heartbeat', '--room', room, '--role', 'host'], { CLAWROOM_RELAY: url });
    assert.equal(r.code, 0, `stderr=${r.stderr}`);
    const d = parseDecision(r.stdout);
    assert.equal(d.action, 'noop');
    assert.equal(d.reason, 'self_event');
    assert.equal(d.event_id, null);
    assert.equal(readJsonState(room).last_wakeup_event_id, 0, 'self event writes no lease');
  } finally {
    server.close();
  }
});

test('peer close while we have not closed → wake_agent/peer_close', async () => {
  const room = freshRoom('peerclose', { cursor: 4 });
  const { server, state, url } = await startMockRelay({
    events: [{ id: 5, from: 'guest', kind: 'close', ts: 20 }],
    closeState: { host_closed: false, guest_closed: true, closed: false },
  });
  try {
    const r = await runCli(['heartbeat', '--room', room, '--role', 'host'], { CLAWROOM_RELAY: url });
    assert.equal(r.code, 0, `stderr=${r.stderr}`);
    const d = parseDecision(r.stdout);
    assert.equal(d.action, 'wake_agent');
    assert.equal(d.reason, 'peer_close');
    assert.equal(d.event_id, 5);
    assert.equal(state.joinPolls, 1, 'a close event triggers exactly one authoritative /join');
    assert.equal(state.hitMessages, false);
  } finally {
    server.close();
  }
});

test('mutual close → cancel/mutual_close', async () => {
  const room = freshRoom('mutual', { cursor: 4 });
  const { server, state, url } = await startMockRelay({
    events: [{ id: 5, from: 'guest', kind: 'close', ts: 20 }],
    closeState: { host_closed: true, guest_closed: true, closed: true },
  });
  try {
    const r = await runCli(['heartbeat', '--room', room, '--role', 'host'], { CLAWROOM_RELAY: url });
    assert.equal(r.code, 0, `default mode cancel must exit 0; stderr=${r.stderr}`);
    const d = parseDecision(r.stdout);
    assert.equal(d.action, 'cancel');
    assert.equal(d.reason, 'mutual_close');
    assert.equal(state.hitMessages, false);
    assert.equal(readJsonState(room).last_wakeup_event_id, 0, 'mutual close writes no wake lease');
  } finally {
    server.close();
  }
});

test('TTL: relay 410 thread_expired → cancel/ttl (no body fetch)', async () => {
  const room = freshRoom('ttl', { cursor: 1 });
  const { server, state, url } = await startMockRelay({ expired: true });
  try {
    const r = await runCli(['heartbeat', '--room', room, '--role', 'host'], { CLAWROOM_RELAY: url });
    assert.equal(r.code, 0, `default mode cancel must exit 0; stderr=${r.stderr}`);
    const d = parseDecision(r.stdout);
    assert.equal(d.action, 'cancel');
    assert.equal(d.reason, 'ttl');
    assert.equal(state.hitMessages, false);
  } finally {
    server.close();
  }
});

test('pending_owner_ask set → notify_owner/pending_owner_ask AND it did NOT poll or wake', async () => {
  const room = freshRoom('pending', { cursor: 1 });
  // Set a not-yet-timed-out pending ask directly in state.
  const s = readState(room, 'host');
  setPendingOwnerAsk(s, {
    question_id: 'q-block',
    question_text: 'over ceiling?',
    asked_at: new Date().toISOString(),
    timeout_at: '2099-01-01T00:00:00.000Z',
    blocks_until: 'answered',
    context_snapshot: {},
  });
  const { server, state, url } = await startMockRelay({
    events: [{ id: 9, from: 'guest', kind: 'message', ts: 30 }], // a peer event is waiting...
  });
  try {
    const r = await runCli(['heartbeat', '--room', room, '--role', 'host'], { CLAWROOM_RELAY: url });
    assert.equal(r.code, 0, `stderr=${r.stderr}`);
    const d = parseDecision(r.stdout);
    assert.equal(d.action, 'notify_owner');
    assert.equal(d.reason, 'pending_owner_ask');
    // ...but the agent is blocked on the OWNER, so heartbeat must NOT poll the
    // relay at all and must NOT wake.
    assert.equal(state.eventsPolls, 0, 'blocked-on-owner must short-circuit BEFORE any relay poll');
    assert.equal(state.joinPolls, 0);
    assert.equal(state.hitMessages, false);
    assert.equal(readJsonState(room).last_wakeup_event_id, 0, 'no wake lease while blocked on owner');
  } finally {
    server.close();
  }
});

test('dedupe: same peer event → 1st wake_agent, 2nd noop/wake_inflight; after lease expiry → wake again', async () => {
  const room = freshRoom('dedupe', { cursor: 2 });
  const { server, url } = await startMockRelay({
    events: [{ id: 3, from: 'guest', kind: 'message', ts: 10 }],
  });
  try {
    // First heartbeat with a long lease → wake_agent, lease written.
    const first = await runCli(
      ['heartbeat', '--room', room, '--role', 'host', '--lease-ttl', '3600'],
      { CLAWROOM_RELAY: url }
    );
    assert.equal(parseDecision(first.stdout).action, 'wake_agent');

    // Second heartbeat, SAME peer event id (cursor never advances — heartbeat
    // doesn't touch it) → deduped to noop/wake_inflight.
    const second = await runCli(
      ['heartbeat', '--room', room, '--role', 'host', '--lease-ttl', '3600'],
      { CLAWROOM_RELAY: url }
    );
    const d2 = parseDecision(second.stdout);
    assert.equal(d2.action, 'noop');
    assert.equal(d2.reason, 'wake_inflight');
    assert.equal(d2.event_id, 3);

    // Force the lease to expire, then a third heartbeat → wake again.
    const s = readJsonState(room);
    s.wakeup_inflight_until = '2000-01-01T00:00:00.000Z';
    fs.writeFileSync(path.join(STATE_DIR, `${room}-host.state.json`), JSON.stringify(s, null, 2));
    const third = await runCli(
      ['heartbeat', '--room', room, '--role', 'host', '--lease-ttl', '3600'],
      { CLAWROOM_RELAY: url }
    );
    assert.equal(parseDecision(third.stdout).action, 'wake_agent', 'after lease expiry the same event wakes again');
  } finally {
    server.close();
  }
});

test('lease write does NOT clobber a concurrently-set pending_owner_ask (setWakeLease unit)', () => {
  // Mirror of the setCursor clobber regression (post-cursor.test.mjs). A
  // scheduler-driven heartbeat may write its wake-lease using a state snapshot
  // read BEFORE the primary agent's turn wrote pending_owner_ask. setWakeLease
  // must re-read the freshest on-disk state and merge ONLY its two lease fields,
  // never re-persisting the stale pending_owner_ask/approvals/cursor.
  const room = `t_hb_leaseclobber_${process.pid}`;
  try { fs.unlinkSync(path.join(STATE_DIR, `${room}-host.state.json`)); } catch {}
  initState({ room_id: room, role: 'host', host_token: HOST_TOKEN });

  const staleHeartbeatSnapshot = readState(room, 'host'); // heartbeat's early read (no pending, cursor -1)
  const agentView = readState(room, 'host');              // the agent turn lands a pending ask + a cursor bump
  setPendingOwnerAsk(agentView, { question_id: 'q9', timeout_at: '2099-01-01T00:00:00.000Z' });
  setCursor(agentView, 7);
  setWakeLease(staleHeartbeatSnapshot, 3, '2099-01-01T00:00:00.000Z'); // ...heartbeat finishes with its STALE object

  const final = readState(room, 'host');
  assert.ok(final.pending_owner_ask, 'pending_owner_ask must survive a concurrent wake-lease write');
  assert.equal(final.pending_owner_ask.question_id, 'q9');
  assert.equal(final.last_event_cursor, 7, 'the agent cursor bump must survive');
  assert.equal(final.last_wakeup_event_id, 3, 'the lease must still apply');
  assert.equal(final.wakeup_inflight_until, '2099-01-01T00:00:00.000Z');
});

test('--exit-code-mode maps action → exit code; default mode is 0 even on noop', async () => {
  // noop
  const roomNoop = freshRoom('ecnoop', { cursor: 5 });
  const noopRelay = await startMockRelay({ events: [{ id: 5, from: 'guest', kind: 'message', ts: 1 }] });
  // wake
  const roomWake = freshRoom('ecwake', { cursor: 2 });
  const wakeRelay = await startMockRelay({ events: [{ id: 3, from: 'guest', kind: 'message', ts: 1 }] });
  // cancel (mutual close)
  const roomCancel = freshRoom('eccancel', { cursor: 4 });
  const cancelRelay = await startMockRelay({
    events: [{ id: 5, from: 'guest', kind: 'close', ts: 1 }],
    closeState: { host_closed: true, guest_closed: true, closed: true },
  });
  // notify_owner
  const roomNotify = freshRoom('ecnotify', { cursor: 1 });
  {
    const s = readState(roomNotify, 'host');
    setPendingOwnerAsk(s, {
      question_id: 'q', question_text: 'x', asked_at: new Date().toISOString(),
      timeout_at: '2099-01-01T00:00:00.000Z', blocks_until: 'answered', context_snapshot: {},
    });
  }
  const notifyRelay = await startMockRelay({ events: [] });
  try {
    // Default mode: noop is exit 0, not a failure.
    const dn = await runCli(['heartbeat', '--room', roomNoop, '--role', 'host'], { CLAWROOM_RELAY: noopRelay.url });
    assert.equal(dn.code, 0, 'default mode noop exits 0');

    // --exit-code-mode: 0 wake / 3 noop / 4 cancel / 5 notify_owner.
    const en = await runCli(['heartbeat', '--room', roomNoop, '--role', 'host', '--exit-code-mode'], { CLAWROOM_RELAY: noopRelay.url });
    assert.equal(en.code, 3, 'exit-code-mode noop → 3');
    assert.equal(parseDecision(en.stdout).action, 'noop', 'JSON still prints in exit-code-mode');

    const ew = await runCli(['heartbeat', '--room', roomWake, '--role', 'host', '--exit-code-mode'], { CLAWROOM_RELAY: wakeRelay.url });
    assert.equal(ew.code, 0, 'exit-code-mode wake_agent → 0');

    const ec = await runCli(['heartbeat', '--room', roomCancel, '--role', 'host', '--exit-code-mode'], { CLAWROOM_RELAY: cancelRelay.url });
    assert.equal(ec.code, 4, 'exit-code-mode cancel → 4');

    const eo = await runCli(['heartbeat', '--room', roomNotify, '--role', 'host', '--exit-code-mode'], { CLAWROOM_RELAY: notifyRelay.url });
    assert.equal(eo.code, 5, 'exit-code-mode notify_owner → 5');
  } finally {
    noopRelay.server.close();
    wakeRelay.server.close();
    cancelRelay.server.close();
    notifyRelay.server.close();
  }
});

test('JSON shape is exactly {ok,action,reason,room,role,event_id}', async () => {
  const room = freshRoom('shape', { cursor: 2 });
  const { server, url } = await startMockRelay({ events: [{ id: 3, from: 'guest', kind: 'message', ts: 1 }] });
  try {
    const r = await runCli(['heartbeat', '--room', room, '--role', 'host'], { CLAWROOM_RELAY: url });
    const d = parseDecision(r.stdout);
    assert.deepEqual(
      Object.keys(d).sort(),
      ['action', 'event_id', 'ok', 'reason', 'role', 'room'],
      'exact key set, no extra fields (no tokens/paths/internals)'
    );
    assert.equal(d.ok, true);
    // Owner-facing safety: the JSON must never carry tokens, paths, or the kind
    // of internals gotchas.md forbids.
    const blob = JSON.stringify(d);
    assert.ok(!/host_token|guest_token|_token|\.state\.json|Bearer|\/tmp\//.test(blob), 'no secrets/paths in output');
  } finally {
    server.close();
  }
});

test('bad args / missing role → non-zero exit (real error, not a detection)', async () => {
  const room = freshRoom('badargs', { cursor: 1 });
  const r = await runCli(['heartbeat', '--room', room], {}); // missing --role
  assert.notEqual(r.code, 0, 'missing required flag is a real error');
  assert.ok(/role/.test(r.stderr), 'error names the missing flag');
});
