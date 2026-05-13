// evals/harness/score.mjs
//
// Phase 4.5 scoring rubric. Per planning room t_d8681c69-e79:
//   MAGICAL  — subagent runs to close with ZERO owner clarification beyond initial prompt.
//   GOOD     — 1-2 clarifications within Quick Pipeline branches we already handle.
//   OK       — 3-4 clarifications, no schema errors.
//   FAILED   — schema errors / pending_owner_ask hard-block / unable to detect create vs join / hung session.
//
// Case 3 release-green requires GOOD or MAGICAL (OK is diagnostic only) AND no invalidating context leak.
// "Invalidating context leak" comes from temp-skill.mjs#checkContextLeak; FAILED regardless of close state if violated.

/**
 * @typedef {Object} ScoreInput
 * @property {number} owner_clarification_count - how many OWNER_ASK packets the subagent emitted beyond initial prompt
 * @property {boolean} closed_mutually - did the room reach mutual_close
 * @property {boolean} close_validator_passed - did the close hard wall accept BOTH sides' CloseDraft
 * @property {boolean} pending_blocked_hard - did the run hit AL8 hard-block (pending blocks post)
 * @property {boolean} create_join_confused - did the subagent try create when it should join, or vice versa
 * @property {boolean} hung - did the subagent exit timeout / never produce close
 * @property {boolean} schema_errors_present - any of the 6 close-reject conditions fired
 * @property {boolean} context_leak - subagent referenced anything outside allowed_context (from temp-skill.mjs#checkContextLeak)
 * @property {string[]} [context_leak_violations] - specific violations
 * @property {string[]} [other_failures]
 */

/**
 * @typedef {Object} ScoreResult
 * @property {"MAGICAL"|"GOOD"|"OK"|"FAILED"} score
 * @property {string[]} reasons - one-line reasoning per signal that contributed
 * @property {boolean} release_green - true only if score in {GOOD, MAGICAL} AND no context_leak
 * @property {"product_bug"|"runtime_limitation"|"e2e_flake"|"pass"} classification
 */

/**
 * @param {ScoreInput} input
 * @returns {ScoreResult}
 */
export function scoreRun(input) {
  const reasons = [];

  // Hard FAILED triggers (override anything else).
  if (input.context_leak) {
    reasons.push('context_leak: subagent referenced paths outside allowed_context (invalidates release evidence)');
    return {
      score: 'FAILED',
      reasons: reasons.concat(input.context_leak_violations || []),
      release_green: false,
      classification: 'product_bug',
    };
  }
  if (input.hung) {
    reasons.push('hung: subagent did not reach close within timeout');
    return { score: 'FAILED', reasons, release_green: false, classification: 'runtime_limitation' };
  }
  if (input.create_join_confused) {
    reasons.push('create_join_confused: subagent invoked the wrong primitive for the owner prompt');
    return { score: 'FAILED', reasons, release_green: false, classification: 'product_bug' };
  }
  if (input.schema_errors_present) {
    reasons.push('schema_errors_present: at least one of the 6 close-reject conditions fired on this run');
    return { score: 'FAILED', reasons, release_green: false, classification: 'product_bug' };
  }
  if (input.pending_blocked_hard && !input.closed_mutually) {
    reasons.push('pending_blocked_hard without subsequent close: AL8 fired and the run did not recover');
    return { score: 'FAILED', reasons, release_green: false, classification: 'product_bug' };
  }
  if (!input.closed_mutually) {
    reasons.push('no_mutual_close: room did not reach closed state on both sides');
    return { score: 'FAILED', reasons, release_green: false, classification: 'runtime_limitation' };
  }
  if (!input.close_validator_passed) {
    reasons.push('close_validator_rejected: at least one side closed but validator complained');
    return { score: 'FAILED', reasons, release_green: false, classification: 'product_bug' };
  }

  // Past the hard-fail wall: clarification count drives the band.
  const n = input.owner_clarification_count ?? 0;
  if (n === 0) {
    reasons.push('zero_owner_clarifications: subagent reached close on only the initial prompt');
    return { score: 'MAGICAL', reasons, release_green: true, classification: 'pass' };
  }
  if (n <= 2) {
    reasons.push(`${n} owner clarification(s): within Quick Pipeline branches`);
    return { score: 'GOOD', reasons, release_green: true, classification: 'pass' };
  }
  if (n <= 4) {
    reasons.push(`${n} owner clarifications: above naive-owner expectation but no schema error`);
    return {
      score: 'OK',
      reasons,
      release_green: false,
      classification: 'product_bug',
    };
  }
  reasons.push(`${n} owner clarifications: subagent is interrogating the owner, not negotiating with the peer`);
  return { score: 'FAILED', reasons, release_green: false, classification: 'product_bug' };
}

/**
 * Convenience predicate matching Phase 5 case-3 release criterion:
 * GOOD or MAGICAL AND no invalidating context leak.
 *
 * @param {ScoreResult} result
 * @returns {boolean}
 */
export function isCase3ReleaseGreen(result) {
  return result.release_green && (result.score === 'GOOD' || result.score === 'MAGICAL');
}
