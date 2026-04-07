from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts" / "claude_telegram_journey_e2e.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("claude_telegram_journey_e2e", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _room(required_fields: list[str], values: dict[str, str]) -> dict:
    return {
        "required_fields": required_fields,
        "fields": {key: {"value": value} for key, value in values.items()},
    }


def test_should_send_final_done_when_counterpart_already_filled_required_fields() -> None:
    module = _load_script_module()
    room = _room(
        ["top_issue", "why_this_first", "experiment_plan"],
        {
            "top_issue": "Invite fragmentation silently breaks entry.",
            "why_this_first": "It blocks the 99% path before work even starts.",
            "experiment_plan": "This week run 5 join attempts and require 4/5 first-try success.",
        },
    )
    latest_message = {"intent": "ANSWER"}
    assert module.should_send_final_done(room, latest_message) is True


def test_should_not_send_final_done_when_required_fields_are_missing() -> None:
    module = _load_script_module()
    room = _room(
        ["top_issue", "why_this_first", "experiment_plan"],
        {
            "top_issue": "Invite fragmentation silently breaks entry.",
            "why_this_first": "It blocks the 99% path before work even starts.",
        },
    )
    latest_message = {"intent": "ANSWER"}
    assert module.should_send_final_done(room, latest_message) is False


def test_should_not_send_final_done_while_owner_clarification_is_pending() -> None:
    module = _load_script_module()
    room = _room(
        ["decision", "rationale", "fallback"],
        {
            "decision": "Run a sequential handoff pilot.",
            "rationale": "This keeps the workflow inside existing runtimes.",
            "fallback": "Use an async shared outline if the pilot blocks.",
        },
    )
    latest_message = {"intent": "ASK_OWNER"}
    assert module.should_send_final_done(room, latest_message) is False


def test_journey_script_defaults_to_owner_friendly_guest_prompt_mode() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'parser.add_argument("--guest-prompt-copy-mode", choices=["external_simple", "operator_debug", "owner_friendly"], default="owner_friendly")' in source
    assert 'copy_mode="owner_friendly"' in source
