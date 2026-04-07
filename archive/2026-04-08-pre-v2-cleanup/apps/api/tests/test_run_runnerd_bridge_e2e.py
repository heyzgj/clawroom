from __future__ import annotations

import httpx

from scripts.run_runnerd_bridge_e2e import build_history_record
from scripts.run_runnerd_bridge_e2e import get_run_or_latest


def test_build_history_record_marks_certified_closed_room_as_pass() -> None:
    payload = {
        "room": {
            "execution_mode": "managed_attached",
            "managed_coverage": "full",
            "product_owned": True,
            "attempt_status": "exited",
        },
        "result": {
            "status": "closed",
            "stop_reason": "goal_done",
            "turn_count": 2,
            "execution_mode": "managed_attached",
            "runner_certification": "certified",
            "managed_coverage": "full",
            "product_owned": True,
            "automatic_recovery_eligible": True,
            "attempt_status": "exited",
            "execution_attention": {"state": "healthy", "reasons": []},
            "root_cause_hints": [],
            "start_slo": {"first_relay_at": "2026-03-11T00:00:00Z"},
        },
    }
    host_run = {"run_id": "run_host", "status": "exited", "root_cause_code": None, "restart_count": 0}
    guest_run = {"run_id": "run_guest", "status": "exited", "root_cause_code": None, "restart_count": 0}

    record = build_history_record(
        payload=payload,
        room_id="room_123",
        watch_link="https://clawroom.cc/?room_id=room_123&host_token=host_123",
        host_run=host_run,
        guest_run=guest_run,
        owner_reply_count=1,
    )

    assert record["scenario"] == "runnerd_gateway_local"
    assert record["pass"] is True
    assert record["product_owned"] is True
    assert record["runner_certification"] == "certified"
    assert record["outcome_class"] == "success"
    assert record["recovery_class"] == "clean"
    assert record["silent_failure"] is False


def test_build_history_record_marks_open_room_as_takeover_required() -> None:
    payload = {
        "room": {
            "execution_mode": "managed_attached",
            "managed_coverage": "full",
            "product_owned": True,
            "attempt_status": "idle",
        },
        "result": {
            "status": "active",
            "stop_reason": None,
            "turn_count": 1,
            "execution_mode": "managed_attached",
            "runner_certification": "certified",
            "managed_coverage": "full",
            "product_owned": True,
            "automatic_recovery_eligible": True,
            "attempt_status": "idle",
            "execution_attention": {"state": "takeover_required", "reasons": ["first_relay_at_risk"]},
            "root_cause_hints": [{"code": "first_relay_at_risk", "confidence": "medium"}],
        },
    }
    host_run = {"run_id": "run_host", "status": "idle", "root_cause_code": None, "restart_count": 0}
    guest_run = {"run_id": "run_guest", "status": "idle", "root_cause_code": None, "restart_count": 0}

    record = build_history_record(
        payload=payload,
        room_id="room_456",
        watch_link="https://clawroom.cc/?room_id=room_456&host_token=host_456",
        host_run=host_run,
        guest_run=guest_run,
        owner_reply_count=0,
    )

    assert record["pass"] is False
    assert record["outcome_class"] == "takeover_required"
    assert record["silent_failure"] is False
    assert any("expected 'closed'" in error for error in record["errors"])


def test_build_history_record_rejects_missing_first_relay_and_owner_escalation() -> None:
    payload = {
        "room": {
            "execution_mode": "managed_attached",
            "managed_coverage": "full",
            "product_owned": True,
            "attempt_status": "exited",
        },
        "result": {
            "status": "closed",
            "stop_reason": "manual_close",
            "turn_count": 1,
            "execution_mode": "managed_attached",
            "runner_certification": "certified",
            "managed_coverage": "full",
            "product_owned": True,
            "automatic_recovery_eligible": True,
            "attempt_status": "exited",
            "execution_attention": {"state": "healthy", "reasons": []},
            "root_cause_hints": [],
            "start_slo": {"first_relay_at": None},
        },
    }
    host_run = {"run_id": "run_host", "status": "exited", "root_cause_code": None, "restart_count": 0}
    guest_run = {"run_id": "run_guest", "status": "exited", "root_cause_code": None, "restart_count": 0}

    record = build_history_record(
        payload=payload,
        room_id="room_789",
        watch_link="https://clawroom.cc/?room_id=room_789&host_token=host_789",
        host_run=host_run,
        guest_run=guest_run,
        owner_reply_count=0,
    )

    assert record["pass"] is False
    assert "start_slo.first_relay_at is empty" in record["errors"]
    assert "owner escalation was never exercised" in record["errors"]
    assert "turn_count=1 < 2" in record["errors"]
    assert any("stop_reason='manual_close'" in error for error in record["errors"])


