from __future__ import annotations

import json
from pathlib import Path

from clawroom_client_core import build_runner_state, next_relays, relay_requires_reply


def test_build_runner_state_loads_cursor_and_seen(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "base_url": "https://api.clawroom.cc",
                "room_id": "room_abc",
                "token": "inv_old",
                "participant": "guest",
                "cursor": 12,
                "seen_event_ids": [10, 11, 12],
                "runtime_session_id": "sess_x",
            }
        ),
        encoding="utf-8",
    )

    state = build_runner_state(
        base_url="https://api.clawroom.cc",
        room_id="room_abc",
        token="inv_new",
        initial_cursor=3,
        state_path=state_path,
    )

    assert state.cursor == 12
    assert state.participant == "guest"
    assert state.runtime_session_id == "sess_x"
    assert state.seen_event_ids == {10, 11, 12}


def test_build_runner_state_ignores_mismatched_room(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "base_url": "https://api.clawroom.cc",
                "room_id": "room_other",
                "cursor": 99,
                "seen_event_ids": [99],
            }
        ),
        encoding="utf-8",
    )

    state = build_runner_state(
        base_url="https://api.clawroom.cc",
        room_id="room_target",
        token="inv_target",
        initial_cursor=4,
        state_path=state_path,
    )
    assert state.cursor == 4
    assert state.seen_event_ids == set()


def test_next_relays_marks_seen_and_advances_cursor() -> None:
    state = build_runner_state(
        base_url="https://api.clawroom.cc",
        room_id="room_a",
        token="inv_a",
        initial_cursor=0,
    )
    batch = {
        "room": {"status": "active"},
        "events": [
            {"id": 1, "type": "status"},
            {"id": 2, "type": "relay", "payload": {"message": {"intent": "ASK", "expect_reply": True}}},
            {"id": 2, "type": "relay", "payload": {"message": {"intent": "ASK", "expect_reply": True}}},
        ],
        "next_cursor": 3,
    }
    room, relays, cursor = next_relays(batch, state)
    assert room["status"] == "active"
    assert len(relays) == 1
    assert relays[0]["id"] == 2
    assert cursor == 3
    assert state.seen_event_ids == {1, 2}

    room2, relays2, cursor2 = next_relays(batch, state)
    assert room2["status"] == "active"
    assert relays2 == []
    assert cursor2 == 3


def test_relay_requires_reply_done_exception() -> None:
    done_evt = {"payload": {"message": {"intent": "DONE", "expect_reply": False}}}
    note_evt = {"payload": {"message": {"intent": "NOTE", "expect_reply": False}}}
    ask_evt = {"payload": {"message": {"intent": "ASK", "expect_reply": True}}}
    assert relay_requires_reply(done_evt) is True
    assert relay_requires_reply(note_evt) is False
    assert relay_requires_reply(ask_evt) is True


def test_counterpart_memory_persists_last_message(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = build_runner_state(
        base_url="https://api.clawroom.cc",
        room_id="room_memory",
        token="inv_x",
        state_path=state_path,
    )
    state.note_counterpart_message(intent="ASK", text="What should we eat tonight?")
    assert state.save()

    loaded = build_runner_state(
        base_url="https://api.clawroom.cc",
        room_id="room_memory",
        token="inv_x",
        state_path=state_path,
    )
    assert loaded.conversation.last_counterpart_ask == "What should we eat tonight?"
    assert loaded.conversation.last_counterpart_message == "What should we eat tonight?"


def test_runner_plane_fields_persist(tmp_path: Path) -> None:
    state_path = tmp_path / "runner_state.json"
    state = build_runner_state(
        base_url="https://api.clawroom.cc",
        room_id="room_runner",
        token="inv_runner",
        state_path=state_path,
    )
    state.runner_id = "codex:runner-1"
    state.attempt_id = "attempt_runner_1"
    state.execution_mode = "managed_attached"
    state.lease_expires_at = "2026-03-07T09:00:00Z"
    assert state.save()

    loaded = build_runner_state(
        base_url="https://api.clawroom.cc",
        room_id="room_runner",
        token="inv_runner",
        state_path=state_path,
    )
    assert loaded.runner_id == "codex:runner-1"
    assert loaded.attempt_id == "attempt_runner_1"
    assert loaded.execution_mode == "managed_attached"
    assert loaded.lease_expires_at == "2026-03-07T09:00:00Z"


def test_runner_capabilities_persist_certification_and_recovery_policy(tmp_path: Path) -> None:
    state_path = tmp_path / "runner_caps.json"
    state = build_runner_state(
        base_url="https://api.clawroom.cc",
        room_id="room_caps",
        token="inv_caps",
        state_path=state_path,
    )
    state.capabilities.managed_certified = True
    state.capabilities.recovery_policy = "automatic"
    assert state.save()

    loaded = build_runner_state(
        base_url="https://api.clawroom.cc",
        room_id="room_caps",
        token="inv_caps",
        state_path=state_path,
    )
    assert loaded.capabilities.managed_certified is True
    assert loaded.capabilities.recovery_policy == "automatic"
