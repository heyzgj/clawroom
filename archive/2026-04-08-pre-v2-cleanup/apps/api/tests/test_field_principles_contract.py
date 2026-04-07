from __future__ import annotations

from pathlib import Path

import pytest

from clawroom_client_core import build_owner_reply_prompt, build_room_reply_prompt, evaluate_room_quality
from roombridge_core.models import OutcomeContract


ROOT = Path(__file__).resolve().parents[3]


def _room_with_contract(contract: dict) -> dict:
    return {
        "topic": "Owner handoff",
        "goal": "Produce a usable decision packet",
        "required_fields": ["decision", "ranked_options", "success_metrics", "owner_actions"],
        "fields": {},
        "outcome_contract": contract,
    }


def test_outcome_contract_accepts_field_principles_dict() -> None:
    contract = OutcomeContract(
        field_principles={
            "decision": "Must be specific.",
            "owner_actions": "Must include an action this week.",
        }
    )
    assert contract.field_principles["decision"] == "Must be specific."
    assert contract.scenario_hint is None


@pytest.mark.parametrize(
    "payload",
    [
        {"field_principles": {f"field_{idx}": "ok" for idx in range(65)}},
        {"field_principles": {"decision": "x" * 501}},
    ],
)
def test_outcome_contract_rejects_oversized_field_principles(payload: dict) -> None:
    with pytest.raises(ValueError):
        OutcomeContract(**payload)


def test_outcome_contract_expands_scenario_hint_to_field_principles() -> None:
    contract = OutcomeContract(scenario_hint="decision_packet")
    assert contract.field_principles["decision"].startswith("Must be a specific actionable decision")
    assert contract.field_principles["owner_actions"].startswith("Must include at least one action")


def test_explicit_field_principles_override_scenario_hint_expansion() -> None:
    contract = OutcomeContract(
        scenario_hint="decision_packet",
        field_principles={"decision": "Use this exact custom guidance."},
    )
    assert contract.field_principles == {"decision": "Use this exact custom guidance."}


def test_unknown_scenario_hint_is_ignored() -> None:
    contract = OutcomeContract(scenario_hint="unknown_mode")
    assert contract.scenario_hint == "unknown_mode"
    assert contract.field_principles == {}


def test_room_prompt_includes_field_quality_guidance_block() -> None:
    prompt = build_room_reply_prompt(
        role="responder",
        room=_room_with_contract(
            {
                "field_principles": {
                    "decision": "Must be specific.",
                    "ranked_options": "Must list at least 2 options, with the recommendation first.",
                }
            }
        ),
        self_name="guest",
        latest_event=None,
        has_started=True,
    )
    assert "Field quality guidance (aim for this, not enforced):" in prompt
    assert "decision: Must be specific." in prompt
    assert "ranked_options: Must list at least 2 options" in prompt
    assert "Fill fields as self-contained handoff text the owner could forward unchanged." in prompt


def test_initiator_opening_prompt_pushes_early_packet_draft() -> None:
    prompt = build_room_reply_prompt(
        role="initiator",
        room=_room_with_contract({"field_principles": {"decision": "Must be specific."}}),
        self_name="host",
        latest_event=None,
        has_started=False,
    )
    assert "On the opening turn, draft as many required fields as you can from the owner context" in prompt
    assert "Use your one direct question only for the biggest remaining gap" in prompt


def test_owner_reply_prompt_includes_field_quality_guidance_block() -> None:
    prompt = build_owner_reply_prompt(
        room=_room_with_contract({"field_principles": {"owner_actions": "Must include one action this week."}}),
        self_name="host",
        role="initiator",
        owner_req_id="owner_req_1",
        owner_text="Use the lighter rollout.",
    )
    assert "Field quality guidance (aim for this, not enforced):" in prompt
    assert "owner_actions: Must include one action this week." in prompt
    assert "Write fills so the owner could forward them unchanged." in prompt


def test_prompt_omits_field_guidance_when_contract_has_none() -> None:
    prompt = build_room_reply_prompt(
        role="initiator",
        room=_room_with_contract({}),
        self_name="host",
        latest_event=None,
        has_started=False,
    )
    assert "Field quality guidance (aim for this, not enforced):" not in prompt


def test_evaluate_room_quality_detects_missing_placeholder_and_truncated_fields() -> None:
    evaluation = evaluate_room_quality(
        fields={
            "decision": "TBD",
            "ranked_options": "",
            "success_metrics": "x" * 1900,
            "owner_actions": "Need more discussion.",
        },
        required_fields=["decision", "ranked_options", "success_metrics", "owner_actions"],
    )
    assert evaluation["usable"] is False
    assert evaluation["checks"]["fields_complete"] is False
    assert "decision" in evaluation["details"]["placeholder_fields"]
    assert "success_metrics" in evaluation["details"]["truncated_fields"]


