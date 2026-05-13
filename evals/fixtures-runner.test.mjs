// evals/fixtures-runner.test.mjs
//
// Phase 4 fixture corpus runner. Walks evals/fixtures/<category>/*.json,
// dispatches each to the appropriate validator, asserts expected
// outcome. Schema in evals/fixtures/README.md.
//
// Per planning room t_d8681c69-e79: each fixture's `release_relevance`
// (high/medium/low) is reported in the run summary so release gates
// can demand high-only pass.

process.env.CLAWROOM_STATE_DIR = `/tmp/clawroom-v4-fixtures-test-${process.pid}`;

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const { validateCloseDraft, validateCloseAgainstState } = await import('../skill/lib/close.mjs');
const { initState } = await import('../skill/lib/state.mjs');
const { makeWatchEvent } = await import('../skill/lib/types.mjs');
const { matchAnchors } = await import('./lib/anchor-match.mjs');

const __filename = fileURLToPath(import.meta.url);
const FIXTURES_ROOT = path.resolve(path.dirname(__filename), 'fixtures');

const CATEGORIES = [
  'close-draft-valid',
  'close-draft-invalid',
  'owner-approval-flow',
  'role-custody',
  'watch-events',
  'owner-context-golden',
];

const ROOM_ID = 't_fixtures_test';

function baseState(overrides = {}) {
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

function loadFixtures(category) {
  const dir = path.join(FIXTURES_ROOT, category);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => {
      const body = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
      return { ...body, _path: path.join(category, f) };
    });
}

// ---------- close-draft-valid ----------

for (const fx of loadFixtures('close-draft-valid')) {
  test(`fixture[close-draft-valid] ${fx.id} — passes hard wall`, () => {
    const state = baseState(fx.state_overrides || {});
    const draftCheck = validateCloseDraft(fx.close_draft);
    assert.equal(draftCheck.ok, true, `validateCloseDraft failed: ${JSON.stringify(draftCheck)}`);
    const stateCheck = validateCloseAgainstState(fx.close_draft, state);
    assert.equal(stateCheck.ok, true, `validateCloseAgainstState failed: ${JSON.stringify(stateCheck)}`);
  });
}

// ---------- close-draft-invalid ----------

for (const fx of loadFixtures('close-draft-invalid')) {
  test(`fixture[close-draft-invalid] ${fx.id} — rejected with ${fx.expected?.reject_reason ?? 'reason'}`, () => {
    const state = baseState(fx.state_overrides || {});
    let firstError = null;
    const draftCheck = validateCloseDraft(fx.close_draft);
    if (!draftCheck.ok) firstError = draftCheck;
    else {
      const stateCheck = validateCloseAgainstState(fx.close_draft, state);
      if (!stateCheck.ok) firstError = stateCheck;
    }
    assert.ok(firstError, `expected reject; both validators passed`);
    if (fx.expected?.reject_reason) {
      const errStr = JSON.stringify(firstError);
      assert.match(errStr, new RegExp(escapeRegExp(fx.expected.reject_reason), 'i'),
        `reject_reason "${fx.expected.reject_reason}" not found in ${errStr}`);
    }
  });
}

// ---------- owner-approval-flow ----------
// These exercise the post-cmd / close-cmd / owner-reply-cmd through their
// validators rather than the CLI itself — testing the contract, not the
// shell-level integration (covered by owner-flow.test.mjs).

