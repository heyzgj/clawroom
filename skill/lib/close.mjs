// skill/lib/close.mjs
// CloseDraft schema validator + state validator. This is the v4 "hard wall"
// per invariants 10 + 13. clawroom close MUST call validateCloseDraft +
// validateCloseAgainstState BEFORE relay POST. Failing either => CLI rejects;
// the relay /close itself stays mechanical (invariant 4).
//
// The 5 reject conditions agreed in t_bf866856-df0 reflection sync:
//   1. invalid CloseDraft schema
//   2. outcome=agreement while pending_owner_ask is set
//   3. agreement commitment cites an owner_constraint with
//      requires_owner_approval=true but has no matching owner_approval
//   4. rejected/expired owner ask presented as approved
//   5. missing provenance for any peer commitment or owner approval

import {
  CLOSE_OUTCOMES,
  APPROVAL_SOURCES,
  MAX_CLOSE_SUMMARY_CHARS,
} from './types.mjs';

/** @typedef {import('./types.mjs').CloseDraft} CloseDraft */
/** @typedef {import('./types.mjs').RoomState} RoomState */
/** @typedef {import('./types.mjs').OwnerApproval} OwnerApproval */

/**
 * Stable JSON serialization (sorted keys). Used by idempotency-key
 * derivation and by `clawroom close` over-the-wire summary.
 *
 * @param {CloseDraft} draft
 * @returns {string}
 */
export function canonicalCloseDraft(draft) {
  return JSON.stringify(sortKeys(draft));
}

function sortKeys(v) {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === 'object') {
    /** @type {Record<string, unknown>} */
    const out = {};
    for (const key of Object.keys(v).sort()) {
      out[key] = sortKeys(/** @type {any} */ (v)[key]);
    }
    return out;
  }
  return v;
}

/**
 * @typedef {Object} ValidationIssue
 * @property {string} code   - stable enum string, e.g. 'schema_outcome_invalid'
 * @property {string} message
 * @property {string} [path] - JSON path within the draft
 */

/**
 * @typedef {Object} ValidationResult
 * @property {boolean} ok
 * @property {ValidationIssue[]} issues
 */

function issue(code, message, path) {
  return { code, message, ...(path ? { path } : {}) };
}

/**
 * Schema-only check. Fails reject condition 1.
 *
 * @param {unknown} raw
 * @returns {ValidationResult}
 */
