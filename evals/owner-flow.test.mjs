// evals/owner-flow.test.mjs
// Codex pass 3 P1: end-to-end smoke of the primary-conversation owner
// approval flow via the new CLI commands `ask-owner` and `owner-reply`.
// Together they must drive state.pending_owner_ask + state.owner_approvals
// so the close hard wall accepts a state-backed approval and rejects a
// stale/timeout one.

process.env.CLAWROOM_STATE_DIR = `/tmp/clawroom-v4-owner-flow-test-${process.pid}`;

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CLI = path.resolve(__dirname, '../skill/cli/clawroom');

const { initState, readState, setPendingOwnerAsk, resolveOwnerAsk, clearOwnerAnsweredWake, setCursor, setWakeLease } =
  await import('../skill/lib/state.mjs');
const { validateAndPrepareClose } = await import('../skill/lib/close.mjs');
const { STATE_DIR } = await import('../skill/lib/types.mjs');

function cleanState(room_id, role) {
  try { fs.unlinkSync(path.join(STATE_DIR, `${room_id}-${role}.state.json`)); } catch {}
}

function runCli(args) {
  return new Promise((resolve) => {
    const child = spawn(CLI, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', (d) => stdout += d);
    child.stderr.on('data', (d) => stderr += d);
    child.on('exit', (code) => resolve({ code, stdout, stderr }));
  });
}

function constraintDraft(approvalEvidence) {
  return {
    outcome: 'agreement',
    agreed_terms: [{ term: 'price', value: '$720', provenance: 'owner_reply:q1' }],
    unresolved_items: [],
    owner_constraints: [
      { constraint: 'budget_ceiling_usd=650', source: 'create', requires_owner_approval: true },
    ],
    peer_commitments: [{ commitment: 'deliver by 2026-06-15', provenance: 'peer_message:3' }],
    owner_approvals: [{
      question_id: 'q1',
      decision: 'approve',
      source: 'primary_agent_conversation',
      ts: new Date().toISOString(),
      evidence: approvalEvidence,
    }],
    next_steps: [],
    owner_summary: 'Owner approved exceeding the budget ceiling to $720 after explicit ask.',
  };
}

test('ask-owner sets pending; agreement close is blocked until owner-reply', async () => {
  const room = `t_ownerflow_${process.pid}_a`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });

  // Stage 1: ask-owner
  const ask = await runCli([
    'ask-owner', '--room', room, '--role', 'host',
    '--question-id', 'q1',
    '--question-text', 'Approve exceeding budget_ceiling_usd=650 up to $720?',
    '--timeout-seconds', '300',
  ]);
  assert.equal(ask.code, 0, `ask-owner failed: ${ask.stderr}`);
  let state = readState(room, 'host');
  assert.ok(state.pending_owner_ask, 'pending_owner_ask must be set');
  assert.equal(state.pending_owner_ask.question_id, 'q1');

  // Attempted close while pending → blocked.
  const evidence = 'owner approved exceeding budget_ceiling_usd=650 to $720 via session reply';
  const draft = constraintDraft(evidence);
  let result = validateAndPrepareClose({ draft, state });
  assert.equal(result.ok, false, 'agreement close must be blocked while pending_owner_ask is set');
  assert.ok(result.issues.some((i) => i.code === 'pending_ask_blocks_agreement'));

  // Stage 2: owner-reply with approve
  const reply = await runCli([
    'owner-reply', '--room', room, '--role', 'host',
    '--question-id', 'q1',
    '--decision', 'approve',
    '--evidence', evidence,
    '--source', 'primary_agent_conversation',
  ]);
  assert.equal(reply.code, 0, `owner-reply failed: ${reply.stderr}`);
  state = readState(room, 'host');
  assert.equal(state.pending_owner_ask, null, 'pending_owner_ask must be cleared after owner-reply');
  assert.equal(state.owner_approvals.length, 1);
  assert.equal(state.owner_approvals[0].decision, 'approve');

  // Now the draft (mirroring state record exactly) must pass.
  const draft2 = constraintDraft(evidence);
  draft2.owner_approvals[0].ts = state.owner_approvals[0].ts;
  result = validateAndPrepareClose({ draft: draft2, state });
  assert.equal(result.ok, true,
    `agreement close should now pass; got: ${JSON.stringify(result.issues)}`);
});