for (const fx of loadFixtures('owner-approval-flow')) {
  test(`fixture[owner-approval-flow] ${fx.id} — ${fx.scenario}`, () => {
    const state = baseState(fx.state_overrides || {});
    switch (fx.scenario) {
      case 'cmdPost_blocked_by_pending': {
        // Contract: posting while pending_owner_ask is set should be
        // rejected. We assert by state shape — the actual cmdPost in
        // CLI checks state.pending_owner_ask before relay call.
        assert.ok(state.pending_owner_ask, `state must have pending_owner_ask for this fixture`);
        // If the CLI semantics regress, owner-flow.test.mjs catches it;
        // here we just lock the state-shape contract.
        assert.equal(typeof state.pending_owner_ask?.question_id, 'string');
        break;
      }
      case 'cmdClose_blocked_by_pending_agreement': {
        // close validator must reject outcome=agreement while pending.
        const close = validateCloseAgainstState(fx.close_draft, state);
        assert.equal(close.ok, false);
        const errStr = JSON.stringify(close);
        if (fx.expected?.reason_substring) {
          assert.match(errStr, new RegExp(escapeRegExp(fx.expected.reason_substring), 'i'),
            `reason "${fx.expected.reason_substring}" not in ${errStr}`);
        }
        break;
      }
      case 'cmdClose_passes_with_approval': {
        const close = validateCloseAgainstState(fx.close_draft, state);
        assert.equal(close.ok, true, `expected pass; got ${JSON.stringify(close)}`);
        break;
      }
      case 'cmdClose_rejects_fabricated_approval': {
        const close = validateCloseAgainstState(fx.close_draft, state);
        assert.equal(close.ok, false);
        const errStr = JSON.stringify(close).toLowerCase();
        assert.ok(errStr.includes('approval') || errStr.includes('approv'),
          `expected approval-related reject; got ${errStr}`);
        break;
      }
      case 'cmdClose_rejects_evidence_mismatch':
      case 'cmdClose_rejects_expired_approval':
      case 'cmdClose_rejects_rejected_approval': {
        const close = validateCloseAgainstState(fx.close_draft, state);
        assert.equal(close.ok, false, `expected reject; got ${JSON.stringify(close)}`);
        if (fx.expected?.reason_substring) {
          const errStr = JSON.stringify(close);
          assert.match(errStr, new RegExp(escapeRegExp(fx.expected.reason_substring), 'i'),
            `reason "${fx.expected.reason_substring}" not in ${errStr}`);
        }
        break;
      }
      default:
        throw new Error(`unknown owner-approval-flow scenario: ${fx.scenario}`);
    }
  });
}

// ---------- role-custody ----------

for (const fx of loadFixtures('role-custody')) {
  test(`fixture[role-custody] ${fx.id} — ${fx.scenario}`, () => {
    switch (fx.scenario) {
      case 'initState_cross_role': {
        let threw = false, errMsg = '';
        try {
          initState({
            room_id: fx.input.room_id || ROOM_ID,
            role: fx.input.role,
            ...fx.input.tokens,
          });
        } catch (e) {
          threw = true;
          errMsg = e.message || String(e);
        }
        if (fx.expected.throws) {
          assert.equal(threw, true, `expected throw; got success`);
          if (fx.expected.error_substring) {
            assert.match(errMsg, new RegExp(escapeRegExp(fx.expected.error_substring), 'i'),
              `expected error to contain "${fx.expected.error_substring}"; got "${errMsg}"`);
          }
        } else {
          assert.equal(threw, false, `expected success; threw ${errMsg}`);
        }
        break;
      }
      default:
        throw new Error(`unknown role-custody scenario: ${fx.scenario}`);
    }
  });
}

// ---------- watch-events ----------

for (const fx of loadFixtures('watch-events')) {
  test(`fixture[watch-events] ${fx.id}`, () => {
    let madeEvent = null, threw = false, errMsg = '';
    try {
      madeEvent = makeWatchEvent(fx.raw_event);
    } catch (e) {
      threw = true;
      errMsg = e.message || String(e);
    }
    if (fx.expected_event === '__throws__') {
      assert.equal(threw, true, `expected makeWatchEvent to throw; got ${JSON.stringify(madeEvent)}`);
      if (fx.expected_error_substring) {
        assert.match(errMsg, new RegExp(escapeRegExp(fx.expected_error_substring), 'i'));
      }
      return;
    }
    assert.equal(threw, false, `makeWatchEvent threw: ${errMsg}`);
    assert.deepEqual(madeEvent, fx.expected_event);
    // No text/metadata_json leakage past makeWatchEvent.
    assert.equal(madeEvent.text, undefined);
    assert.equal(madeEvent.metadata_json, undefined);
  });
}

