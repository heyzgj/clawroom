// evals/lib/anchor-match.mjs
//
// Deterministic structural anchor-matcher for owner-context-golden
// fixtures. Phase 4 uses this offline; Phase 5 case 3 will reuse it to
// score a live cold-subagent's CloseDraft against the same golden.
//
// What this is NOT: a regex layer trying to infer constraints from
// free-text owner prompts. The v3 bridge did that (legacy/v3-bridge/
// bridge.mjs:177-265 ParseMandates) and Phase 2 retro Lesson BQ called
// it out as maintainer truth blending. This module only validates that
// an already-built CloseDraft's owner_constraints array satisfies
// declared schema anchors.
//
// Phase 4 review room t_2b22838a-7de P1: original implementation used
// "last numeric in constraint string" which false-positives strings
// like "deadline <= 2026-06-17" (parses "17", satisfies "<= 20260615")
// and "budget_ceiling_usd=900 (was 650)" (parses "650", satisfies
// "<= 65000"). Fixed: field-aware extraction. The matcher looks for
// the FIRST numeric token AFTER the field name, with date-typed
// anchors normalized to YYYYMMDD comparison.

/**
 * @typedef {Object} MandateAnchor
 * @property {string} field - constraint name (e.g. "budget_ceiling_jpy", "deadline")
 * @property {"=="|"="|"<="|">="|"<"|">"} comparator
 * @property {number} value - target numeric value; for date type, YYYYMMDD-form
 * @property {"number"|"date"|"money"} [type] - default "number"
 */

/**
 * Match a CloseDraft against owner-context-golden anchors.
 *
 * @param {object} closeDraft - candidate CloseDraft.
 * @param {object} fixture - owner-context-golden fixture body.
 * @returns {{ matched: boolean, reasons: string[] }}
 */
export function matchAnchors(closeDraft, fixture) {
  const reasons = [];
  const constraints = Array.isArray(closeDraft?.owner_constraints)
    ? closeDraft.owner_constraints
    : [];

  // Anchor 1: every expected_mandate_anchors entry must be matched by
  // at least one owner_constraints entry whose `constraint` string
  // names the field AND whose value satisfies the comparator at the
  // FIELD-ADJACENT position (not "last in string").
  //
  // Per anchor, also check whether the shape requires
  // requires_owner_approval=true and, if so, that AT LEAST ONE matching
  // constraint for THIS specific anchor has it. Per-anchor (not global)
  // because the global form lets an unrelated approval-required
  // constraint paper over a real mandate that's been marked
  // approval=false (Phase 4 review t_2b22838a-7de pass 2 finding).
  const shape = fixture.expected_owner_constraints_shape || {};
  const needsApprovalPerAnchor = shape.requires_owner_approval_when_crossed === true;
  for (const anchor of fixture.expected_mandate_anchors || []) {
    const matching = constraints.filter((c) =>
      typeof c?.constraint === 'string' &&
      c.constraint.includes(anchor.field) &&
      satisfiesAnchor(c.constraint, anchor)
    );
    if (matching.length === 0) {
      reasons.push(
        `missing_anchor: no owner_constraints entry matches ${anchor.field} ${anchor.comparator} ${anchor.value}${anchor.type ? ` (type=${anchor.type})` : ''}`
      );
      continue;
    }
    if (needsApprovalPerAnchor) {
      const matchingNeedsApproval = matching.some(
        (c) => c.requires_owner_approval === true
      );
      if (!matchingNeedsApproval) {
        reasons.push(
          `shape_violation: anchor "${anchor.field}" matched but no matching constraint has requires_owner_approval=true (per-anchor scope — unrelated approval-required constraints do not count)`
        );
      }
    }
  }

  // Anchor 2: owner_constraints shape — min_count only. The
  // requires_owner_approval check moved into the per-anchor loop above
  // so it can't be globally faked.
  if (typeof shape.min_count === 'number' && constraints.length < shape.min_count) {
    reasons.push(
      `shape_violation: owner_constraints.length=${constraints.length} < min_count=${shape.min_count}`
    );
  }

  // Anchor 3: must_not_drop — exact substrings that must appear
  // somewhere in owner_constraints[].constraint or owner_summary.
  if (Array.isArray(fixture.must_not_drop)) {
    const haystack = [
      ...constraints.map((c) => c?.constraint || ''),
      closeDraft?.owner_summary || '',
    ].join(' ');
    for (const required of fixture.must_not_drop) {
      if (!haystack.includes(required)) {
        reasons.push(`must_not_drop: required substring "${required}" not found in constraints or owner_summary`);
      }
    }
  }

  return { matched: reasons.length === 0, reasons };
}

