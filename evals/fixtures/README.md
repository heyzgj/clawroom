# Phase 4 fixture corpus

Offline regression guards for the 6 categories agreed in planning room
`t_d8681c69-e79`. Every fixture is a single JSON file with a stable
schema. `evals/fixtures-runner.test.mjs` walks each subdirectory, loads
fixtures, and asserts expected outcomes.

## Six categories

| Subdir | What it locks | Active Law |
|---|---|---|
| `close-draft-valid/` | CloseDraft schemas that MUST pass the hard wall | AL3 |
| `close-draft-invalid/` | CloseDraft schemas that MUST fail with specific `reject_reason` | AL3 |
| `owner-approval-flow/` | `ask-owner` / `owner-reply` state machine + post-block-while-pending + evidence/timeout reject paths | AL8 + AL3 |
| `role-custody/` | `initState` cross-role token persist negatives (CLI-level custody covered by `evals/owner-flow.test.mjs`) | AL2 |
| `watch-events/` | Watch event envelope purity — `makeWatchEvent()` accepts metadata-only and throws on text/metadata_json leak (batch-drain + cursor resume covered by `evals/watch-once.test.mjs`) | AL1 |
| `owner-context-golden/` | **Constraint retention scoring** — anchor matcher verifies that an already-built CloseDraft's `owner_constraints` array preserves the field/value anchors declared in the fixture. Phase 5 case 3 reuses the same anchors to score live cold-subagent output. NOT a free-text owner-prompt parser; v3 ParseMandates regex stays archived in `legacy/v3-bridge/bridge.mjs`. | AL3 + product UX |

## Fixture schema (all categories)

Every JSON file has these top-level fields:

```json
{
  "id": "stable-kebab-case-id",
  "description": "one-line",
  "category": "<one of the 6 subdir names>",
  "invariant": "AL3" /* or whichever law */,
  "release_relevance": "high" /* | "medium" | "low" */,
  "source": "where this scenario came from (file:line, room id, or v3 corpus reference)",
  ...category-specific fields...
}
```

`release_relevance` (per Codex): `high` = must pass for release; `medium`
= regression value; `low` = historical interest only. Phase 4 fixture
runner reports counts per relevance band; release gate uses `high`.

## Category-specific fields

### close-draft-valid / close-draft-invalid

```json
{
  "state_overrides": { ...partial state object... },
  "close_draft": { ...full CloseDraft... },
  "expected": {
    "pass": true | false,
    "reject_reason": "string-substring expected in error message"  /* invalid only */
  }
}
```

### owner-approval-flow

```json
{
  "scenario": "cmdPost" | "cmdClose" | "cmdOwnerReply" | "cmdAskOwner",
  "state_overrides": { "pending_owner_ask": {...}, "owner_approvals": [...] },
  "input": { ...scenario-specific payload... },
  "expected": {
    "exit_code": 0 | 5 | 6,
    "reason_substring": "string"
  }
}
```

### role-custody

```json
{
  "scenario": "initState_cross_role" | "post_cross_role" | "watch_cross_role",
  "input": { ...primitive args... },
  "expected": {
    "throws": true | false,
    "error_substring": "string"  /* if throws */
  }
}
```

### watch-events

```json
{
  "raw_event": { /* raw relay event INCLUDING potentially-sensitive fields */ },
  "expected_event": { /* what makeWatchEvent() should produce */ },
  "expected_filter_outcome": "emit" | "drop_self" | "drop_close",
  "current_role": "host" | "guest"
}
```

### owner-context-golden

```json
{
  "owner_prompt": "natural-language owner statement of intent + constraints",
  "expected_mandate_anchors": [
    { "field": "budget_ceiling_jpy", "comparator": "<=" | ">=" | "==", "value": 65000, "type": "number" | "date" | "money" }
  ],
  "expected_owner_constraints_shape": {
    "min_count": 1,
    "requires_owner_approval_when_crossed": true
  },
  "must_not_drop": ["65,000", "yen"],
  "owner_question_allowed": { "boolean": true, "max_count": 1 },
  "forbidden": ["operator_grade_followup_to_owner"],
  "candidate_correct": { ...CloseDraft that satisfies anchors... },
  "candidate_incorrect": { ...CloseDraft that violates anchors... },
  "candidate_incorrect_reason": "string-substring expected from anchor mismatch report"
}
```

Phase 4 tests the anchor-matcher (deterministic) against
`candidate_correct` (must MATCH) and `candidate_incorrect` (must
MISMATCH with the named reason). Phase 5 case 3 uses the SAME
`owner_prompt + expected_anchors` to score a live agent's actual
CloseDraft output.

## Naming convention

`<id>.json` where `<id>` is kebab-case, scoped by category. Examples:
- `close-draft-valid/agreement-with-owner-approval.json`
- `close-draft-invalid/fabricated-approval-bypass.json`
- `owner-context-golden/budget-ceiling-jpy-explicit.json`

## How to add a fixture

1. Pick category, copy a sibling fixture, edit.
2. Run `node --test evals/fixtures-runner.test.mjs` — new fixture
   should be picked up automatically.
3. If it fails: either the fixture is wrong, or the validator surfaced
   a real bug — diagnose, don't paper over.
4. Each fixture's `source` field should point at a concrete origin:
   v3 bridge.mjs line range, a Codex review-pass finding, an Active
   Law, or a Phase 5 case requirement. No "I made this up."

## Phase 4 close criterion

Per planning close `t_d8681c69-e79`:
- All fixtures pass with their declared `expected`
- Both sides agree the offline guardrails are enough to enter Phase 4.5
- No v3 regex product layer reintroduced (anchor-matcher is structural
  schema matching, not regex inference)
