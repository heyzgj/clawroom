from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import roombridge_store.service as store_service
from roombridge_core.models import ContextEnvelope, ContextRef, MessageIn, OutcomeContract, RoomCreateIn
from roombridge_store.service import RoomStore


ROOT = Path(__file__).resolve().parents[3]


def _make_store(tmp_path: Path) -> RoomStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    dsn = f"sqlite+pysqlite:///{tmp_path / 'roombridge.db'}"
    store = RoomStore(dsn)
    store.init()
    return store


def _create_joined_room(
    store: RoomStore,
    *,
    required_fields: list[str] | None = None,
    turn_limit: int = 12,
    stall_limit: int = 3,
    timeout_minutes: int = 30,
) -> tuple[str, str, str]:
    created = store.create_room(
        RoomCreateIn(
            topic="Close matrix",
            goal="Verify close semantics",
            participants=["alice", "bob"],
            required_fields=required_fields or [],
            turn_limit=turn_limit,
            timeout_minutes=timeout_minutes,
            stall_limit=stall_limit,
        )
    )
    room_id = str(created["room"]["id"])
    alice_token = str(created["invites"]["alice"])
    bob_token = str(created["invites"]["bob"])
    store.join(room_id, alice_token, "Alice")
    store.join(room_id, bob_token, "Bob")
    return room_id, alice_token, bob_token


@pytest.mark.parametrize(
    ("scenario", "expected_stop_reason"),
    [
        ("mutual_done", "mutual_done"),
        ("goal_done", "goal_done"),
        ("timeout", "timeout"),
        ("turn_limit", "turn_limit"),
        ("stall", "stall"),
    ],
)
def test_store_close_matrix_covers_shared_outcomes(tmp_path: Path, monkeypatch, scenario: str, expected_stop_reason: str) -> None:
    store = _make_store(tmp_path / scenario)

    if scenario == "goal_done":
        room_id, alice_token, bob_token = _create_joined_room(
            store,
            required_fields=["decision"],
            turn_limit=12,
            stall_limit=3,
            timeout_minutes=30,
        )
        store.post_message(
            room_id,
            alice_token,
            MessageIn(
                intent="NOTE",
                text="decision: ship with the faster safe path",
                fills={"decision": "faster safe path"},
                expect_reply=False,
                meta={},
            ),
        )
        snapshot = store.post_message(
            room_id,
            bob_token,
            MessageIn(
                intent="DONE",
                text="That closes the loop.",
                expect_reply=False,
                meta={},
            ),
        )["room"]
    elif scenario == "mutual_done":
        room_id, alice_token, bob_token = _create_joined_room(store, turn_limit=12, stall_limit=3, timeout_minutes=30)
        store.post_message(
            room_id,
            alice_token,
            MessageIn(intent="DONE", text="done from alice", expect_reply=False, meta={}),
        )
        snapshot = store.post_message(
            room_id,
            bob_token,
            MessageIn(intent="DONE", text="done from bob", expect_reply=False, meta={}),
        )["room"]
    elif scenario == "timeout":
        room_id, alice_token, _bob_token = _create_joined_room(store, turn_limit=12, stall_limit=3, timeout_minutes=30)
        future = datetime.now(UTC) + timedelta(minutes=45)
        monkeypatch.setattr(store_service, "now_utc", lambda: future)
        snapshot = store.room_for_participant(room_id, alice_token)["room"]
    elif scenario == "turn_limit":
        room_id, alice_token, bob_token = _create_joined_room(store, turn_limit=2, stall_limit=3, timeout_minutes=30)
        store.post_message(
            room_id,
            alice_token,
            MessageIn(intent="NOTE", text="turn one", expect_reply=False, meta={}),
        )
        snapshot = store.post_message(
            room_id,
            bob_token,
            MessageIn(intent="NOTE", text="turn two", expect_reply=False, meta={}),
        )["room"]
    elif scenario == "stall":
        room_id, alice_token, bob_token = _create_joined_room(store, turn_limit=12, stall_limit=1, timeout_minutes=30)
        store.post_message(
            room_id,
            alice_token,
            MessageIn(intent="NOTE", text="stall me", expect_reply=False, meta={}),
        )
        snapshot = store.post_message(
            room_id,
            bob_token,
            MessageIn(intent="NOTE", text="stall me", expect_reply=False, meta={}),
        )["room"]

    assert snapshot["status"] == "closed"
    assert snapshot["stop_reason"] == expected_stop_reason