// ---------- owner-context-golden ----------

for (const fx of loadFixtures('owner-context-golden')) {
  test(`fixture[owner-context-golden] ${fx.id} — candidate_correct matches anchors`, () => {
    const result = matchAnchors(fx.candidate_correct, fx);
    assert.equal(result.matched, true, `candidate_correct should match; got reasons: ${JSON.stringify(result.reasons)}`);
  });
  if (fx.candidate_incorrect) {
    test(`fixture[owner-context-golden] ${fx.id} — candidate_incorrect mismatches`, () => {
      const result = matchAnchors(fx.candidate_incorrect, fx);
      assert.equal(result.matched, false, `candidate_incorrect should mismatch but matched`);
      if (fx.candidate_incorrect_reason) {
        const reasons = result.reasons.join(' | ');
        assert.match(reasons, new RegExp(escapeRegExp(fx.candidate_incorrect_reason), 'i'),
          `expected reason "${fx.candidate_incorrect_reason}"; got ${reasons}`);
      }
    });
  }
}

// ---------- schema-contract validation (Phase 4 review P2) ----------
// Every fixture must declare governance fields. If id/filename or
// category/dir drift, this fails fast so the corpus stays trustworthy
// as it grows.

const RELEASE_RELEVANCE_VALUES = new Set(['high', 'medium', 'low']);
const KNOWN_OWNER_APPROVAL_SCENARIOS = new Set([
  'cmdPost_blocked_by_pending',
  'cmdClose_blocked_by_pending_agreement',
  'cmdClose_passes_with_approval',
  'cmdClose_rejects_fabricated_approval',
  'cmdClose_rejects_evidence_mismatch',
  'cmdClose_rejects_expired_approval',
  'cmdClose_rejects_rejected_approval',
]);
const KNOWN_ROLE_CUSTODY_SCENARIOS = new Set([
  'initState_cross_role',
]);

for (const cat of CATEGORIES) {
  for (const fx of loadFixtures(cat)) {
    test(`fixture[${cat}] ${fx.id} — schema contract`, () => {
      const basename = path.basename(fx._path, '.json');
      assert.equal(fx.id, basename, `id "${fx.id}" must equal filename "${basename}"`);
      assert.equal(fx.category, cat, `category "${fx.category}" must equal dir "${cat}"`);
      assert.ok(RELEASE_RELEVANCE_VALUES.has(fx.release_relevance),
        `release_relevance "${fx.release_relevance}" must be one of ${[...RELEASE_RELEVANCE_VALUES].join('|')}`);
      assert.ok(typeof fx.invariant === 'string' && fx.invariant.trim().length > 0,
        `invariant tag required`);
      assert.ok(typeof fx.source === 'string' && fx.source.trim().length > 0,
        `source tag required (file:line / room id / corpus reference — no "I made this up")`);
      assert.ok(typeof fx.description === 'string' && fx.description.trim().length > 0,
        `description required`);

      if (cat === 'owner-approval-flow') {
        assert.ok(KNOWN_OWNER_APPROVAL_SCENARIOS.has(fx.scenario),
          `unknown owner-approval-flow scenario "${fx.scenario}"`);
      }
      if (cat === 'role-custody') {
        assert.ok(KNOWN_ROLE_CUSTODY_SCENARIOS.has(fx.scenario),
          `unknown role-custody scenario "${fx.scenario}"`);
      }
    });
  }
}

// ---------- summary (relevance band reporting) ----------

test('fixture corpus relevance band summary', () => {
  const bands = { high: 0, medium: 0, low: 0, undefined: 0 };
  for (const cat of CATEGORIES) {
    for (const fx of loadFixtures(cat)) {
      const band = fx.release_relevance || 'undefined';
      bands[band] = (bands[band] || 0) + 1;
    }
  }
  process.stderr.write(
    `\n  fixture corpus: high=${bands.high} medium=${bands.medium} low=${bands.low} undefined=${bands.undefined || 0}\n`
  );
  assert.ok(bands.high > 0, `no high-relevance fixtures — release gate has nothing to lock`);
});

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
