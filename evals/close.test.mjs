// evals/close.test.mjs
// Release gate for invariant 13 + capability primitive #6: `clawroom close`
// hard wall must reject all 5 invalid CloseDraft cases agreed in
// `t_bf866856-df0` reflection sync, and accept the happy path.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  validateCloseDraft,
  validateCloseAgainstState,
  validateAndPrepareClose,
  canonicalCloseDraft,
} from '../skill/lib/close.mjs';

const ROOM_ID = 't_close_test';

function makeState(overrides = {}) {
  return {
    room_id: ROOM_ID,
    role: 'host',
    host_token: 'host_test',
    last_event_cursor: -1,
    pending_owner_ask: null,
    owner_approvals: [],
    draft_close: null,
    started_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    ...overrides,
  };
}

function happyDraft(overrides = {}) {
  return {
    outcome: 'agreement',
    agreed_terms: [
      { term: 'price', value: '$650', provenance: 'owner_context' },
    ],
    unresolved_items: [],
    owner_constraints: [
      { constraint: 'budget_ceiling_usd=650', source: 'create', requires_owner_approval: false },
    ],
    peer_commitments: [
      { commitment: 'deliver by 2026-06-15', provenance: 'peer_message:3' },
    ],
    owner_approvals: [],
    next_steps: [{ step: 'await fulfillment', owner: 'guest' }],
    owner_summary: 'Buyer agreed to $650 with delivery by June 15. Awaiting fulfillment.',
    ...overrides,
  };
}

// ---- Reject 1: invalid CloseDraft schema ----

test('reject 1: invalid CloseDraft schema (missing outcome)', () => {
  const draft = happyDraft();
  delete draft.outcome;
  const result = validateCloseDraft(draft);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'schema_outcome_invalid'),
    `expected schema_outcome_invalid, got ${result.issues.map((i) => i.code).join(',')}`);
});

test('reject 1b: missing owner_summary', () => {
  const draft = happyDraft({ owner_summary: '' });
  const result = validateCloseDraft(draft);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'schema_owner_summary_required'));
});

// ---- Reject 2: pending_owner_ask blocks outcome=agreement ----

test('reject 2: outcome=agreement while pending_owner_ask is set', () => {
  const draft = happyDraft();
  const state = makeState({
    pending_owner_ask: {
      question_id: 'q1',
      question_text: 'approve over budget?',
      asked_at: new Date().toISOString(),
      timeout_at: new Date(Date.now() + 60_000).toISOString(),
      blocks_until: 'answered',
      context_snapshot: {},
    },
  });
  const result = validateCloseAgainstState(draft, state);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'pending_ask_blocks_agreement'));
});

test('reject 2b: same constraint allows partial close even with pending_owner_ask', () => {
  const draft = happyDraft({
    outcome: 'partial',
    unresolved_items: [{ item: 'over-budget request', reason: 'awaiting owner approval' }],
  });
  const state = makeState({
    pending_owner_ask: {
      question_id: 'q1',
      question_text: '?',
      asked_at: new Date().toISOString(),
      timeout_at: new Date(Date.now() + 60_000).toISOString(),
      blocks_until: 'answered',
      context_snapshot: {},
    },
  });
  const result = validateCloseAgainstState(draft, state);
  assert.equal(result.ok, true,
    `partial close should be allowed even with pending ask: ${JSON.stringify(result.issues)}`);
});

// ---- Reject 3: agreement requires approval for requires_owner_approval constraints ----

test('reject 3: requires_owner_approval constraint without matching approval', () => {
  const draft = happyDraft({
    owner_constraints: [
      { constraint: 'budget_ceiling_usd=650', source: 'create', requires_owner_approval: true },
    ],
    owner_approvals: [], // no approval evidence
  });
  const state = makeState();
  const result = validateCloseAgainstState(draft, state);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'missing_approval_for_constraint'),
    `expected missing_approval_for_constraint, got ${result.issues.map((i) => i.code).join(',')}`);
});

test('reject 3b: state-backed approval evidence matches constraint -> accepted', () => {
  // Post-pass-2 (Codex P1.A): approval must be in STATE, not just in draft.
  // The state record is authoritative; the draft mirrors it (or refers to it).
  const approvalRecord = {
    question_id: 'q1',
    decision: 'approve',
    source: 'primary_agent_conversation',
    ts: new Date().toISOString(),
    evidence: 'owner approved exceeding budget_ceiling_usd=650 to $720 via session reply',
  };
  const draft = happyDraft({
    owner_constraints: [
      { constraint: 'budget_ceiling_usd=650', source: 'create', requires_owner_approval: true },
    ],
    owner_approvals: [approvalRecord],
  });
  const state = makeState({ owner_approvals: [approvalRecord] });
  const result = validateCloseAgainstState(draft, state);
  assert.equal(result.ok, true,
    `expected ok, got issues: ${JSON.stringify(result.issues)}`);
});

// ---- Reject 6: fabricated approval bypass (Codex pass 2 P1.A) ----