test('owner-reply with reject blocks agreement close', async () => {
  const room = `t_ownerflow_${process.pid}_b`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });

  await runCli([
    'ask-owner', '--room', room, '--role', 'host',
    '--question-id', 'q1', '--question-text', '?', '--timeout-seconds', '300',
  ]);
  const rejectEvidence = 'owner explicitly said no';
  await runCli([
    'owner-reply', '--room', room, '--role', 'host',
    '--question-id', 'q1', '--decision', 'reject',
    '--evidence', rejectEvidence,
  ]);
  const state = readState(room, 'host');
  assert.equal(state.owner_approvals[0].decision, 'reject');

  // Draft tries to claim approve — must be rejected on decision_mismatch.
  const draft = constraintDraft(rejectEvidence);
  const result = validateAndPrepareClose({ draft, state });
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'approval_decision_mismatch'),
    `expected approval_decision_mismatch, got ${result.issues.map((i) => i.code).join(',')}`);
});

test('owner-reply errors if no matching pending_owner_ask', async () => {
  const room = `t_ownerflow_${process.pid}_c`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  const reply = await runCli([
    'owner-reply', '--room', room, '--role', 'host',
    '--question-id', 'q_none', '--decision', 'approve', '--evidence', 'x',
  ]);
  assert.notEqual(reply.code, 0, 'owner-reply without pending must fail');
  assert.match(reply.stderr, /no pending_owner_ask/);
});

// Codex pass 4 P1: cmdPost must hard-block on pending_owner_ask.
test('post is hard-blocked while pending_owner_ask is set (Codex pass 4 P1)', async () => {
  const room = `t_ownerflow_${process.pid}_d`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  await runCli([
    'ask-owner', '--room', room, '--role', 'host',
    '--question-id', 'q1', '--question-text', '?', '--timeout-seconds', '300',
  ]);
  // Use a relay URL that would fail if reached — proves we exit BEFORE network.
  const result = await runCli(
    ['post', '--room', room, '--role', 'host', '--text', 'should be blocked'],
    { CLAWROOM_RELAY: 'http://127.0.0.1:1/never-reached' }
  );
  assert.equal(result.code, 5, `expected exit code 5 (pending block), got ${result.code}; stderr=${result.stderr}`);
  assert.match(result.stderr, /pending_owner_ask.*unresolved/);
});

test('post --allow-pending-owner-ask bypasses the block (escape hatch)', async () => {
  const room = `t_ownerflow_${process.pid}_d2`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });

  // Hoist mock setup BEFORE ask-owner so server.listen has fully resolved.
  const http = await import('node:http');
  const { server, url } = await new Promise((resolve) => {
    const s = http.createServer((req, res) => {
      if (!req.headers['authorization']?.startsWith('Bearer ')) {
        res.writeHead(401); res.end('{}'); return;
      }
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ id: 0, from: 'host', kind: 'message', ts: Date.now(), text: 'echo' }));
    });
    s.listen(0, '127.0.0.1', () => {
      const port = /** @type {any} */ (s.address()).port;
      resolve({ server: s, url: `http://127.0.0.1:${port}` });
    });
  });

  try {
    await runCli([
      'ask-owner', '--room', room, '--role', 'host',
      '--question-id', 'q1', '--question-text', '?', '--timeout-seconds', '300',
    ], { CLAWROOM_RELAY: url });
    // Use --relay flag instead of env to remove env-propagation as a variable
    // (env works in other tests; bypass test was hitting production for an
    // unidentified reason). --relay has precedence over both env and default.
    const result = await runCli(
      ['post', '--room', room, '--role', 'host', '--text', 'status-only',
       '--allow-pending-owner-ask', '--relay', url],
    );
    assert.equal(result.code, 0, `bypass should succeed; stderr=${result.stderr}`);
  } finally {
    server.close();
  }
});

