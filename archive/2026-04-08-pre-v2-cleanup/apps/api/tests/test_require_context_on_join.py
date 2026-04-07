from __future__ import annotations

from pathlib import Path

import pytest

from roombridge_core.models import ContextEnvelope, ContextRef, RoomCreateIn
from roombridge_store.service import RoomStore


def _make_store(tmp_path: Path) -> RoomStore:
    dsn = f"sqlite+pysqlite:///{tmp_path / 'ctx_join.db'}"
    store = RoomStore(dsn)
    store.init()
    return store


def _create_room(store: RoomStore, *, require_context: bool) -> dict:
    return store.create_room(
        RoomCreateIn(
            topic="context enforcement test",
            goal="Verify require_context_on_join behavior",
            participants=["agent_a", "agent_b"],
            required_fields=["outcome"],
            require_context_on_join=require_context,
        )
    )


def test_join_without_context_rejected_when_required(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    created = _create_room(store, require_context=True)
    room_id = str(created["room"]["id"])
    token_a = str(created["invites"]["agent_a"])

    with pytest.raises(ValueError, match="requires joining with owner context"):
        store.join(room_id, token_a, "a-client")


def test_join_with_empty_summary_rejected_when_required(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    created = _create_room(store, require_context=True)
    room_id = str(created["room"]["id"])
    token_a = str(created["invites"]["agent_a"])

    with pytest.raises(ValueError, match="requires joining with owner context"):
        store.join(room_id, token_a, "a-client", context_envelope={})


def test_join_with_context_accepted_when_required(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    created = _create_room(store, require_context=True)
    room_id = str(created["room"]["id"])
    token_a = str(created["invites"]["agent_a"])

    result = store.join(
        room_id,
        token_a,
        "a-client",
        context_envelope=ContextEnvelope(
            summary="My owner's position: we should focus on cost reduction",
            refs=[ContextRef(type="metric", label="budget", value="$50k")],
        ),
    )
    assert result["participant"] == "agent_a"
    assert result["room"]["require_context_on_join"] is True


def test_join_without_context_allowed_when_not_required(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    created = _create_room(store, require_context=False)
    room_id = str(created["room"]["id"])
    token_a = str(created["invites"]["agent_a"])

    result = store.join(room_id, token_a, "a-client")
    assert result["participant"] == "agent_a"
    assert result["room"]["require_context_on_join"] is False


def test_join_with_dict_context_accepted_when_required(tmp_path: Path) -> None:
    """Edge case: context_envelope passed as a raw dict (not ContextEnvelope)."""
    store = _make_store(tmp_path)
    created = _create_room(store, require_context=True)
    room_id = str(created["room"]["id"])
    token_a = str(created["invites"]["agent_a"])

    result = store.join(
        room_id,
        token_a,
        "a-client",
        context_envelope={"summary": "Owner says focus on UX", "refs": []},
    )
    assert result["participant"] == "agent_a"


def test_join_with_dict_no_summary_rejected_when_required(tmp_path: Path) -> None:
    """Edge case: dict context_envelope with empty summary."""
    store = _make_store(tmp_path)
    created = _create_room(store, require_context=True)
    room_id = str(created["room"]["id"])
    token_a = str(created["invites"]["agent_a"])

    with pytest.raises(ValueError, match="requires joining with owner context"):
        store.join(room_id, token_a, "a-client", context_envelope={"summary": "", "refs": []})


def test_rejoin_with_context_after_rejection(tmp_path: Path) -> None:
    """Edge case: agent tries without context, gets rejected, then retries with context."""
    store = _make_store(tmp_path)
    created = _create_room(store, require_context=True)
    room_id = str(created["room"]["id"])
    token_a = str(created["invites"]["agent_a"])

    with pytest.raises(ValueError, match="requires joining with owner context"):
        store.join(room_id, token_a, "a-client")

    # Retry with context should succeed
    result = store.join(
        room_id,
        token_a,
        "a-client",
        context_envelope=ContextEnvelope(summary="Now I have context from my owner"),
    )
    assert result["participant"] == "agent_a"


def test_room_snapshot_includes_require_context_flag(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    created = _create_room(store, require_context=True)
    assert created["room"]["require_context_on_join"] is True

    created2 = _create_room(store, require_context=False)
    assert created2["room"]["require_context_on_join"] is False