def test_store_close_matrix_manual_close_uses_host_token(tmp_path: Path) -> None:
    store = _make_store(tmp_path / "manual_close")
    created = store.create_room(
        RoomCreateIn(
            topic="Close matrix",
            goal="Verify close semantics",
            participants=["alice", "bob"],
            required_fields=[],
            turn_limit=12,
            timeout_minutes=30,
            stall_limit=3,
        )
    )
    room_id = str(created["room"]["id"])
    host_token = str(created["host_token"])
    alice_token = str(created["invites"]["alice"])
    bob_token = str(created["invites"]["bob"])
    store.join(room_id, alice_token, "Alice")
    store.join(room_id, bob_token, "Bob")

    snapshot = store.close(room_id, host_token, "manual operator close")["room"]
    assert snapshot["status"] == "closed"
    assert snapshot["stop_reason"] == "manual_close"


def test_edge_and_store_close_matrix_share_the_same_common_stop_reasons() -> None:
    store_source = (ROOT / "packages" / "store" / "src" / "roombridge_store" / "service.py").read_text(encoding="utf-8")
    edge_source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")

    shared_reasons = ("goal_done", "mutual_done", "turn_limit", "timeout", "manual_close")
    for reason in shared_reasons:
        assert reason in store_source
        assert reason in edge_source

    assert 'self._close_room(conn, room_id, "stall", "stall limit reached")' in store_source
    assert 'requestRoomClose(roomId, "stall_limit", "stall limit reached", { source: "auto" })' in edge_source
    assert 'type StopReason = "goal_done" | "mutual_done" | "turn_limit" | "stall_limit" | "timeout" | "manual_close";' in edge_source


def test_store_create_join_and_read_expose_context_and_lineage(tmp_path: Path) -> None:
    store = _make_store(tmp_path / "context_lineage")
    created = store.create_room(
        RoomCreateIn(
            topic="Lineage carry-forward",
            goal="Keep prior decisions and participant context visible",
            participants=["alice", "bob"],
            required_fields=[],
            turn_limit=12,
            timeout_minutes=30,
            stall_limit=3,
            parent_room_id="room_parent_123",
            prior_outcome_summary="The previous room locked the framing and the constraints.",
            prior_outcome_refs=[
                ContextRef(type="url", label="prior summary", value="https://example.com/rooms/room_parent_123"),
                ContextRef(type="file", label="handoff note", value="/tmp/handoff.md"),
            ],
            outcome_contract=OutcomeContract(
                close_conditions={"min_turns": 3, "min_unique_participants": 2, "require_explicit_consensus": True},
                resolution_mode="owner_gated",
            ),
        )
    )

    room = created["room"]
    room_id = str(room["id"])
    alice_token = str(created["invites"]["alice"])
    assert room["parent_room_id"] == "room_parent_123"
    assert room["prior_outcome_summary"] == "The previous room locked the framing and the constraints."
    assert room["prior_outcome_refs"][0]["label"] == "prior summary"
    assert room["outcome_contract"]["resolution_mode"] == "owner_gated"

    join = store.join(
        room_id,
        alice_token,
        "Alice",
        ContextEnvelope(
            summary="Alice keeps the prior handoff in view.",
            refs=[ContextRef(type="metric", label="carry-over", value="yes")],
        ),
    )
    joined_room = join["room"]
    alice = next(participant for participant in joined_room["participants"] if participant["name"] == "alice")
    assert alice["context_envelope"]["summary"] == "Alice keeps the prior handoff in view."
    assert alice["context_envelope"]["refs"][0]["type"] == "metric"

    reread = store.room_for_participant(room_id, alice_token)["room"]
    reread_alice = next(participant for participant in reread["participants"] if participant["name"] == "alice")
    assert reread_alice["context_envelope"]["summary"] == "Alice keeps the prior handoff in view."
    assert reread["parent_room_id"] == "room_parent_123"


def test_context_and_lineage_validation_limits_are_enforced() -> None:
    with pytest.raises(ValueError):
        RoomCreateIn(
            topic="Lineage carry-forward",
            goal="Keep prior decisions visible",
            participants=["alice", "bob"],
            required_fields=[],
            parent_room_id="room_parent_123",
        )

    with pytest.raises(ValueError):
        ContextEnvelope(
            summary="x" * 2_000,
            refs=[
                ContextRef(type="url", label=f"ref-{idx}", value="v" * 1_000)
                for idx in range(16)
            ],
        )