def test_evaluate_room_quality_applies_field_principle_heuristics() -> None:
    evaluation = evaluate_room_quality(
        fields={
            "decision": "Run a two-week BTC daily-summary pilot through ClawRoom.",
            "ranked_options": "1. Run a two-week BTC daily-summary pilot.\n2. Wait one quarter and gather more manual feedback.",
            "success_metrics": "Within 2 weeks, reach >=80% completion and collect positive operator feedback.",
            "owner_actions": "Today assign one workflow owner; this week start the pilot and schedule a week-2 review.",
        },
        required_fields=["decision", "ranked_options", "success_metrics", "owner_actions"],
        field_principles={
            "decision": "Must be a specific actionable decision.",
            "ranked_options": "Must list at least 2 options, with the chosen recommendation first.",
            "success_metrics": "Must include at least one measurable metric with a timeline.",
            "owner_actions": "Must include at least one action the owner can take this week.",
        },
    )
    assert evaluation["usable"] is True
    assert evaluation["checks"]["principles_passed"] is True


def test_evaluate_room_quality_accepts_inline_ranked_options() -> None:
    evaluation = evaluate_room_quality(
        fields={
            "decision": "Run a two-week scoped ClawRoom pilot on the daily BTC+ETH market brief workflow.",
            "ranked_options": (
                "1. RUN scoped pilot (RECOMMENDED) — Test ClawRoom on the daily BTC+ETH brief for 2 weeks. "
                "2. WAIT — Keep solo daily reports for now and revisit later. "
                "3. EXPAND — Apply ClawRoom to multiple workflows immediately."
            ),
            "success_metrics": "Within 2 weeks, deliver by 07:00 GMT+8 on >=90% of weekdays and keep turn count <=4.",
            "owner_actions": "This week confirm the pilot workflow and schedule a week-1 review.",
        },
        required_fields=["decision", "ranked_options", "success_metrics", "owner_actions"],
        field_principles={
            "decision": "Must be a specific actionable decision.",
            "ranked_options": "Must list at least 2 options, with the chosen recommendation first.",
            "success_metrics": "Must include at least one measurable metric with a timeline.",
            "owner_actions": "Must include at least one action the owner can take this week.",
        },
    )
    assert evaluation["checks"]["principles_passed"] is True
    assert evaluation["usable"] is True


def test_evaluate_room_quality_detects_internal_jargon_for_owner_facing_fields() -> None:
    evaluation = evaluate_room_quality(
        fields={
            "decision": "Run it in compatibility mode after runnerd fallback succeeds.",
        },
        required_fields=["decision"],
        field_principles={
            "decision": "Must be a specific actionable decision in owner-facing language with no internal jargon.",
        },
    )
    assert evaluation["checks"]["principles_passed"] is False
    assert evaluation["details"]["principle_checks"]["decision"]["matched_checks"] == [
        "specific_content",
        "owner_friendly_language",
    ]


def test_evaluate_room_quality_requires_pass_fail_signal_when_requested() -> None:
    evaluation = evaluate_room_quality(
        fields={
            "experiment_plan": "This week send the new invite format to five users and watch what happens.",
        },
        required_fields=["experiment_plan"],
        field_principles={
            "experiment_plan": "Must include one action this week and one pass/fail signal in owner-facing language.",
        },
    )
    assert evaluation["checks"]["principles_passed"] is False


def test_evaluate_room_quality_requires_decision_to_match_top_ranked_option() -> None:
    evaluation = evaluate_room_quality(
        fields={
            "decision": "Wait and do not run a pilot this month.",
            "ranked_options": (
                "1. Run a two-week scoped pilot now. "
                "2. Wait one month and collect more solo feedback."
            ),
        },
        required_fields=["decision", "ranked_options"],
        field_principles={
            "decision": "Must be a specific actionable decision in owner-facing language.",
            "ranked_options": "Must list at least 2 options, with the chosen recommendation first.",
        },
    )
    assert evaluation["checks"]["cross_field_consistent"] is False
    assert evaluation["details"]["cross_field_checks"]["decision_matches_top_option"]["passed"] is False


def test_evaluate_room_quality_accepts_continue_pause_wording() -> None:
    evaluation = evaluate_room_quality(
        fields={
            "weekly_check": (
                "Friday morning, if all three owners report done, we continue into next week's plan. "
                "If any work is still unverified, we pause new work and close carry-over first."
            ),
        },
        required_fields=["weekly_check"],
        field_principles={
            "weekly_check": "Must define one simple progress check with a clear continue or pause signal for this week.",
        },
    )
    assert evaluation["checks"]["principles_passed"] is True


def test_edge_worker_contract_mentions_field_principles_and_scenario_hint() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")
    assert "field_principles: Record<string, string>;" in source
    assert "scenario_hint: string | null;" in source
    assert "const SCENARIO_PRESETS" in source
    assert "function normalizeFieldPrinciples" in source