// Codex pass 4 P1: timed-out ask + decision=approve must fail closed without
// mutating state. Reject after timeout is allowed (cannot unblock agreement).
test('owner-reply with approve after timeout fails closed; state unchanged', async () => {
  const room = `t_ownerflow_${process.pid}_e`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  // Set up an already-timed-out pending by writing state directly.
  const stateBefore = readState(room, 'host');
  stateBefore.pending_owner_ask = {
    question_id: 'q1',
    question_text: '?',
    asked_at: new Date(Date.now() - 60_000).toISOString(),
    timeout_at: new Date(Date.now() - 1_000).toISOString(), // already past
    blocks_until: 'answered',
    context_snapshot: {},
  };
  fs.writeFileSync(
    path.join(STATE_DIR, `${room}-host.state.json`),
    JSON.stringify(stateBefore, null, 2),
  );
  const result = await runCli([
    'owner-reply', '--room', room, '--role', 'host',
    '--question-id', 'q1', '--decision', 'approve', '--evidence', 'x',
  ]);
  assert.equal(result.code, 6, `expected exit code 6 (timeout-approve block), got ${result.code}; stderr=${result.stderr}`);
  assert.match(result.stderr, /timed out|not allowed/i);
  const stateAfter = readState(room, 'host');
  assert.ok(stateAfter.pending_owner_ask, 'pending_owner_ask must remain (state unchanged)');
  assert.equal(stateAfter.owner_approvals.length, 0, 'no approval should be recorded');
});

test('owner-reply with reject after timeout succeeds; agreement still blocked downstream', async () => {
  const room = `t_ownerflow_${process.pid}_f`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  const s = readState(room, 'host');
  s.pending_owner_ask = {
    question_id: 'q1',
    question_text: '?',
    asked_at: new Date(Date.now() - 60_000).toISOString(),
    timeout_at: new Date(Date.now() - 1_000).toISOString(),
    blocks_until: 'answered',
    context_snapshot: {},
  };
  fs.writeFileSync(path.join(STATE_DIR, `${room}-host.state.json`), JSON.stringify(s, null, 2));
  const result = await runCli([
    'owner-reply', '--room', room, '--role', 'host',
    '--question-id', 'q1', '--decision', 'reject', '--evidence', 'timed out, treat as no',
  ]);
  assert.equal(result.code, 0, `reject after timeout should be allowed; stderr=${result.stderr}`);
  const stateAfter = readState(room, 'host');
  assert.equal(stateAfter.owner_approvals.length, 1);
  assert.equal(stateAfter.owner_approvals[0].decision, 'reject');
});

// Codex pass 4 P2: input validation
test('ask-owner rejects non-positive --timeout-seconds', async () => {
  const room = `t_ownerflow_${process.pid}_g`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  const r1 = await runCli([
    'ask-owner', '--room', room, '--role', 'host',
    '--question-id', 'q1', '--question-text', '?', '--timeout-seconds', '0',
  ]);
  assert.notEqual(r1.code, 0);
  assert.match(r1.stderr, /must be a positive number/);
});

test('owner-reply rejects --source not in APPROVAL_SOURCES', async () => {
  const room = `t_ownerflow_${process.pid}_h`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  await runCli([
    'ask-owner', '--room', room, '--role', 'host',
    '--question-id', 'q1', '--question-text', '?', '--timeout-seconds', '300',
  ]);
  const r = await runCli([
    'owner-reply', '--room', room, '--role', 'host',
    '--question-id', 'q1', '--decision', 'approve', '--evidence', 'e', '--source', 'bogus_source',
  ]);
  assert.notEqual(r.code, 0);
  assert.match(r.stderr, /--source must be one of/);
});

// ---- owner_answered wake signal (unattended owner-approval loop close) ----

function setAndResolveAsk(room, decision) {
  const s = readState(room, 'host');
  setPendingOwnerAsk(s, {
    question_id: 'q1', question_text: '?', asked_at: new Date().toISOString(),
    timeout_at: '2099-01-01T00:00:00.000Z', blocks_until: 'answered', context_snapshot: {},
  });
  resolveOwnerAsk(s, {
    question_id: 'q1', decision, source: 'primary_agent_conversation',
    ts: new Date().toISOString(), evidence: 'e',
  });
  return s;
}

