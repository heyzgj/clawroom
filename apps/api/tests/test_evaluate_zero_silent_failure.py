from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "evaluate_zero_silent_failure.py"
SPEC = importlib.util.spec_from_file_location("evaluate_zero_silent_failure", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_evaluate_requires_enough_certified_history() -> None:
    history = [
        {
            "foundation_contract_version": MODULE.CURRENT_FOUNDATION_CONTRACT_VERSION,
            "product_owned": True,
            "pass": True,
            "silent_failure": False,
            "outcome_class": "success",
        }
        for _ in range(5)
    ]
    summary = MODULE.evaluate(history, e2e_window=3, certified_window=10)
    assert summary["product_owned_gate_pass"] is True
    assert summary["certified_runtime_gate_pass"] is False
    assert summary["dod_pass"] is False


def test_evaluate_fails_on_silent_failure_in_window() -> None:
    history = [
        {
            "foundation_contract_version": MODULE.CURRENT_FOUNDATION_CONTRACT_VERSION,
            "product_owned": True,
            "pass": True,
            "silent_failure": False,
            "outcome_class": "success",
        }
        for _ in range(9)
    ] + [
        {
            "foundation_contract_version": MODULE.CURRENT_FOUNDATION_CONTRACT_VERSION,
            "product_owned": True,
            "pass": False,
            "silent_failure": True,
            "outcome_class": "failed_unclassified",
        }
    ]
    summary = MODULE.evaluate(history, e2e_window=10, certified_window=10)
    assert summary["product_owned_gate_pass"] is False
    assert summary["certified_runtime_gate_pass"] is False
    assert summary["latest_product_owned"]["silent_failures"] == 1


def test_load_history_and_render_text(tmp_path: Path) -> None:
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "foundation_contract_version": MODULE.CURRENT_FOUNDATION_CONTRACT_VERSION,
                "runner_certification": "certified",
                "product_owned": True,
                "pass": True,
                "silent_failure": False,
                "outcome_class": "success",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = MODULE.load_history(history_path)
    assert len(loaded) == 1
    summary = MODULE.evaluate(loaded, e2e_window=1, certified_window=1)
    text = MODULE.render_text(summary)
    assert "dod_pass=true" in text
    assert "product_owned_gate_pass=true" in text


def test_last_live_product_owned_counts_for_terminal_history() -> None:
    history = [
        {
            "foundation_contract_version": MODULE.CURRENT_FOUNDATION_CONTRACT_VERSION,
            "product_owned": False,
            "last_live_product_owned": True,
            "pass": True,
            "silent_failure": False,
            "outcome_class": "success",
        }
    ]
    summary = MODULE.evaluate(history, e2e_window=1, certified_window=1)
    assert summary["product_owned_records"] == 1


def test_evaluate_ignores_legacy_records_by_default() -> None:
    history = [
        {
            "product_owned": True,
            "pass": False,
            "silent_failure": False,
            "outcome_class": "failed_unclassified",
        },
        {
            "foundation_contract_version": MODULE.CURRENT_FOUNDATION_CONTRACT_VERSION,
            "product_owned": True,
            "pass": True,
            "silent_failure": False,
            "outcome_class": "success",
        },
    ]
    summary = MODULE.evaluate(history, e2e_window=1, certified_window=1)
    assert summary["current_contract_records"] == 1
    assert summary["dod_pass"] is True


def test_derive_path_family_uses_telegram_only_shape_when_no_helper_submission() -> None:
    record = {
        "scenario": "natural",
        "host_bot": "@singularitygz_bot",
        "guest_bot": "@link_clawd_bot",
        "wait_after_new_seconds": 30.0,
    }
    assert MODULE.derive_path_family(record) == "telegram_only_cross_owner_v1"


def test_derive_path_family_prefers_helper_submitted_participants() -> None:
    record = {
        "scenario": "owner_escalation",
        "host_bot": "@singularitygz_bot",
        "guest_bot": "@link_clawd_bot",
        "wait_after_new_seconds": 30.0,
        "helper_submitted_participants": ["host", "guest"],
    }
    assert MODULE.derive_path_family(record) == "telegram_helper_submitted_runnerd_v1"