test('reject 6a: fabricated approval (in draft, NOT in state) -> rejected', () => {
  const draft = happyDraft({
    owner_constraints: [
      { constraint: 'budget_ceiling_usd=650', source: 'create', requires_owner_approval: true },
    ],
    owner_approvals: [{
      question_id: 'q1',
      decision: 'approve',
      source: 'primary_agent_conversation',
      ts: new Date().toISOString(),
      evidence: 'owner approved budget_ceiling_usd=650', // looks plausible but is fabricated
    }],
  });
  const state = makeState({ owner_approvals: [] }); // STATE is the authority; no record here
  const result = validateCloseAgainstState(draft, state);
  assert.equal(result.ok, false,
    `expected fabricated_approval rejection, got ok=${result.ok}`);
  assert.ok(result.issues.some((i) => i.code === 'fabricated_approval'),
    `expected fabricated_approval, got: ${result.issues.map((i) => i.code).join(',')}`);
});

test('reject 6b: missing state approval for requires_owner_approval constraint -> rejected', () => {
  // Even with no draft.owner_approvals, the constraint needs state backing.
  const draft = happyDraft({
    owner_constraints: [
      { constraint: 'budget_ceiling_usd=650', source: 'create', requires_owner_approval: true },
    ],
    owner_approvals: [],
  });
  const state = makeState({ owner_approvals: [] });
  const result = validateCloseAgainstState(draft, state);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'missing_approval_for_constraint'));
});

test('reject 6c: state approval whose evidence does NOT reference the constraint -> still missing', () => {
  const unrelatedApproval = {
    question_id: 'q1',
    decision: 'approve',
    source: 'primary_agent_conversation',
    ts: new Date().toISOString(),
    evidence: 'owner approved some unrelated matter, no mention of the binding constraint',
  };
  const draft = happyDraft({
    owner_constraints: [
      { constraint: 'budget_ceiling_usd=650', source: 'create', requires_owner_approval: true },
    ],
    owner_approvals: [unrelatedApproval],
  });
  const state = makeState({ owner_approvals: [unrelatedApproval] });
  const result = validateCloseAgainstState(draft, state);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'missing_approval_for_constraint'));
});

// ---- Reject 4: rejected/expired ask presented as approve ----

test('reject 4: state has rejection but draft presents as approve', () => {
  const draft = happyDraft({
    owner_approvals: [{
      question_id: 'q1',
      decision: 'approve',
      source: 'primary_agent_conversation',
      ts: new Date().toISOString(),
      evidence: 'context evidence',
    }],
  });
  const state = makeState({
    owner_approvals: [{
      question_id: 'q1',
      decision: 'reject',
      source: 'primary_agent_conversation',
      ts: new Date().toISOString(),
      evidence: 'owner said no',
    }],
  });
  const result = validateCloseAgainstState(draft, state);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'approval_decision_mismatch'));
});

test('reject 4b: presenting approval after pending_owner_ask timed out', () => {
  const draft = happyDraft({
    owner_approvals: [{
      question_id: 'q1',
      decision: 'approve',
      source: 'primary_agent_conversation',
      ts: new Date().toISOString(),
      evidence: 'evidence',
    }],
  });
  const state = makeState({
    pending_owner_ask: {
      question_id: 'q1',
      question_text: '?',
      asked_at: new Date(Date.now() - 120_000).toISOString(),
      timeout_at: new Date(Date.now() - 60_000).toISOString(), // already past
      blocks_until: 'timeout',
      context_snapshot: {},
    },
  });
  const result = validateCloseAgainstState(draft, state);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'approval_after_timeout' || i.code === 'pending_ask_blocks_agreement'),
    `expected approval_after_timeout or pending_ask_blocks_agreement, got ${result.issues.map((i) => i.code).join(',')}`);
});

// ---- Reject 5: missing provenance ----

test('reject 5: peer_commitment without provenance', () => {
  const draft = happyDraft({
    peer_commitments: [{ commitment: 'deliver by june 15' }], // no provenance
  });
  const result = validateCloseDraft(draft);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'missing_provenance_commitment'));
});

test('reject 5b: owner_approval without evidence', () => {
  const draft = happyDraft({
    owner_approvals: [{
      question_id: 'q1',
      decision: 'approve',
      source: 'primary_agent_conversation',
      ts: new Date().toISOString(),
      // evidence omitted
    }],
  });
  const result = validateCloseDraft(draft);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some((i) => i.code === 'missing_provenance_approval'));
});

// ---- Happy path ----

test('happy: well-formed agreement passes both schema and state validation', () => {
  const draft = happyDraft();
  const state = makeState();
  const result = validateAndPrepareClose({ draft, state });
  assert.equal(result.ok, true, `expected ok, got issues: ${JSON.stringify(result.issues)}`);
  assert.ok(result.canonical, 'expected canonical serialization');
  assert.ok(result.summary, 'expected summary');
});

test('happy: no_agreement with unresolved is allowed', () => {
  const draft = happyDraft({
    outcome: 'no_agreement',
    agreed_terms: [],
    unresolved_items: [{ item: 'price gap', reason: 'buyer max $500, seller floor $650' }],
  });
  const state = makeState();
  const result = validateAndPrepareClose({ draft, state });
  assert.equal(result.ok, true);
});

test('canonicalCloseDraft is stable across key ordering', () => {
  const a = happyDraft();
  const b = { ...happyDraft() };
  // shuffle keys
  const reordered = {
    owner_summary: b.owner_summary,
    outcome: b.outcome,
    next_steps: b.next_steps,
    peer_commitments: b.peer_commitments,
    owner_approvals: b.owner_approvals,
    owner_constraints: b.owner_constraints,
    unresolved_items: b.unresolved_items,
    agreed_terms: b.agreed_terms,
  };
  assert.equal(canonicalCloseDraft(a), canonicalCloseDraft(reordered));
});