test('resolveOwnerAsk sets owner_answered_wake on APPROVE (and clears pending)', () => {
  const room = `t_ownerflow_${process.pid}_wake_approve`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  setAndResolveAsk(room, 'approve');
  const s = readState(room, 'host');
  assert.equal(s.pending_owner_ask, null, 'pending cleared');
  assert.ok(s.owner_answered_wake, 'owner_answered_wake must be set');
  assert.equal(s.owner_answered_wake.question_id, 'q1');
  assert.equal(s.owner_answered_wake.decision, 'approve');
  assert.equal(typeof s.owner_answered_wake.answered_at, 'string');
});

test('resolveOwnerAsk sets owner_answered_wake on REJECT too', () => {
  const room = `t_ownerflow_${process.pid}_wake_reject`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  setAndResolveAsk(room, 'reject');
  const s = readState(room, 'host');
  assert.ok(s.owner_answered_wake, 'a reject must also set the wake signal');
  assert.equal(s.owner_answered_wake.decision, 'reject');
});

test('clearOwnerAnsweredWake removes the signal', () => {
  const room = `t_ownerflow_${process.pid}_wake_clear`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  const s = setAndResolveAsk(room, 'approve');
  clearOwnerAnsweredWake(s);
  const after = readState(room, 'host');
  assert.ok(!after.owner_answered_wake, 'owner_answered_wake must be cleared (null/absent)');
  // The recorded approval must survive — clearing the wake signal must not drop it.
  assert.equal(after.owner_approvals.length, 1);
  assert.equal(after.owner_approvals[0].decision, 'approve');
});

test('clearOwnerAnsweredWake merges concurrently (does NOT clobber a wake-lease written after the caller read)', () => {
  // Mirror of the setCursor clobber regression. The agent's post/close holds a
  // state snapshot that has owner_answered_wake set; meanwhile a scheduler-
  // driven heartbeat writes the wake-lease fields. clearOwnerAnsweredWake must
  // re-read the freshest state and clear ONLY owner_answered_wake, never
  // re-persisting the stale (pre-lease) lease fields.
  const room = `t_ownerflow_${process.pid}_wake_merge`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });
  const staleAgentSnapshot = setAndResolveAsk(room, 'approve'); // has owner_answered_wake set, lease unset

  // Concurrent heartbeat lands a wake-lease AND a cursor bump after the agent's read.
  const heartbeatView = readState(room, 'host');
  setWakeLease(heartbeatView, -3553, '2099-01-01T00:00:00.000Z');
  const cursorView = readState(room, 'host');
  setCursor(cursorView, 9);

  // ...then the agent's turn finishes with its STALE object.
  clearOwnerAnsweredWake(staleAgentSnapshot);

  const final = readState(room, 'host');
  assert.ok(!final.owner_answered_wake, 'owner_answered_wake cleared');
  assert.equal(final.last_wakeup_event_id, -3553, 'the concurrent wake-lease must survive');
  assert.equal(final.wakeup_inflight_until, '2099-01-01T00:00:00.000Z', 'lease inflight time survives');
  assert.equal(final.last_event_cursor, 9, 'the concurrent cursor bump must survive');
  assert.equal(final.owner_approvals.length, 1, 'recorded approval survives');
});

test('cmdPost clears owner_answered_wake after a successful post (signal consumed, no re-wake loop)', async () => {
  const room = `t_ownerflow_${process.pid}_post_clears`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });

  // Mock relay that echoes a posted message id.
  const http = await import('node:http');
  const { server, url } = await new Promise((resolve) => {
    const s = http.createServer((req, res) => {
      if (!req.headers['authorization']?.startsWith('Bearer ')) { res.writeHead(401); res.end('{}'); return; }
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ id: 4, from: 'host', kind: 'message', ts: Date.now(), text: 'echo' }));
    });
    s.listen(0, '127.0.0.1', () => resolve({ server: s, url: `http://127.0.0.1:${/** @type {any} */ (s.address()).port}` }));
  });

  try {
    // Owner asked then answered → pending cleared, owner_answered_wake set.
    setAndResolveAsk(room, 'approve');
    assert.ok(readState(room, 'host').owner_answered_wake, 'precondition: wake signal set');

    // The agent (woken on owner_answered) posts its reply. pending is null so the
    // post is NOT blocked; on success the wake signal must be consumed.
    const r = await runCli(['post', '--room', room, '--role', 'host', '--text', 'sharing the owner-approved plan', '--relay', url]);
    assert.equal(r.code, 0, `post should succeed; stderr=${r.stderr}`);
    const after = readState(room, 'host');
    assert.ok(!after.owner_answered_wake, 'owner_answered_wake must be cleared after a successful post');
    assert.equal(after.owner_approvals.length, 1, 'the recorded approval still survives the clear');
  } finally {
    server.close();
  }
});