def test_build_history_record_marks_success_after_restart() -> None:
    payload = {
        "room": {
            "execution_mode": "managed_attached",
            "managed_coverage": "full",
            "product_owned": True,
            "attempt_status": "exited",
        },
        "result": {
            "status": "closed",
            "stop_reason": "mutual_done",
            "turn_count": 4,
            "execution_mode": "managed_attached",
            "runner_certification": "certified",
            "managed_coverage": "full",
            "product_owned": True,
            "automatic_recovery_eligible": True,
            "attempt_status": "exited",
            "execution_attention": {"state": "healthy", "reasons": []},
            "root_cause_hints": [],
            "start_slo": {"first_relay_at": "2026-03-11T00:00:00Z"},
        },
    }
    host_run = {"run_id": "run_host", "status": "exited", "root_cause_code": None, "restart_count": 1}
    guest_run = {"run_id": "run_guest", "status": "exited", "root_cause_code": None, "restart_count": 0}

    record = build_history_record(
        payload=payload,
        room_id="room_restart",
        watch_link="https://clawroom.cc/?room_id=room_restart&host_token=host_restart",
        host_run=host_run,
        guest_run=guest_run,
        owner_reply_count=1,
    )

    assert record["pass"] is True
    assert record["restart_observed"] is True
    assert record["recovery_class"] == "success_after_restart"
    assert "runnerd restart path was exercised before the room finished" in record["warnings"]


def test_build_history_record_marks_restart_exhaustion() -> None:
    payload = {
        "room": {
            "execution_mode": "managed_attached",
            "managed_coverage": "full",
            "product_owned": True,
            "attempt_status": "abandoned",
        },
        "result": {
            "status": "active",
            "stop_reason": None,
            "turn_count": 1,
            "execution_mode": "managed_attached",
            "runner_certification": "certified",
            "managed_coverage": "full",
            "product_owned": True,
            "automatic_recovery_eligible": True,
            "attempt_status": "abandoned",
            "execution_attention": {"state": "takeover_required", "reasons": ["replacement_pending"]},
            "root_cause_hints": [{"code": "replacement_pending", "confidence": "high"}],
            "start_slo": {"first_relay_at": None},
        },
    }
    host_run = {
        "run_id": "run_host",
        "status": "abandoned",
        "root_cause_code": "runnerd_restart_exhausted_after_claim",
        "restart_count": 1,
    }
    guest_run = {"run_id": "run_guest", "status": "idle", "root_cause_code": None, "restart_count": 0}

    record = build_history_record(
        payload=payload,
        room_id="room_exhausted",
        watch_link="https://clawroom.cc/?room_id=room_exhausted&host_token=host_exhausted",
        host_run=host_run,
        guest_run=guest_run,
        owner_reply_count=1,
    )

    assert record["pass"] is False
    assert record["replacement_plane_exhausted"] is True
    assert record["recovery_class"] == "restart_exhausted"


def test_build_history_record_marks_success_after_replacement() -> None:
    payload = {
        "room": {
            "execution_mode": "managed_attached",
            "managed_coverage": "full",
            "product_owned": True,
            "attempt_status": "exited",
        },
        "result": {
            "status": "closed",
            "stop_reason": "goal_done",
            "turn_count": 3,
            "execution_mode": "managed_attached",
            "runner_certification": "certified",
            "managed_coverage": "full",
            "product_owned": True,
            "automatic_recovery_eligible": True,
            "attempt_status": "exited",
            "execution_attention": {"state": "healthy", "reasons": []},
            "root_cause_hints": [],
            "start_slo": {"first_relay_at": "2026-03-11T00:00:00Z"},
        },
    }
    host_run = {
        "run_id": "run_host_2",
        "status": "exited",
        "root_cause_code": None,
        "restart_count": 0,
        "supersedes_run_id": "run_host_1",
    }
    guest_run = {"run_id": "run_guest", "status": "exited", "root_cause_code": None, "restart_count": 0}

    record = build_history_record(
        payload=payload,
        room_id="room_replaced",
        watch_link="https://clawroom.cc/?room_id=room_replaced&host_token=host_replaced",
        host_run=host_run,
        guest_run=guest_run,
        owner_reply_count=1,
    )

    assert record["pass"] is True
    assert record["replacement_run_observed"] is True
    assert record["recovery_class"] == "success_after_replacement"
    assert "runner replacement lineage was exercised before the room finished" in record["warnings"]


def test_get_run_or_latest_falls_back_to_cached_payload_when_room_is_closed(monkeypatch) -> None:
    cached = {"run_id": "run_123", "status": "active"}
    latest = {"run_123": cached}

    def fake_get_run(*, runnerd_url: str, run_id: str):  # type: ignore[no-untyped-def]
        _ = (runnerd_url, run_id)
        raise httpx.ReadTimeout("runnerd not reachable")

    monkeypatch.setattr("scripts.run_runnerd_bridge_e2e.get_run", fake_get_run)

    payload = get_run_or_latest(
        runnerd_url="http://127.0.0.1:8741",
        run_id="run_123",
        latest_runs=latest,
        room_closed=True,
    )

    assert payload is cached


def test_get_run_or_latest_raises_when_room_is_not_closed_and_runnerd_unreachable(monkeypatch) -> None:
    def fake_get_run(*, runnerd_url: str, run_id: str):  # type: ignore[no-untyped-def]
        _ = (runnerd_url, run_id)
        raise httpx.ReadTimeout("runnerd not reachable")

    monkeypatch.setattr("scripts.run_runnerd_bridge_e2e.get_run", fake_get_run)

    try:
        get_run_or_latest(
            runnerd_url="http://127.0.0.1:8741",
            run_id="run_123",
            latest_runs={},
            room_closed=False,
        )
    except httpx.ReadTimeout:
        pass
    else:
        raise AssertionError("expected ReadTimeout when no cached payload is available")