/**
 * Field-aware value extraction + comparator check. Look at the
 * constraint string AFTER the field name. Take the first plausible
 * value (number for number/money types; YYYY-MM-DD or YYYYMMDD for
 * date type). Compare against the anchor.
 *
 * The "after the field name" anchoring is what defeats the
 * last-numeric exploits Codex flagged in Phase 4 review:
 *
 *   "budget_ceiling_usd=900 (was 650)"
 *      -> field="budget_ceiling_usd"; after-field segment="=900 (was 650)";
 *      -> first numeric="900"; "900 <= 650"? FALSE. Correct.
 *
 *   "deadline <= 2026-06-17"
 *      -> field="deadline"; after-field segment="<= 2026-06-17";
 *      -> date normalize "2026-06-17" -> 20260617;
 *      -> "20260617 <= 20260615"? FALSE. Correct.
 *
 * @param {string} constraintStr
 * @param {MandateAnchor} anchor
 * @returns {boolean}
 */
function satisfiesAnchor(constraintStr, anchor) {
  const str = String(constraintStr);
  const fieldIdx = str.indexOf(anchor.field);
  if (fieldIdx < 0) return false;
  const afterField = str.slice(fieldIdx + anchor.field.length);

  const type = anchor.type || 'number';
  let value;
  if (type === 'date') {
    value = firstDateAsYyyymmdd(afterField);
  } else {
    // number | money — both use first numeric token after field
    value = firstNumber(afterField);
  }
  if (value === null) return false;
  return compare(value, anchor.comparator, anchor.value);
}

/**
 * First date in YYYYMMDD form. Accepts ISO `2026-06-17` and bare
 * `20260617`. Returns null if no recognizable date.
 *
 * @param {string} s
 * @returns {number | null}
 */
function firstDateAsYyyymmdd(s) {
  const iso = s.match(/(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (iso) {
    const yyyy = Number(iso[1]);
    const mm = Number(iso[2]);
    const dd = Number(iso[3]);
    if (Number.isFinite(yyyy) && Number.isFinite(mm) && Number.isFinite(dd)) {
      return yyyy * 10000 + mm * 100 + dd;
    }
  }
  const bare = s.match(/\b(\d{8})\b/);
  if (bare) {
    const n = Number(bare[1]);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/**
 * First numeric token after position 0. Strips commas. Returns null
 * if none parseable.
 *
 * Picks the FIRST number, not the last — this is the field-adjacent
 * value semantics. Anything later in the string (parenthetical notes,
 * historical values) is ignored for comparator purposes.
 *
 * @param {string} s
 * @returns {number | null}
 */
function firstNumber(s) {
  const m = s.match(/(-?\d[\d,]*\.?\d*)/);
  if (!m) return null;
  const v = Number(m[1].replace(/,/g, ''));
  return Number.isFinite(v) ? v : null;
}

/**
 * @param {number} actual
 * @param {string} cmp
 * @param {number} expected
 * @returns {boolean}
 */
function compare(actual, cmp, expected) {
  switch (cmp) {
    case '==':
    case '=':
      return actual === expected;
    case '<=':
      return actual <= expected;
    case '>=':
      return actual >= expected;
    case '<':
      return actual < expected;
    case '>':
      return actual > expected;
    default:
      return false;
  }
}