test('cmdClose clears owner_answered_wake after a successful close', async () => {
  const room = `t_ownerflow_${process.pid}_close_clears`;
  cleanState(room, 'host');
  initState({ room_id: room, role: 'host', host_token: 'host_test' });

  // Owner asked then approved → owner_answered_wake set, approval recorded.
  setAndResolveAsk(room, 'approve');
  const state = readState(room, 'host');
  assert.ok(state.owner_answered_wake, 'precondition: wake signal set');

  // Build a minimal agreement draft whose single approval mirrors the recorded
  // state approval exactly (so validateAndPrepareClose accepts it).
  const approval = state.owner_approvals[0];
  const draft = {
    outcome: 'agreement',
    agreed_terms: [{ term: 'plan', value: 'go', provenance: 'owner_reply:q1' }],
    unresolved_items: [],
    owner_constraints: [],
    peer_commitments: [],
    owner_approvals: [{
      question_id: 'q1', decision: 'approve', source: 'primary_agent_conversation',
      ts: approval.ts, evidence: approval.evidence,
    }],
    next_steps: [],
    owner_summary: 'Owner approved the plan; closing with agreement.',
  };
  const draftPath = path.join(STATE_DIR, `${room}-draft.json`);
  fs.writeFileSync(draftPath, JSON.stringify(draft));

  // Mock relay that accepts the close.
  const http = await import('node:http');
  const { server, url } = await new Promise((resolve) => {
    const s = http.createServer((req, res) => {
      if (!req.headers['authorization']?.startsWith('Bearer ')) { res.writeHead(401); res.end('{}'); return; }
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ id: 7, kind: 'close', ts: Date.now() }));
    });
    s.listen(0, '127.0.0.1', () => resolve({ server: s, url: `http://127.0.0.1:${/** @type {any} */ (s.address()).port}` }));
  });

  try {
    const r = await runCli(['close', '--room', room, '--role', 'host', '--draft-file', draftPath, '--relay', url]);
    assert.equal(r.code, 0, `close should succeed; stderr=${r.stderr}`);
    const after = readState(room, 'host');
    assert.ok(!after.owner_answered_wake, 'owner_answered_wake must be cleared after a successful close');
  } finally {
    server.close();
  }
});

test('backward-compat: a state file WITHOUT owner_answered_wake still validates/reads (resumeRoom + writeState)', () => {
  // initState never writes owner_answered_wake, so a freshly created state file
  // exercises the absent-field path. It must read back cleanly (validateRoomState
  // treats the field as optional, exactly like the wake-lease fields), and a
  // re-write must not invent the field.
  const room = `t_ownerflow_${process.pid}_backcompat`;
  cleanState(room, 'host');
  const created = initState({ room_id: room, role: 'host', host_token: 'host_test' });
  assert.equal(created.owner_answered_wake, undefined, 'initState does not set owner_answered_wake');
  // Read back (calls validateRoomState internally) — must not throw.
  const read = readState(room, 'host');
  assert.ok(read, 'state without owner_answered_wake must read cleanly');
  assert.equal(read.owner_answered_wake, undefined);
  // Simulate an OLD on-disk file that predates the feature: strip any wake/lease
  // fields and confirm validation still accepts it.
  const raw = JSON.parse(fs.readFileSync(path.join(STATE_DIR, `${room}-host.state.json`), 'utf8'));
  delete raw.owner_answered_wake;
  delete raw.last_wakeup_event_id;
  delete raw.wakeup_inflight_until;
  fs.writeFileSync(path.join(STATE_DIR, `${room}-host.state.json`), JSON.stringify(raw, null, 2));
  const reread = readState(room, 'host');
  assert.ok(reread, 'an old-shape state file (no wake fields) must still validate');
});