export function validateCloseDraft(raw) {
  const issues = /** @type {ValidationIssue[]} */ ([]);
  if (!raw || typeof raw !== 'object') {
    return { ok: false, issues: [issue('schema_not_object', 'CloseDraft must be an object')] };
  }
  const d = /** @type {Record<string, unknown>} */ (raw);

  if (!CLOSE_OUTCOMES.includes(/** @type {any} */ (d.outcome))) {
    issues.push(issue('schema_outcome_invalid', `outcome must be one of ${CLOSE_OUTCOMES.join('|')}`, 'outcome'));
  }

  const arrFields = ['agreed_terms', 'unresolved_items', 'owner_constraints', 'peer_commitments', 'owner_approvals', 'next_steps'];
  for (const f of arrFields) {
    if (!Array.isArray(d[f])) issues.push(issue('schema_array_required', `${f} must be an array`, f));
  }

  if (typeof d.owner_summary !== 'string' || !d.owner_summary.trim()) {
    issues.push(issue('schema_owner_summary_required', 'owner_summary must be a non-empty string', 'owner_summary'));
  } else if (d.owner_summary.length > MAX_CLOSE_SUMMARY_CHARS) {
    issues.push(issue('schema_owner_summary_too_long', `owner_summary > ${MAX_CLOSE_SUMMARY_CHARS} chars`, 'owner_summary'));
  }

  // Field-level shape checks (only run if the array itself is well-formed)
  if (Array.isArray(d.agreed_terms)) {
    d.agreed_terms.forEach((t, i) => {
      if (!t || typeof t !== 'object') return issues.push(issue('schema_agreed_term_bad', 'not an object', `agreed_terms[${i}]`));
      if (typeof t.term !== 'string' || !t.term) issues.push(issue('schema_agreed_term_missing_term', 'term required', `agreed_terms[${i}].term`));
      if (typeof t.value !== 'string') issues.push(issue('schema_agreed_term_missing_value', 'value required', `agreed_terms[${i}].value`));
      if (typeof t.provenance !== 'string' || !t.provenance) issues.push(issue('schema_agreed_term_missing_provenance', 'provenance required', `agreed_terms[${i}].provenance`));
    });
  }

  if (Array.isArray(d.peer_commitments)) {
    d.peer_commitments.forEach((c, i) => {
      if (!c || typeof c !== 'object') return issues.push(issue('schema_commitment_bad', 'not an object', `peer_commitments[${i}]`));
      if (typeof c.commitment !== 'string' || !c.commitment) issues.push(issue('schema_commitment_missing_commitment', 'commitment required', `peer_commitments[${i}].commitment`));
      if (typeof c.provenance !== 'string' || !c.provenance) {
        // Reject condition 5: missing provenance for any peer commitment.
        issues.push(issue('missing_provenance_commitment', 'provenance required for every peer commitment', `peer_commitments[${i}].provenance`));
      }
    });
  }

  if (Array.isArray(d.owner_constraints)) {
    d.owner_constraints.forEach((c, i) => {
      if (!c || typeof c !== 'object') return issues.push(issue('schema_constraint_bad', 'not an object', `owner_constraints[${i}]`));
      if (typeof c.constraint !== 'string' || !c.constraint) issues.push(issue('schema_constraint_missing_constraint', 'constraint required', `owner_constraints[${i}].constraint`));
      if (c.source !== 'create' && c.source !== 'join') issues.push(issue('schema_constraint_bad_source', "source must be 'create' or 'join'", `owner_constraints[${i}].source`));
      if (typeof c.requires_owner_approval !== 'boolean') issues.push(issue('schema_constraint_bad_requires_owner_approval', 'requires_owner_approval must be boolean', `owner_constraints[${i}].requires_owner_approval`));
    });
  }

  if (Array.isArray(d.owner_approvals)) {
    d.owner_approvals.forEach((a, i) => {
      if (!a || typeof a !== 'object') return issues.push(issue('schema_approval_bad', 'not an object', `owner_approvals[${i}]`));
      if (typeof a.question_id !== 'string' || !a.question_id) issues.push(issue('schema_approval_missing_question_id', 'question_id required', `owner_approvals[${i}].question_id`));
      if (a.decision !== 'approve' && a.decision !== 'reject') issues.push(issue('schema_approval_bad_decision', "decision must be 'approve' or 'reject'", `owner_approvals[${i}].decision`));
      if (!APPROVAL_SOURCES.includes(/** @type {any} */ (a.source))) issues.push(issue('schema_approval_bad_source', `source must be one of ${APPROVAL_SOURCES.join('|')}`, `owner_approvals[${i}].source`));
      if (typeof a.ts !== 'string' || !a.ts) issues.push(issue('schema_approval_missing_ts', 'ts required', `owner_approvals[${i}].ts`));
      if (typeof a.evidence !== 'string') {
        // Reject condition 5: missing provenance for owner approvals.
        issues.push(issue('missing_provenance_approval', 'evidence (provenance) required for every owner approval', `owner_approvals[${i}].evidence`));
      }
    });
  }

  if (Array.isArray(d.unresolved_items)) {
    d.unresolved_items.forEach((u, i) => {
      if (!u || typeof u !== 'object') return issues.push(issue('schema_unresolved_bad', 'not an object', `unresolved_items[${i}]`));
      if (typeof u.item !== 'string' || !u.item) issues.push(issue('schema_unresolved_missing_item', 'item required', `unresolved_items[${i}].item`));
      if (typeof u.reason !== 'string' || !u.reason) issues.push(issue('schema_unresolved_missing_reason', 'reason required', `unresolved_items[${i}].reason`));
    });
  }

  // Outcome consistency: partial requires explicit unresolved blocker.
  if (d.outcome === 'partial' && Array.isArray(d.unresolved_items) && d.unresolved_items.length === 0) {
    issues.push(issue('outcome_partial_requires_unresolved', 'partial outcome requires at least one unresolved_items entry', 'unresolved_items'));
  }

  return { ok: issues.length === 0, issues };
}

/**
 * Reject conditions 2, 3, 4, 6. Run AFTER validateCloseDraft.ok === true.
 *
 * The key correctness rule (Codex pass 2 P1.A): every `draft.owner_approvals[]`
 * MUST have a matching record in `state.owner_approvals[]` (same question_id +
 * decision). Approvals are authoritative in STATE, not in the draft text.
 * A draft cannot fabricate an approval just by writing one into the JSON.
 *
 * @param {CloseDraft} draft
 * @param {RoomState} state
 * @returns {ValidationResult}
 */
