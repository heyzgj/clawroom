from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "evaluate_telegram_certified_path.py"
SPEC = importlib.util.spec_from_file_location("evaluate_telegram_certified_path", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _record(
    *,
    scenario: str,
    passed: bool,
    room_id: str = "room_x",
    path_family: str = "telegram_helper_submitted_runnerd_v1",
) -> dict[str, object]:
    return {
        "host_bot": "@singularitygz_bot",
        "guest_bot": "@link_clawd_bot",
        "product_owned": True,
        "execution_mode": "managed_attached",
        "runner_certification": "certified",
        "path_family": path_family,
        "scenario": scenario,
        "pass": passed,
        "silent_failure": False,
        "outcome_class": "success" if passed else "failed_unclassified",
        "room_id": room_id,
    }


def test_gate_passes_with_five_clean_successes_and_three_owner_escalations() -> None:
    history = [
        _record(scenario="owner_escalation", passed=True, room_id="room_1"),
        _record(scenario="owner_escalation", passed=True, room_id="room_2"),
        _record(scenario="natural", passed=True, room_id="room_3"),
        _record(scenario="owner_escalation", passed=True, room_id="room_4"),
        _record(scenario="owner_escalation", passed=True, room_id="room_5"),
    ]
    summary = MODULE.evaluate(
        history,
        host_bot="@singularitygz_bot",
        guest_bot="@link_clawd_bot",
        execution_mode="managed_attached",
        runner_certification="certified",
        path_family="telegram_helper_submitted_runnerd_v1",
        window=5,
        min_owner_escalation_successes=3,
    )
    assert summary["gate_pass"] is True
    assert summary["latest_window"]["successes"] == 5
    assert summary["latest_window"]["owner_escalation_successes"] == 4


def test_gate_fails_when_any_window_record_fails() -> None:
    history = [
        _record(scenario="owner_escalation", passed=True, room_id="room_1"),
        _record(scenario="owner_escalation", passed=True, room_id="room_2"),
        _record(scenario="natural", passed=False, room_id="room_3"),
        _record(scenario="owner_escalation", passed=True, room_id="room_4"),
        _record(scenario="owner_escalation", passed=True, room_id="room_5"),
    ]
    summary = MODULE.evaluate(
        history,
        host_bot="@singularitygz_bot",
        guest_bot="@link_clawd_bot",
        execution_mode="managed_attached",
        runner_certification="certified",
        path_family="telegram_helper_submitted_runnerd_v1",
        window=5,
        min_owner_escalation_successes=3,
    )
    assert summary["gate_pass"] is False
    assert summary["latest_window"]["failures"] == 1


def test_gate_fails_when_owner_escalation_coverage_is_too_low() -> None:
    history = [
        _record(scenario="natural", passed=True, room_id="room_1"),
        _record(scenario="natural", passed=True, room_id="room_2"),
        _record(scenario="natural", passed=True, room_id="room_3"),
        _record(scenario="owner_escalation", passed=True, room_id="room_4"),
        _record(scenario="natural", passed=True, room_id="room_5"),
    ]
    summary = MODULE.evaluate(
        history,
        host_bot="@singularitygz_bot",
        guest_bot="@link_clawd_bot",
        execution_mode="managed_attached",
        runner_certification="certified",
        path_family="telegram_helper_submitted_runnerd_v1",
        window=5,
        min_owner_escalation_successes=3,
    )
    assert summary["gate_pass"] is False
    assert summary["latest_window"]["owner_escalation_successes"] == 1


def test_gate_filters_to_requested_path_family() -> None:
    history = [
        _record(scenario="owner_escalation", passed=True, room_id="room_helper"),
        _record(
            scenario="owner_escalation",
            passed=True,
            room_id="room_other",
            path_family="telegram_only_cross_owner_v1",
        ),
    ]
    summary = MODULE.evaluate(
        history,
        host_bot="@singularitygz_bot",
        guest_bot="@link_clawd_bot",
        execution_mode="managed_attached",
        runner_certification="certified",
        path_family="telegram_helper_submitted_runnerd_v1",
        window=1,
        min_owner_escalation_successes=1,
    )
    assert summary["matching_records"] == 1
    assert summary["latest_rooms"][0]["room_id"] == "room_helper"
