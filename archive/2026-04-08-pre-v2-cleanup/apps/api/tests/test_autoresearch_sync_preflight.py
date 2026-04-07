from __future__ import annotations

from pathlib import Path

from roombridge_core.models import ContextEnvelope, ContextRef, MessageIn, OutcomeContract, RoomCreateIn
from roombridge_store.service import RoomStore


def _make_store(tmp_path: Path) -> RoomStore:
    dsn = f"sqlite+pysqlite:///{tmp_path / 'autoresearch_sync.db'}"
    store = RoomStore(dsn)
    store.init()
    return store


def _join_with_research_context(store: RoomStore, room_id: str, invite_token: str, agent_name: str, summary: str, best_bpb: str) -> None:
    store.join(
        room_id,
        invite_token,
        agent_name,
        ContextEnvelope(
            summary=summary,
            refs=[ContextRef(type="metric", label="best_val_bpb", value=best_bpb)],
        ),
    )


def _coordination_contract() -> OutcomeContract:
    return OutcomeContract(
        close_conditions={"min_turns": 4, "min_unique_participants": 2, "require_explicit_consensus": True},
        resolution_mode="consensus",
    )


def test_autoresearch_sync_room_chain_supports_three_cycles(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    last_room_id: str | None = None
    last_summary: str | None = None
    rooms: list[dict[str, object]] = []

    for cycle in range(1, 4):
        created = store.create_room(
            RoomCreateIn(
                topic=f"autoresearch sync cycle {cycle}",
                goal="Share findings, carry dead ends forward, and split next directions.",
                participants=["agent_a1", "agent_a2"],
                required_fields=[
                    "best_result_summary",
                    "dead_ends_summary",
                    "assignment_a1",
                    "assignment_a2",
                ],
                turn_limit=12,
                timeout_minutes=30,
                stall_limit=3,
                parent_room_id=last_room_id,
                prior_outcome_summary=last_summary,
                outcome_contract=_coordination_contract(),
            )
        )
        room_id = str(created["room"]["id"])
        token_a1 = str(created["invites"]["agent_a1"])
        token_a2 = str(created["invites"]["agent_a2"])

        _join_with_research_context(
            store,
            room_id,
            token_a1,
            "A1",
            f"cycle {cycle}: explored lr and warmup; best run stayed stable below 1e-3",
            f"0.94{cycle}",
        )
        _join_with_research_context(
            store,
            room_id,
            token_a2,
            "A2",
            f"cycle {cycle}: explored dropout and weight decay; regularization still matters",
            f"0.95{cycle}",
        )

        store.post_message(
            room_id,
            token_a1,
            MessageIn(
                intent="ASK",
                text="I explored lr and warmup. What did your dropout/weight-decay runs show?",
                expect_reply=True,
                meta={},
            ),
        )
        store.post_message(
            room_id,
            token_a2,
            MessageIn(
                intent="ANSWER",
                text="Dropout and weight decay still matter. Let's carry forward the dead end on lr>=1e-3 and split our next focus.",
                expect_reply=True,
                meta={},
            ),
        )
        store.post_message(
            room_id,
            token_a1,
            MessageIn(
                intent="ANSWER",
                text="Agreed. I will own lr tuning and you will own regularization next cycle.",
                fills={
                    "best_result_summary": f"cycle {cycle}: best val_bpb came from A1 on lr tuning",
                    "dead_ends_summary": "lr>=1e-3 diverges; attention heads>=12 overfits",
                    "assignment_a1": "focus: fine-tune lr in [4e-4, 8e-4]; keep heads fixed",
                    "assignment_a2": "focus: explore dropout+weight_decay; keep lr fixed",
                },
                expect_reply=True,
                meta={},
            ),
        )
        store.post_message(
            room_id,
            token_a2,
            MessageIn(
                intent="DONE",
                text="Split looks good. The dead ends and assignments are clear.",
                fills={
                    "best_result_summary": f"cycle {cycle}: best val_bpb came from A1 on lr tuning",
                    "dead_ends_summary": "lr>=1e-3 diverges; attention heads>=12 overfits",
                    "assignment_a1": "focus: fine-tune lr in [4e-4, 8e-4]; keep heads fixed",
                    "assignment_a2": "focus: explore dropout+weight_decay; keep lr fixed",
                },
                expect_reply=False,
                meta={},
            ),
        )
        final = store.post_message(
            room_id,
            token_a1,
            MessageIn(
                intent="DONE",
                text="Consensus confirmed. We can carry this plan into the next cycle.",
                fills={
                    "best_result_summary": f"cycle {cycle}: best val_bpb came from A1 on lr tuning",
                    "dead_ends_summary": "lr>=1e-3 diverges; attention heads>=12 overfits",
                    "assignment_a1": "focus: fine-tune lr in [4e-4, 8e-4]; keep heads fixed",
                    "assignment_a2": "focus: explore dropout+weight_decay; keep lr fixed",
                },
                expect_reply=False,
                meta={},
            ),
        )
        room = final["room"]
        rooms.append(
            {
                "room_id": room_id,
                "status": room["status"],
                "stop_reason": room["stop_reason"],
                "turn_count": room["turn_count"],
                "parent_room_id": room["parent_room_id"],
                "prior_outcome_summary": room["prior_outcome_summary"],
                "field_keys": sorted(room["fields"].keys()),
            }
        )
        last_room_id = room_id
        last_summary = f"cycle {cycle} locked lr-vs-regularization split and carried forward the dead ends."

    assert [room["status"] for room in rooms] == ["closed", "closed", "closed"]
    assert [room["stop_reason"] for room in rooms] == ["mutual_done", "mutual_done", "mutual_done"]
    assert rooms[0]["parent_room_id"] is None
    assert rooms[1]["parent_room_id"] == rooms[0]["room_id"]
    assert rooms[2]["parent_room_id"] == rooms[1]["room_id"]
    assert rooms[1]["prior_outcome_summary"] == "cycle 1 locked lr-vs-regularization split and carried forward the dead ends."
    assert rooms[2]["prior_outcome_summary"] == "cycle 2 locked lr-vs-regularization split and carried forward the dead ends."
    assert rooms[2]["field_keys"] == [
        "assignment_a1",
        "assignment_a2",
        "best_result_summary",
        "dead_ends_summary",
    ]


def test_autoresearch_sync_requires_reconfirmation_after_assignment_mutation(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    created = store.create_room(
        RoomCreateIn(
            topic="autoresearch assignment reset",
            goal="Assignment changes must force renewed consensus before close.",
            participants=["agent_a1", "agent_a2"],
            required_fields=["assignment_a1", "assignment_a2"],
            turn_limit=12,
            timeout_minutes=30,
            stall_limit=3,
            outcome_contract=_coordination_contract(),
        )
    )
    room_id = str(created["room"]["id"])
    token_a1 = str(created["invites"]["agent_a1"])
    token_a2 = str(created["invites"]["agent_a2"])

    store.join(room_id, token_a1, "A1")
    store.join(room_id, token_a2, "A2")

    store.post_message(
        room_id,
        token_a1,
        MessageIn(intent="ASK", text="I should own lr and you should own dropout. Does that split work?", expect_reply=True, meta={}),
    )
    store.post_message(
        room_id,
        token_a2,
        MessageIn(
            intent="ANSWER",
            text="Tentatively yes.",
            fills={"assignment_a1": "focus: lr", "assignment_a2": "focus: dropout"},
            expect_reply=True,
            meta={},
        ),
    )
    store.post_message(
        room_id,
        token_a1,
        MessageIn(
            intent="DONE",
            text="Locking lr/dropout split.",
            fills={"assignment_a1": "focus: lr", "assignment_a2": "focus: dropout"},
            expect_reply=False,
            meta={},
        ),
    )
    mutated = store.post_message(
        room_id,
        token_a2,
        MessageIn(
            intent="ANSWER",
            text="Actually switch me to warmup instead of dropout.",
            fills={"assignment_a2": "focus: warmup"},
            expect_reply=True,
            meta={},
        ),
    )
    mid = mutated["room"]

    store.post_message(
        room_id,
        token_a1,
        MessageIn(
            intent="DONE",
            text="Okay, relocking the new split.",
            fills={"assignment_a1": "focus: lr", "assignment_a2": "focus: warmup"},
            expect_reply=False,
            meta={},
        ),
    )
    final = store.post_message(
        room_id,
        token_a2,
        MessageIn(
            intent="DONE",
            text="Confirmed. Warmup is mine.",
            fills={"assignment_a1": "focus: lr", "assignment_a2": "focus: warmup"},
            expect_reply=False,
            meta={},
        ),
    )["room"]

    assert mid["status"] == "active"
    assert mid["stop_reason"] is None
    assert final["status"] == "closed"
    assert final["stop_reason"] == "mutual_done"
    assert final["fields"]["assignment_a2"]["value"] == "focus: warmup"
    assert final["turn_count"] == 6


def test_autoresearch_sync_blocks_close_when_assignment_is_missing(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    created = store.create_room(
        RoomCreateIn(
            topic="autoresearch incomplete sync",
            goal="A room must not close if one next-cycle assignment is still missing.",
            participants=["agent_a1", "agent_a2"],
            required_fields=["best_result_summary", "assignment_a1", "assignment_a2"],
            turn_limit=12,
            timeout_minutes=30,
            stall_limit=3,
            outcome_contract=_coordination_contract(),
        )
    )
    room_id = str(created["room"]["id"])
    token_a1 = str(created["invites"]["agent_a1"])
    token_a2 = str(created["invites"]["agent_a2"])

    store.join(room_id, token_a1, "A1")
    store.join(room_id, token_a2, "A2")

    store.post_message(room_id, token_a1, MessageIn(intent="ASK", text="Let's sync findings.", expect_reply=True, meta={}))
    store.post_message(
        room_id,
        token_a2,
        MessageIn(
            intent="ANSWER",
            text="We have a best result and only one assignment so far.",
            fills={"best_result_summary": "best is lr=6e-4", "assignment_a1": "focus: lr tuning"},
            expect_reply=True,
            meta={},
        ),
    )
    store.post_message(
        room_id,
        token_a1,
        MessageIn(
            intent="DONE",
            text="I think we're done.",
            fills={"best_result_summary": "best is lr=6e-4", "assignment_a1": "focus: lr tuning"},
            expect_reply=False,
            meta={},
        ),
    )
    final = store.post_message(
        room_id,
        token_a2,
        MessageIn(
            intent="DONE",
            text="Still missing the second assignment.",
            fills={"best_result_summary": "best is lr=6e-4", "assignment_a1": "focus: lr tuning"},
            expect_reply=False,
            meta={},
        ),
    )["room"]

    assert final["status"] == "active"
    assert final["stop_reason"] is None
    assert sorted(final["fields"].keys()) == ["assignment_a1", "best_result_summary"]
    assert "assignment_a2" not in final["fields"]
    assert final["turn_count"] == 4