export function validateCloseAgainstState(draft, state) {
  const issues = /** @type {ValidationIssue[]} */ ([]);

  // Reject condition 2: agreement while pending_owner_ask is set.
  if (draft.outcome === 'agreement' && state.pending_owner_ask) {
    issues.push(issue(
      'pending_ask_blocks_agreement',
      `cannot outcome=agreement while pending_owner_ask "${state.pending_owner_ask.question_id}" is unresolved`,
    ));
  }

  // Reject condition 6 (Codex P1.A): every draft approval MUST be backed by a
  // matching record in state.owner_approvals. Without this, the close hard wall
  // is bypassable by fabricating an `owner_approvals[]` entry inline.
  // Match key: question_id + decision (evidence is free-form context, but the
  // commitment of who approved what lives in state).
  for (const a of draft.owner_approvals) {
    const stateRecord = state.owner_approvals.find((x) => x.question_id === a.question_id);
    if (!stateRecord) {
      issues.push(issue(
        'fabricated_approval',
        `draft owner_approvals contains question_id "${a.question_id}" but no record in state.owner_approvals — approvals must be backed by state`,
      ));
      continue;
    }
    // Reject condition 4: state decision differs from draft decision.
    if (stateRecord.decision !== a.decision) {
      issues.push(issue(
        'approval_decision_mismatch',
        `question_id "${a.question_id}" presented as ${a.decision} but state has ${stateRecord.decision}`,
      ));
    }
    // Codex pass 3 P2: strict-match source + evidence so draft can't reshape
    // a real approval into a misleading owner-facing summary. ts is allowed
    // to differ (e.g., draft serialization timestamp vs original recording).
    if (stateRecord.source !== a.source) {
      issues.push(issue(
        'approval_source_mismatch',
        `question_id "${a.question_id}" source "${a.source}" does not match state record source "${stateRecord.source}"`,
      ));
    }
    if (stateRecord.evidence !== a.evidence) {
      issues.push(issue(
        'approval_evidence_mismatch',
        `question_id "${a.question_id}" evidence differs from state record — owner_approvals must mirror state verbatim (use owner_summary for owner-facing rewording)`,
      ));
    }
    // Timeout check: if state's pending_owner_ask had this question_id with a
    // past timeout_at and the draft presents as approve, that's a violation.
    if (state.pending_owner_ask &&
        state.pending_owner_ask.question_id === a.question_id &&
        a.decision === 'approve' &&
        Date.now() >= Date.parse(state.pending_owner_ask.timeout_at)) {
      issues.push(issue(
        'approval_after_timeout',
        `question_id "${a.question_id}" timed out at ${state.pending_owner_ask.timeout_at} — cannot be presented as approve`,
      ));
    }
  }

  // Reject condition 3 (now state-backed): for each requires_owner_approval
  // constraint, there must be at least one APPROVED record in STATE whose
  // evidence references the constraint. Previously this checked draft.owner_approvals
  // which is exactly the bypass surface Codex flagged.
  if (draft.outcome === 'agreement') {
    for (const c of draft.owner_constraints) {
      if (!c.requires_owner_approval) continue;
      const hasStateApproval = state.owner_approvals.some(
        (a) => a.decision === 'approve' && a.evidence && a.evidence.includes(c.constraint)
      );
      if (!hasStateApproval) {
        issues.push(issue(
          'missing_approval_for_constraint',
          `constraint "${c.constraint}" requires owner approval but no matching approve record in STATE.owner_approvals with evidence referencing the constraint`,
        ));
      }
    }
  }

  return { ok: issues.length === 0, issues };
}

/**
 * Full close validation: schema + state. Returns the canonical summary
 * payload to POST and the canonical hash for idempotency. Caller does
 * NOT POST if `ok === false`.
 *
 * @param {Object} args
 * @param {CloseDraft} args.draft
 * @param {RoomState} args.state
 * @returns {{ ok: boolean, issues: ValidationIssue[], canonical?: string, summary?: string }}
 */
export function validateAndPrepareClose({ draft, state }) {
  const schema = validateCloseDraft(draft);
  if (!schema.ok) return { ok: false, issues: schema.issues };
  const semantic = validateCloseAgainstState(draft, state);
  if (!semantic.ok) return { ok: false, issues: semantic.issues };
  const canonical = canonicalCloseDraft(draft);
  return { ok: true, issues: [], canonical, summary: canonical };
}
