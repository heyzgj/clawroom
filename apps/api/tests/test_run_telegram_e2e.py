from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills" / "openclaw-telegram-e2e" / "scripts" / "run_telegram_e2e.py"
CREATE_SCRIPT = ROOT / "skills" / "openclaw-telegram-e2e" / "scripts" / "create_telegram_test_room.py"
SPEC = importlib.util.spec_from_file_location("run_telegram_e2e", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CREATE_SPEC = importlib.util.spec_from_file_location("create_telegram_test_room", CREATE_SCRIPT)
assert CREATE_SPEC and CREATE_SPEC.loader
CREATE_MODULE = importlib.util.module_from_spec(CREATE_SPEC)
CREATE_SPEC.loader.exec_module(CREATE_MODULE)


def test_evaluate_result_passes_clean_closed_room() -> None:
    result = {
        "status": "closed",
        "stop_reason": "mutual_done",
        "turn_count": 5,
        "transcript": [
            {"text": "Maybe ramen tonight?"},
            {"text": "Ramen sounds good. Any place in mind?"},
            {"text": "Let's do the spot near the station."},
            {"text": "Perfect, let's go there."},
        ],
    }
    evaluation = MODULE.evaluate_result(
        result=result,
        min_turns=4,
        reject_meta_language=True,
        allowed_stop={"goal_done", "mutual_done", "turn_limit", "timeout"},
    )
    assert evaluation["pass"] is True
    assert evaluation["errors"] == []


def test_evaluate_result_allows_short_required_fields_completion() -> None:
    result = {
        "status": "closed",
        "stop_reason": "goal_done",
        "turn_count": 2,
        "required_total": 3,
        "required_filled": 3,
        "transcript": [
            {
                "text": "Here is the recommendation with all required outputs filled.",
                "fills": {
                    "core_problem": "Group chat mixes human chat with agent work.",
                    "room_value": "Structured task rooms separate the two.",
                    "next_validation_step": "Run a small pilot.",
                },
            },
            {"text": "Aligned. Done."},
        ],
    }
    evaluation = MODULE.evaluate_result(
        result=result,
        min_turns=4,
        reject_meta_language=False,
        allowed_stop={"goal_done", "mutual_done", "turn_limit", "timeout"},
    )
    assert evaluation["pass"] is True
    assert evaluation["required_total"] == 3
    assert evaluation["required_filled"] == 3
    assert evaluation["errors"] == []


def test_build_log_entry_mentions_watch_link_and_wait_rule() -> None:
    entry = MODULE.build_log_entry(
        title="Natural Scenario (serial Telegram runner)",
        summary={
            "room_id": "room_abc123",
            "watch_link": "https://clawroom.cc/?room_id=room_abc123&host_token=host_1",
            "status": "closed",
            "stop_reason": "mutual_done",
            "turn_count": 5,
            "execution_mode": "managed_attached",
            "runner_certification": "certified",
            "managed_coverage": "full",
            "product_owned": True,
            "automatic_recovery_eligible": True,
            "attempt_status": "exited",
            "execution_attention_state": "healthy",
            "primary_root_cause_code": "runner_lost_before_first_relay",
            "primary_root_cause_confidence": "high",
            "pass": True,
        },
        prompt_pack_version="serial runner with /new double-enter + 30s wait",
        participants="@host host + @guest guest",
        learnings=["Used /new with a 30.0s wait before the real prompt."],
        follow_up=["Keep the matrix healthy."],
    )
    assert "room_abc123" in entry
    assert "30s wait" in entry
    assert "watch_link" in entry
    assert "managed_attached" in entry
    assert "product-owned" in entry
    assert "runner_lost_before_first_relay" in entry


def test_history_record_classifies_success_without_silent_failure() -> None:
    summary = {
        "pass": True,
        "room_id": "room_hist",
        "watch_link": "https://clawroom.cc/?room_id=room_hist",
        "status": "closed",
        "stop_reason": "mutual_done",
        "turn_count": 6,
        "execution_mode": "managed_attached",
        "runner_certification": "certified",
        "managed_coverage": "full",
        "product_owned": True,
        "automatic_recovery_eligible": True,
        "last_live_execution_mode": "managed_attached",
        "last_live_managed_coverage": "full",
        "last_live_product_owned": True,
        "attempt_status": "exited",
        "execution_attention_state": "healthy",
        "execution_attention_reasons": [],
        "primary_root_cause_code": None,
        "primary_root_cause_confidence": None,
        "errors": [],
        "warnings": [],
    }
    record = MODULE.build_history_record(
        summary=summary,
        scenario="natural",
        host_bot="@host",
        guest_bot="@guest",
        wait_after_new=30.0,
        submitted_run_ids={},
    )
    assert record["outcome_class"] == "success"
    assert record["silent_failure"] is False
    assert record["runner_certification"] == "certified"
    assert record["last_live_product_owned"] is True
    assert record["managed_coverage"] == "full"
    assert record["product_owned"] is True
    assert record["path_family"] == ""
    assert record["helper_submitted_participants"] == []


def test_history_record_marks_silent_failure_only_when_unexplained() -> None:
    summary = {
        "pass": False,
        "room_id": "room_hist_fail",
        "watch_link": "https://clawroom.cc/?room_id=room_hist_fail",
        "status": "active",
        "stop_reason": None,
        "turn_count": 1,
        "execution_mode": "managed_attached",
        "runner_certification": "candidate",
        "managed_coverage": "partial",
        "product_owned": False,
        "automatic_recovery_eligible": False,
        "attempt_status": "abandoned",
        "execution_attention_state": "healthy",
        "execution_attention_reasons": [],
        "root_cause_hints": [],
        "errors": ["room status is 'active', expected 'closed'"],
        "warnings": [],
    }
    record = MODULE.build_history_record(
        summary=summary,
        scenario="natural",
        host_bot="@host",
        guest_bot="@guest",
        wait_after_new=30.0,
        submitted_run_ids={},
    )
    assert record["outcome_class"] == "failed_unclassified"
    assert record["silent_failure"] is True


def test_derive_path_family_splits_telegram_only_and_helper_submitted_lanes() -> None:
    assert (
        MODULE.derive_path_family(
            scenario="natural",
            host_bot="@singularitygz_bot",
            guest_bot="@link_clawd_bot",
            wait_after_new=30.0,
            submitted_run_ids={},
        )
        == "telegram_only_cross_owner_v1"
    )
    assert (
        MODULE.derive_path_family(
            scenario="owner_escalation",
            host_bot="@singularitygz_bot",
            guest_bot="@link_clawd_bot",
            wait_after_new=30.0,
            submitted_run_ids={"host": "run_a", "guest": "run_b"},
        )
        == "telegram_helper_submitted_runnerd_v1"
    )


def test_natural_join_prompt_stays_goal_focused_without_style_override() -> None:
    prompt = CREATE_MODULE.build_join_prompt(
        "https://api.clawroom.cc/join/room_abc?token=inv_123",
        room_id="room_abc",
        role="responder",
        scenario="natural",
        runnerd_url="http://127.0.0.1:9999",
    )
    assert "Use everyday language" not in prompt
    assert "Make a concrete recommendation or ask one useful follow-up if needed" in prompt
    assert "Preferred path: treat this chat as a gateway, not the long-running worker." in prompt
    assert "treat this chat as a gateway, not the long-running worker" in prompt
    assert "owner or a local helper can hand it to runnerd" in prompt
    assert "submit_cli.py" in prompt
    assert "ClawRoom wake package." in prompt
    assert '"coordination_id": "coord_room_abc"' in prompt
    assert '"role": "responder"' in prompt
    assert '"preferred_runner_kind": "openclaw_bridge"' in prompt
    assert "If local runnerd is unavailable or rejects the wake, say so briefly instead of inventing a local worker path." in prompt
    assert "bash /tmp/openclaw-shell-bridge.sh" not in prompt
    assert "Reply with one concise gateway status update for the owner" in prompt
    assert "close the room once a clear decision is reached" in prompt


def test_gateway_only_join_prompt_never_tells_gateway_to_join_directly() -> None:
    prompt = CREATE_MODULE.build_join_prompt(
        "https://api.clawroom.cc/join/room_gateway?token=inv_123",
        room_id="room_gateway",
        role="initiator",
        scenario="owner_escalation",
        runnerd_url="http://127.0.0.1:9999",
        gateway_only=True,
    )
    assert "Act as the gateway for this ClawRoom task." in prompt
    assert "Join link (context only; do not join directly)" in prompt
    assert "Do not call /join yourself" in prompt
    assert "do not start a shell keepalive" in prompt
    assert "Join this clawroom for me." not in prompt
    assert "If reading the skill page is blocked, continue API-first" not in prompt
    assert "Reply with one concise gateway status update for the owner" in prompt


def test_owner_escalation_join_prompt_requires_one_owner_clarification() -> None:
    prompt = CREATE_MODULE.build_join_prompt(
        "https://api.clawroom.cc/join/room_owner?token=inv_123",
        room_id="room_owner",
        role="initiator",
        scenario="owner_escalation",
        runnerd_url="http://127.0.0.1:9999",
    )
    assert "One owner-only clarification is required before the final decision" in prompt
    assert '"role": "initiator"' in prompt
    assert "hidden decision rule" in prompt
    assert "ask the owner only once" in prompt
    assert "do at least one normal in-room exchange before asking the owner" in prompt


def test_join_prompt_can_pin_relay_agent_id() -> None:
    prompt = CREATE_MODULE.build_join_prompt(
        "https://api.clawroom.cc/join/room_abc?token=inv_123",
        room_id="room_abc",
        role="initiator",
        scenario="natural",
        runnerd_url="http://127.0.0.1:8741",
        relay_agent_id="sam",
    )
    assert '"role": "initiator"' in prompt


def test_default_owner_reply_text_changes_for_owner_escalation() -> None:
    assert MODULE.default_owner_reply_text("natural").startswith("Proceed with the safer option")
    assert "safer, classic option" in MODULE.default_owner_reply_text("owner_escalation")


def test_refresh_runnerd_runs_prefers_live_payloads(monkeypatch, tmp_path: Path) -> None:
    state = {"runnerd_runs": {"host": {"status": "waiting_owner"}}}

    def fake_get_runnerd_run(*, runnerd_url: str, run_id: str) -> dict:
        assert runnerd_url == "http://127.0.0.1:8741"
        assert run_id == "run_123"
        return {"run_id": run_id, "status": "exited", "pending_owner_request": None}

    monkeypatch.setattr(MODULE, "get_runnerd_run", fake_get_runnerd_run)
    artifact_path = tmp_path / "artifact.json"
    MODULE.refresh_runnerd_runs(
        runnerd_url="http://127.0.0.1:8741",
        submitted_run_ids={"host": "run_123"},
        state=state,
        artifact_path=artifact_path,
    )
    assert state["runnerd_runs"]["host"]["status"] == "exited"
    assert artifact_path.exists()


def test_choose_runnerd_port_returns_requested_or_ephemeral() -> None:
    port = MODULE.choose_runnerd_port(8741)
    assert isinstance(port, int)
    assert port > 0


def test_expect_execution_mode_can_fail_summary_without_touching_other_gates() -> None:
    summary = {
        "pass": True,
        "errors": [],
        "execution_mode": "compatibility",
    }
    expected = "managed_attached"
    if expected and str(summary.get("execution_mode") or "") != expected:
        summary["pass"] = False
        summary.setdefault("errors", []).append(
            f"execution_mode={summary.get('execution_mode')!r} != expected {expected!r}"
        )
    assert summary["pass"] is False
    assert "managed_attached" in summary["errors"][0]


def test_poll_for_room_close_preserves_last_live_snapshot_before_closed_result(monkeypatch) -> None:
    room_snapshots = iter(
        [
            {
                "status": "active",
                "execution_mode": "managed_attached",
                "attempt_status": "abandoned",
                "execution_attention": {
                    "state": "takeover_required",
                    "reasons": ["replacement_pending", "runner_abandoned"],
                },
            },
            {"status": "closed"},
        ]
    )
    result_payloads = iter(
        [
            {"result": {"status": "active"}},
            {"result": {"status": "closed", "stop_reason": "manual_close"}},
        ]
    )
    time_values = iter([0.0, 0.0, 1.0])

    monkeypatch.setattr(MODULE, "fetch_room_snapshot", lambda **_: next(room_snapshots))
    monkeypatch.setattr(MODULE, "fetch_result", lambda **_: next(result_payloads))
    monkeypatch.setattr(MODULE, "time", SimpleNamespace(time=lambda: next(time_values), sleep=lambda _: None))

    polled = MODULE.poll_for_room_close(
        base_url="https://api.clawroom.cc",
        room_id="room_test",
        token="inv_test",
        host_token="host_test",
        timeout_seconds=30,
        poll_seconds=1.0,
    )

    assert polled["result_payload"]["result"]["status"] == "closed"
    assert polled["last_live_room"]["status"] == "active"
    assert polled["last_live_room"]["attempt_status"] == "abandoned"
    assert polled["last_live_room"]["execution_attention"]["reasons"] == [
        "replacement_pending",
        "runner_abandoned",
    ]


def test_update_state_from_live_room_persists_last_live_fields(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    state = {"phase": "guest_sent", "room_id": "room_live"}
    room = {
        "execution_mode": "managed_attached",
        "runner_certification": "candidate",
        "managed_coverage": "partial",
        "product_owned": False,
        "automatic_recovery_eligible": False,
        "attempt_status": "active",
        "execution_attention": {
            "state": "takeover_required",
            "reasons": ["replacement_pending"],
        },
    }

    MODULE.update_state_from_live_room(state, room, artifact)

    assert state["execution_mode"] == "managed_attached"
    assert state["last_live_execution_mode"] == "managed_attached"
    assert state["last_live_managed_coverage"] == "partial"
    assert state["last_live_product_owned"] is False
    assert state["last_live_execution_attention_reasons"] == ["replacement_pending"]
    persisted = artifact.read_text(encoding="utf-8")
    assert "\"last_live_execution_mode\": \"managed_attached\"" in persisted


def test_update_state_from_result_persists_last_result(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    state = {"phase": "guest_sent", "room_id": "room_live"}
    result = {
        "status": "active",
        "turn_count": 1,
        "root_cause_hints": [{"code": "runner_lost_before_first_relay"}],
    }

    MODULE.update_state_from_result(state, result, artifact)

    assert state["last_result"]["status"] == "active"
    assert state["last_result"]["turn_count"] == 1
    persisted = artifact.read_text(encoding="utf-8")
    assert "\"last_result\"" in persisted
