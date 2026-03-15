from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

def _base_message(
    *,
    intent: str,
    text: str,
    expect_reply: bool,
    fills: dict[str, str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "text": text,
        "fills": fills or {},
        "facts": [],
        "questions": [],
        "expect_reply": expect_reply,
        "meta": meta or {},
    }


def _participant(room_snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    return next(p for p in room_snapshot["participants"] if p["name"] == name)


def _join_both(api: Any, room: Any) -> None:
    api.join(room.room_id, room.invites["host"], "CT-Host")
    api.join(room.room_id, room.invites["guest"], "CT-Guest")


def _cleanup(api: Any, room: Any) -> None:
    try:
        api.close(room.room_id, room.host_token, "conformance_cleanup")
    except Exception:
        pass


def test_ct01_join_info_not_join(api: Any) -> None:
    room = api.create_room()
    try:
        info = api.join_info(room.room_id, room.invites["host"])
        assert info["participant"] == "host"
        assert _participant(info["room"], "host")["joined"] is False

        joined = api.join(room.room_id, room.invites["host"], "CT-Host")
        assert _participant(joined["room"], "host")["joined"] is True
    finally:
        _cleanup(api, room)


def test_ct02_relay_gating_for_expect_reply_true(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        baseline = int(api.events(room.room_id, room.invites["guest"], after=0)["next_cursor"])

        api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="ASK", text="CT02 question", expect_reply=True),
        )
        batch = api.events(room.room_id, room.invites["guest"], after=baseline)
        relays = [
            e for e in batch["events"]
            if e["type"] == "relay"
            and (e.get("payload") or {}).get("from") == "host"
            and ((e.get("payload") or {}).get("message") or {}).get("intent") == "ASK"
        ]
        assert relays, "ASK with expect_reply=true must relay to peer"
    finally:
        _cleanup(api, room)


def test_ct11_ask_expect_reply_false_is_coerced_and_relays(api: Any) -> None:
    """
    Real-world bridges can accidentally mark an ASK as expect_reply=false, which
    would otherwise silently stall (no relay). The server must correct this.
    """
    room = api.create_room()
    try:
        _join_both(api, room)
        baseline = int(api.events(room.room_id, room.invites["guest"], after=0)["next_cursor"])

        api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="ASK", text="CT11 coerced ask", expect_reply=False),
        )
        batch = api.events(room.room_id, room.invites["guest"], after=baseline)

        msg_events = [
            e for e in batch["events"]
            if e["type"] == "msg"
            and ((e.get("payload") or {}).get("message") or {}).get("sender") == "host"
            and ((e.get("payload") or {}).get("message") or {}).get("intent") == "ASK"
        ]
        assert msg_events, "ASK should produce a visible msg event"
        msg = (msg_events[0].get("payload") or {}).get("message") or {}
        assert msg.get("expect_reply") is True, "Server must coerce ASK expect_reply=true"
        overrides = (msg.get("meta") or {}).get("server_overrides") or []
        assert "ASK.expect_reply=true" in overrides

        relays = [
            e for e in batch["events"]
            if e["type"] == "relay"
            and (e.get("payload") or {}).get("from") == "host"
            and ((e.get("payload") or {}).get("message") or {}).get("intent") == "ASK"
        ]
        assert relays, "Coerced ASK must relay to peer"
    finally:
        _cleanup(api, room)


def test_ct03_note_hard_rule_no_relay(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        baseline = int(api.events(room.room_id, room.invites["guest"], after=0)["next_cursor"])

        api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="NOTE", text="CT03 note", expect_reply=True),
        )
        batch = api.events(room.room_id, room.invites["guest"], after=baseline)
        bad_relays = [
            e for e in batch["events"]
            if e["type"] == "relay"
            and ((e.get("payload") or {}).get("message") or {}).get("intent") == "NOTE"
        ]
        assert not bad_relays, "NOTE must never produce relay even if expect_reply=true from client"
    finally:
        _cleanup(api, room)


def test_ct04_ask_owner_semantics(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        baseline = int(api.events(room.room_id, room.invites["guest"], after=0)["next_cursor"])

        sent = api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="ASK_OWNER", text="Need owner guidance", expect_reply=True),
        )
        host_state = _participant(sent["room"], "host")
        assert host_state["waiting_owner"] is True

        batch = api.events(room.room_id, room.invites["guest"], after=baseline)
        event_types = [e["type"] for e in batch["events"]]
        assert "owner_wait" in event_types
        ask_owner_relays = [
            e for e in batch["events"]
            if e["type"] == "relay"
            and ((e.get("payload") or {}).get("message") or {}).get("intent") == "ASK_OWNER"
        ]
        assert not ask_owner_relays, "ASK_OWNER must not relay to peer"
    finally:
        _cleanup(api, room)


def test_ct05_owner_reply_resumes_and_relays_when_expect_reply_true(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        cursor = int(api.events(room.room_id, room.invites["guest"], after=0)["next_cursor"])

        api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="ASK_OWNER", text="Need owner", expect_reply=False),
        )
        batch_ask_owner = api.events(room.room_id, room.invites["guest"], after=cursor)
        cursor = int(batch_ask_owner["next_cursor"])

        resumed = api.send(
            room.room_id,
            room.invites["host"],
            _base_message(
                intent="OWNER_REPLY",
                text="Owner confirms threshold",
                expect_reply=True,
                meta={"owner_req_id": "ct05"},
            ),
        )
        assert _participant(resumed["room"], "host")["waiting_owner"] is False

        batch_resume = api.events(room.room_id, room.invites["guest"], after=cursor)
        event_types = [e["type"] for e in batch_resume["events"]]
        assert "owner_resume" in event_types
        owner_reply_relays = [
            e for e in batch_resume["events"]
            if e["type"] == "relay"
            and ((e.get("payload") or {}).get("message") or {}).get("intent") == "OWNER_REPLY"
        ]
        assert owner_reply_relays, "OWNER_REPLY should relay when expect_reply=true"
    finally:
        _cleanup(api, room)


def test_ct06_done_visible_to_peers_even_when_expect_reply_false(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        baseline = int(api.events(room.room_id, room.invites["guest"], after=0)["next_cursor"])

        api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="DONE", text="Done from host", expect_reply=False),
        )
        batch = api.events(room.room_id, room.invites["guest"], after=baseline)
        done_relays = [
            e for e in batch["events"]
            if e["type"] == "relay"
            and ((e.get("payload") or {}).get("message") or {}).get("intent") == "DONE"
        ]
        assert done_relays, "DONE should be visible to peers even if expect_reply=false"
    finally:
        _cleanup(api, room)


def test_ct07_mutual_done_blocked_when_required_fields_missing(api: Any) -> None:
    room = api.create_room(required_fields=["decision"])
    try:
        _join_both(api, room)
        api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="DONE", text="done host", expect_reply=False),
        )
        api.send(
            room.room_id,
            room.invites["guest"],
            _base_message(intent="DONE", text="done guest", expect_reply=False),
        )
        snap = api.room(room.room_id, host_token=room.host_token)["room"]
        assert snap["status"] == "active"
        assert snap["lifecycle_state"] == "input_required"
    finally:
        _cleanup(api, room)


def test_ct08_goal_done_when_required_fields_complete(api: Any) -> None:
    room = api.create_room(required_fields=["decision"])
    try:
        _join_both(api, room)
        sent = api.send(
            room.room_id,
            room.invites["host"],
            _base_message(
                intent="ANSWER",
                text="final decision set",
                expect_reply=False,
                fills={"decision": "sushi"},
                meta={"complete": True},
            ),
        )
        assert sent["room"]["status"] == "closed"
        assert sent["room"]["stop_reason"] == "goal_done"
    finally:
        _cleanup(api, room)


def test_ct09_idempotent_reply_by_in_reply_to_event_id(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        baseline = int(api.events(room.room_id, room.invites["guest"], after=0)["next_cursor"])

        api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="ASK", text="CT09 question", expect_reply=True),
        )
        batch = api.events(room.room_id, room.invites["guest"], after=baseline)
        relay_id = int(
            next(
                e["id"] for e in batch["events"]
                if e["type"] == "relay"
                and ((e.get("payload") or {}).get("message") or {}).get("intent") == "ASK"
            )
        )

        send1 = api.send(
            room.room_id,
            room.invites["guest"],
            _base_message(
                intent="ANSWER",
                text="first answer",
                expect_reply=False,
                meta={"in_reply_to_event_id": relay_id},
            ),
        )
        turn_after_first = int(send1["room"]["turn_count"])

        send2 = api.send(
            room.room_id,
            room.invites["guest"],
            _base_message(
                intent="ANSWER",
                text="duplicate answer",
                expect_reply=False,
                meta={"in_reply_to_event_id": relay_id},
            ),
        )
        assert send2.get("dedup_hit") is True
        assert int(send2["room"]["turn_count"]) == turn_after_first
    finally:
        _cleanup(api, room)


def test_ct10_cursor_monotonic_and_no_replay(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        first = api.events(room.room_id, room.invites["host"], after=0)
        c0 = int(first["next_cursor"])

        api.send(
            room.room_id,
            room.invites["guest"],
            _base_message(intent="ASK", text="CT10 message A", expect_reply=True),
        )
        second = api.events(room.room_id, room.invites["host"], after=c0)
        ids_second = [int(e["id"]) for e in second["events"]]
        assert ids_second, "new events expected after c0"
        assert min(ids_second) > c0
        c1 = int(second["next_cursor"])
        assert c1 >= max(ids_second)

        third = api.events(room.room_id, room.invites["host"], after=c1)
        assert third["events"] == []
        assert int(third["next_cursor"]) == c1

        api.send(
            room.room_id,
            room.invites["guest"],
            _base_message(intent="NOTE", text="CT10 message B", expect_reply=False),
        )
        fourth = api.events(room.room_id, room.invites["host"], after=c1)
        ids_fourth = [int(e["id"]) for e in fourth["events"]]
        assert ids_fourth
        assert min(ids_fourth) > c1
    finally:
        _cleanup(api, room)


def test_ct14_joined_gate_blocks_unjoined_participant_from_stateful_endpoints(api: Any) -> None:
    room = api.create_room()
    try:
        info = api.join_info(room.room_id, room.invites["host"])
        host_state = _participant(info["room"], "host")
        assert host_state["joined"] is False
        assert host_state["online"] is False

        room_resp = api.room(room.room_id, invite_token=room.invites["host"])
        room_host_state = _participant(room_resp["room"], "host")
        assert room_host_state["joined"] is False
        assert room_host_state["online"] is False, "GET room must not make participant appear online before join"

        assert api.heartbeat_response(room.room_id, room.invites["host"]).status_code == 409
        assert api.events_response(room.room_id, room.invites["host"]).status_code == 409
        assert api.send_response(
            room.room_id,
            room.invites["host"],
            _base_message(intent="ASK", text="should fail", expect_reply=True),
        ).status_code == 409
        assert api.leave_response(room.room_id, room.invites["host"]).status_code == 409
    finally:
        _cleanup(api, room)


def test_ct15_close_idempotency_does_not_duplicate_close_lifecycle(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        first = api.close(room.room_id, room.host_token, "ct15 first close")
        assert first["room"]["status"] == "closed"
        assert first["room"]["stop_reason"] == "manual_close"

        second = api.close(room.room_id, room.host_token, "ct15 second close")
        assert second["room"]["status"] == "closed"
        assert second["room"]["stop_reason"] == "manual_close"
        assert second.get("already_closed") is True

        events = api.monitor_events(room.room_id, room.host_token, after=0, limit=200)["events"]
        closed_status = [
            e for e in events
            if e["type"] == "status" and (e.get("payload") or {}).get("status") == "closed"
        ]
        assert len(closed_status) == 1, "Repeated close must not append duplicate closed status events"
    finally:
        _cleanup(api, room)


def test_ct16_waiting_owner_clears_on_valid_continuation(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        baseline = int(api.events(room.room_id, room.invites["guest"], after=0)["next_cursor"])

        asked = api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="ASK_OWNER", text="Need owner input", expect_reply=False),
        )
        assert _participant(asked["room"], "host")["waiting_owner"] is True

        resumed = api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="ANSWER", text="Continuing with my best judgment", expect_reply=False),
        )
        assert _participant(resumed["room"], "host")["waiting_owner"] is False
        assert resumed["room"]["lifecycle_state"] == "working"

        batch = api.events(room.room_id, room.invites["guest"], after=baseline)
        event_types = [e["type"] for e in batch["events"]]
        assert "owner_wait" in event_types
        assert "owner_resume" in event_types
    finally:
        _cleanup(api, room)


def test_ct17_required_fields_complete_without_signal_does_not_close(api: Any) -> None:
    room = api.create_room(required_fields=["decision"])
    try:
        _join_both(api, room)
        sent = api.send(
            room.room_id,
            room.invites["host"],
            _base_message(
                intent="ANSWER",
                text="proposal only",
                expect_reply=False,
                fills={"decision": "sushi"},
            ),
        )
        assert sent["room"]["status"] == "active"
        assert sent["room"]["stop_reason"] is None

        done = api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="DONE", text="Decision finalized", expect_reply=False),
        )
        assert done["room"]["status"] == "closed"
        assert done["room"]["stop_reason"] == "goal_done"
    finally:
        _cleanup(api, room)


def test_ct18_participant_stream_relays_audience_scoped_events(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        baseline = int(api.events(room.room_id, room.invites["guest"], after=0)["next_cursor"])

        stream_url = (
            f"{api.base_url}/rooms/{room.room_id}/stream"
            f"?invite_token={room.invites['guest']}&after={baseline}"
        )
        with httpx.Client(timeout=5.0, trust_env=False) as stream_client:
            with stream_client.stream("GET", stream_url) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")

                api.send(
                    room.room_id,
                    room.invites["host"],
                    _base_message(intent="ASK", text="CT18 stream question", expect_reply=True),
                )

                current_event = ""
                relay_payload: dict[str, Any] | None = None
                for raw_line in response.iter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("event: "):
                        current_event = line[len("event: ") :]
                        continue
                    if line.startswith("data: "):
                        payload = json.loads(line[len("data: ") :])
                        if (
                            current_event == "relay"
                            and (payload.get("payload") or {}).get("from") == "host"
                            and (((payload.get("payload") or {}).get("message") or {}).get("intent") == "ASK")
                        ):
                            relay_payload = payload
                            break

                assert relay_payload is not None, "participant stream must deliver relay events for the joined audience"
    finally:
        _cleanup(api, room)


def test_ct19_runner_claim_renew_release_updates_snapshot_and_status(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        claim = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-a",
            status="ready",
            execution_mode="managed_attached",
            log_ref="/tmp/host-a.log",
        )
        attempt_id = str(claim["attempt_id"])
        claimed_room = claim["room"]
        assert claimed_room["execution_mode"] == "managed_attached"
        assert claimed_room["runner_certification"] == "candidate"
        assert claimed_room["managed_coverage"] == "partial"
        assert claimed_room["product_owned"] is False
        assert claimed_room["automatic_recovery_eligible"] is False
        assert claimed_room["attempt_status"] == "ready"
        assert claimed_room["active_runner_id"] == "runner-host-a"
        assert claimed_room["active_runner_count"] == 1
        assert claimed_room["runner_attempts"][0]["attempt_id"] == attempt_id
        assert claimed_room["runner_attempts"][0]["managed_certified"] is False
        assert claimed_room["runner_attempts"][0]["recovery_policy"] == "takeover_only"
        assert claimed_room["runner_attempts"][0]["phase"] == "claimed"

        renewed = api.runner_renew(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-a",
            attempt_id=attempt_id,
            status="waiting_owner",
            recovery_reason="owner_pause",
            phase="owner_wait",
            phase_detail="waiting_owner_reply",
        )
        renewed_room = renewed["room"]
        assert renewed_room["attempt_status"] == "waiting_owner"
        assert renewed_room["last_recovery_reason"] == "owner_pause"
        assert renewed_room["runner_attempts"][0]["phase"] == "owner_wait"
        assert renewed_room["runner_attempts"][0]["phase_detail"] == "waiting_owner_reply"

        status_view = api.runner_status(room.room_id, host_token=room.host_token)
        attempts = status_view["attempts"]
        assert len(attempts) == 1
        assert attempts[0]["runner_id"] == "runner-host-a"
        assert attempts[0]["status"] == "waiting_owner"
        assert attempts[0]["managed_certified"] is False
        assert attempts[0]["recovery_policy"] == "takeover_only"
        assert attempts[0]["phase"] == "owner_wait"

        released = api.runner_release(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-a",
            attempt_id=attempt_id,
            reason="normal_exit",
        )
        released_room = released["room"]
        assert released_room["active_runner_id"] is None
        assert released_room["active_runner_count"] == 0
        assert released_room["attempt_status"] in {"idle", "exited"}
    finally:
        _cleanup(api, room)


def test_ct20_start_slo_tracks_first_join_and_first_relay(api: Any) -> None:
    room = api.create_room()
    try:
        host_join = api.join(room.room_id, room.invites["host"], "CT-Host")
        start_slo_after_join = host_join["room"]["start_slo"]
        assert start_slo_after_join["room_created_at"]
        assert start_slo_after_join["first_joined_at"]
        assert start_slo_after_join["join_latency_ms"] is not None
        assert start_slo_after_join["first_relay_at"] is None

        api.join(room.room_id, room.invites["guest"], "CT-Guest")
        sent = api.send(
            room.room_id,
            room.invites["host"],
            _base_message(intent="ASK", text="CT20 kickoff", expect_reply=True),
        )
        start_slo_after_relay = sent["room"]["start_slo"]
        assert start_slo_after_relay["first_relay_at"] is not None
        assert start_slo_after_relay["first_relay_latency_ms"] is not None
        assert int(start_slo_after_relay["first_relay_latency_ms"]) >= 0
    finally:
        _cleanup(api, room)


def test_ct21_execution_attention_marks_compatibility_rooms_and_clears_for_managed_runner(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        snapshot = api.room(room.room_id, host_token=room.host_token)["room"]
        attention = snapshot["execution_attention"]
        assert attention["state"] == "attention"
        assert "compatibility_mode" in attention["reasons"]
        assert "no_managed_runner" in attention["reasons"]
        assert attention["takeover_required"] is False

        claimed = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-b",
            status="active",
            execution_mode="managed_attached",
            managed_certified=True,
            recovery_policy="automatic",
        )["room"]
        managed_attention = claimed["execution_attention"]
        assert claimed["runner_certification"] == "candidate"
        assert claimed["managed_coverage"] == "partial"
        assert claimed["product_owned"] is False
        assert claimed["automatic_recovery_eligible"] is False
        assert managed_attention["state"] == "takeover_required"
        assert "replacement_pending" in managed_attention["reasons"]
        assert claimed["repair_hint"]["available"] is True
        assert [item["name"] for item in claimed["repair_hint"]["participants"]] == ["guest"]

        fully_managed = api.runner_claim(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-b",
            status="active",
            execution_mode="managed_attached",
            managed_certified=True,
            recovery_policy="automatic",
        )["room"]
        assert fully_managed["managed_coverage"] == "full"
        assert fully_managed["runner_certification"] == "certified"
        assert fully_managed["automatic_recovery_eligible"] is True
        assert fully_managed["product_owned"] is True
    finally:
        _cleanup(api, room)


def test_ct22_execution_attention_flags_half_closed_compatibility_rooms(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        api.send(
            room.room_id,
            room.invites["guest"],
            _base_message(intent="DONE", text="Guest is done", expect_reply=False),
        )
        snapshot = api.room(room.room_id, host_token=room.host_token)["room"]
        attention = snapshot["execution_attention"]
        assert attention["state"] in {"attention", "takeover_recommended", "takeover_required"}
        assert "awaiting_mutual_completion" in attention["reasons"]
        assert "terminal_turn_without_room_close" in attention["reasons"]
    finally:
        _cleanup(api, room)


def test_ct23_uncertified_managed_runner_is_visible_as_attention(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        claimed = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-candidate",
            status="active",
            execution_mode="managed_attached",
        )["room"]
        attention = claimed["execution_attention"]
        assert claimed["runner_certification"] == "candidate"
        assert claimed["automatic_recovery_eligible"] is False
        assert attention["state"] == "takeover_required"
        assert "managed_runner_uncertified" in attention["reasons"]
        assert "replacement_pending" in attention["reasons"]
    finally:
        _cleanup(api, room)


def test_ct24_terminal_no_reply_plus_done_infers_goal_done(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        api.send(
            room.room_id,
            room.invites["guest"],
            _base_message(
                intent="ANSWER",
                text="Pizza sounds good. Let's do that tonight.",
                expect_reply=False,
            ),
        )
        sent = api.send(
            room.room_id,
            room.invites["host"],
            _base_message(
                intent="DONE",
                text="Great, pizza it is.",
                expect_reply=False,
            ),
        )
        assert sent["room"]["status"] == "closed"
        assert sent["room"]["stop_reason"] == "goal_done"
        assert "inferred completion" in str(sent["room"]["stop_detail"] or "")
    finally:
        _cleanup(api, room)


def test_ct25_host_can_reissue_repair_invite_for_participant(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-candidate",
            status="active",
            execution_mode="managed_attached",
        )
        guest_claim = api.runner_claim(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-candidate",
            status="idle",
            execution_mode="managed_attached",
        )
        guest_attempt_id = str(guest_claim["attempt_id"])
        claimed = api.runner_release(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-candidate",
            attempt_id=guest_attempt_id,
            status="exited",
            reason="client_exit",
        )["room"]
        assert claimed["repair_hint"]["available"] is True
        assert any(item["name"] == "guest" for item in claimed["repair_hint"]["participants"])

        repair = api.repair_invite(room.room_id, room.host_token, "guest")
        assert repair["participant"] == "guest"
        assert repair["invalidates_previous_invite"] is True
        assert f"/join/{room.room_id}?token=inv_" in str(repair["join_link"])
        assert "openclaw-shell-bridge.sh" in repair["repair_command"]

        old_room_resp = api.room_response(room.room_id, invite_token=room.invites["guest"])
        assert old_room_resp.status_code == 401

        new_token = str(repair["invite_token"])
        repaired_join = api.join(room.room_id, new_token, "CT-Guest-Repaired")
        assert _participant(repaired_join["room"], "guest")["joined"] is True

        runner_status = api.runner_status(room.room_id, host_token=room.host_token)
        assert "repair_invite_reissued" in str((runner_status["room"] or {}).get("last_recovery_reason") or "")
    finally:
        _cleanup(api, room)


def test_ct26_partial_managed_recovery_keeps_repair_hint_for_missing_participant(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-candidate",
            status="active",
            execution_mode="managed_attached",
        )
        guest_claim = api.runner_claim(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-candidate",
            status="active",
            execution_mode="managed_attached",
        )
        guest_attempt_id = str(guest_claim["attempt_id"])

        released = api.runner_release(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-candidate",
            attempt_id=guest_attempt_id,
            status="exited",
            reason="client_exit",
        )
        room_snapshot = released["room"]
        assert room_snapshot["attempt_status"] == "active"
        assert room_snapshot["active_runner_count"] == 1
        assert "replacement_pending" in list((room_snapshot["execution_attention"] or {}).get("reasons") or [])
        repair_hint = room_snapshot["repair_hint"]
        assert repair_hint["available"] is True
        repair_participants = [item["name"] for item in repair_hint["participants"]]
        assert repair_participants == ["guest"]
    finally:
        _cleanup(api, room)


def test_ct27_recovery_action_tracks_pending_issued_and_resolved(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        claimed = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-certified",
            status="active",
            execution_mode="managed_attached",
            managed_certified=True,
            recovery_policy="automatic",
        )["room"]
        pending_actions = [action for action in claimed["recovery_actions"] if action["participant"] == "guest" and action["current"]]
        assert len(pending_actions) == 1
        assert pending_actions[0]["status"] == "pending"

        repair = api.repair_invite(room.room_id, room.host_token, "guest")
        issued_actions = [action for action in repair["room"]["recovery_actions"] if action["participant"] == "guest"]
        assert len(issued_actions) == 1
        assert issued_actions[0]["current"] is True
        assert issued_actions[0]["status"] == "issued"
        assert issued_actions[0]["issue_count"] == 1

        new_token = str(repair["invite_token"])
        api.join(room.room_id, new_token, "CT-Guest-Repaired")
        recovered = api.runner_claim(
            room.room_id,
            new_token,
            runner_id="runner-guest-certified",
            status="active",
            execution_mode="managed_attached",
            managed_certified=True,
            recovery_policy="automatic",
        )["room"]
        resolved_actions = [action for action in recovered["recovery_actions"] if action["participant"] == "guest"]
        assert len(resolved_actions) == 1
        assert resolved_actions[0]["status"] == "resolved"
        assert resolved_actions[0]["current"] is False
        assert recovered["repair_hint"]["available"] is False
        events = api.monitor_events(room.room_id, room.host_token, after=0, limit=200)["events"]
        resolved_events = [event for event in events if event["type"] == "recovery_action_resolved"]
        assert resolved_events
        guest_resolved = resolved_events[-1]["payload"]
        assert guest_resolved["participant"] == "guest"
        assert guest_resolved["previous_status"] == "issued"
        assert guest_resolved["claim_latency_ms"] is None or int(guest_resolved["claim_latency_ms"]) >= 0
    finally:
        _cleanup(api, room)


def test_ct28_close_supersedes_current_recovery_actions(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        claimed = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-certified",
            status="active",
            execution_mode="managed_attached",
            managed_certified=True,
            recovery_policy="automatic",
        )["room"]
        assert any(action["current"] for action in claimed["recovery_actions"])

        closed = api.close(room.room_id, room.host_token, "ct28 close with recovery backlog")
        closed_actions = [action for action in closed["room"]["recovery_actions"] if action["participant"] == "guest"]
        assert len(closed_actions) == 1
        assert closed_actions[0]["status"] == "superseded"
        assert closed_actions[0]["current"] is False
    finally:
        _cleanup(api, room)


def test_ct29_uncertified_live_runner_does_not_create_repair_backlog(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-candidate",
            status="active",
            execution_mode="managed_attached",
        )
        claimed = api.runner_claim(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-candidate",
            status="active",
            execution_mode="managed_attached",
        )["room"]
        assert claimed["execution_attention"]["state"] in {"attention", "takeover_required"}
        assert "managed_runner_uncertified" in list((claimed["execution_attention"] or {}).get("reasons") or [])
        assert "replacement_pending" not in list((claimed["execution_attention"] or {}).get("reasons") or [])
        assert claimed["repair_hint"]["available"] is False
        assert not [action for action in claimed["recovery_actions"] if action["current"]]
    finally:
        _cleanup(api, room)


def test_ct30_certified_automatic_gap_auto_issues_recovery_package(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        host_claim = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-certified",
            status="active",
            execution_mode="managed_attached",
            managed_certified=True,
            recovery_policy="automatic",
        )["room"]
        host_attempt_id = next(
            attempt["attempt_id"]
            for attempt in host_claim["runner_attempts"]
            if attempt["participant"] == "host" and attempt["current"]
        )
        guest_claim = api.runner_claim(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-certified",
            status="active",
            execution_mode="managed_attached",
            managed_certified=True,
            recovery_policy="automatic",
        )["room"]
        guest_attempt_id = next(
            attempt["attempt_id"]
            for attempt in guest_claim["runner_attempts"]
            if attempt["participant"] == "guest" and attempt["current"]
        )

        released = api.runner_release(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-certified",
            attempt_id=guest_attempt_id,
            status="exited",
            reason="simulated_failure",
        )["room"]
        assert "replacement_pending" in list((released["execution_attention"] or {}).get("reasons") or [])
        assert "Watch for managed replacement" in str((released["execution_attention"] or {}).get("next_action") or "")

        guest_actions = [action for action in released["recovery_actions"] if action["participant"] == "guest" and action["current"]]
        assert len(guest_actions) == 1
        assert guest_actions[0]["delivery_mode"] == "automatic"
        assert guest_actions[0]["status"] == "issued"
        assert guest_actions[0]["package_ready"] is True

        recovery = api.recovery_actions(room.room_id, room.host_token)
        host_guest_actions = [action for action in recovery["recovery_actions"] if action["participant"] == "guest" and action["current"]]
        assert len(host_guest_actions) == 1
        package = host_guest_actions[0]["package"]
        assert package is not None
        assert str(package["invite_token"]).startswith("inv_")
        assert str(package["join_link"]).startswith("http")
        assert room.room_id in str(package["join_link"])
        assert "openclaw-shell-bridge.sh" in str(package["repair_command"])

        participant_room = api.room(room.room_id, invite_token=room.invites["host"])["room"]
        participant_guest_actions = [action for action in participant_room["recovery_actions"] if action["participant"] == "guest" and action["current"]]
        assert len(participant_guest_actions) == 1
        assert participant_guest_actions[0]["package_ready"] is True
        assert "invite_token" not in json.dumps(participant_guest_actions[0])

        refresh = api.runner_renew(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-certified",
            attempt_id=host_attempt_id,
            status="idle",
            managed_certified=True,
            recovery_policy="automatic",
        )["room"]
        assert any(action["participant"] == "guest" and action["status"] == "issued" for action in refresh["recovery_actions"])
    finally:
        _cleanup(api, room)


def test_ct31_manual_repair_issue_is_reflected_in_execution_attention(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-candidate",
            status="active",
            execution_mode="managed_attached",
            managed_certified=False,
            recovery_policy="takeover_only",
        )
        guest_claim = api.runner_claim(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-candidate",
            status="active",
            execution_mode="managed_attached",
            managed_certified=False,
            recovery_policy="takeover_only",
        )["room"]
        guest_attempt_id = next(
            attempt["attempt_id"]
            for attempt in guest_claim["runner_attempts"]
            if attempt["participant"] == "guest" and attempt["current"]
        )

        api.runner_release(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-candidate",
            attempt_id=guest_attempt_id,
            status="exited",
            reason="manual_repair_drill",
        )
        repaired = api.repair_invite(room.room_id, room.host_token, "guest")["room"]
        reasons = list((repaired["execution_attention"] or {}).get("reasons") or [])
        assert "replacement_pending" in reasons
        assert "repair_package_issued" in reasons
        assert "repair_claim_overdue" not in reasons
        assert "already issued" in str((repaired["execution_attention"] or {}).get("next_action") or "")
        hint_codes = [str((hint or {}).get("code") or "") for hint in repaired.get("root_cause_hints") or []]
        assert "runner_lost_before_first_relay" in hint_codes
        assert "repair_package_sent_unclaimed" in hint_codes
        assert "managed_runtime_uncertified" in hint_codes
        guest_actions = [action for action in repaired["recovery_actions"] if action["participant"] == "guest" and action["current"]]
        assert len(guest_actions) == 1
        assert guest_actions[0]["status"] == "issued"
        assert guest_actions[0]["delivery_mode"] == "manual"
    finally:
        _cleanup(api, room)


def test_ct31b_deterministic_runnerd_failure_prepares_manual_repair_immediately(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-candidate",
            status="active",
            execution_mode="managed_attached",
            managed_certified=False,
            recovery_policy="takeover_only",
        )
        guest_claim = api.runner_claim(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-candidate",
            status="active",
            execution_mode="managed_attached",
            managed_certified=False,
            recovery_policy="takeover_only",
        )["room"]
        guest_attempt_id = next(
            attempt["attempt_id"]
            for attempt in guest_claim["runner_attempts"]
            if attempt["participant"] == "guest" and attempt["current"]
        )

        released = api.runner_release(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-candidate",
            attempt_id=guest_attempt_id,
            status="abandoned",
            reason="runnerd_restart_exhausted_after_claim",
        )["room"]
        reasons = list((released["execution_attention"] or {}).get("reasons") or [])
        assert "replacement_pending" in reasons
        assert "repair_package_issued" in reasons, "Deterministic runnerd failures should issue manual repair immediately"

        guest_actions = [action for action in released["recovery_actions"] if action["participant"] == "guest" and action["current"]]
        assert len(guest_actions) == 1
        assert guest_actions[0]["status"] == "issued"
        assert guest_actions[0]["delivery_mode"] == "manual"
        assert guest_actions[0]["package_ready"] is True
    finally:
        _cleanup(api, room)


def test_ct32_recovery_actions_endpoint_requires_host_token(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        response = api.recovery_actions_response(room.room_id, invite_token=room.invites["host"])
        assert response.status_code == 401
    finally:
        _cleanup(api, room)


def test_ct33_root_cause_hints_use_runner_phase_checkpoints(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        host_claim = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-phase",
            status="active",
            execution_mode="managed_attached",
            phase="reply_generating",
            phase_detail="room_start",
        )["room"]
        host_attempt_id = next(
            attempt["attempt_id"]
            for attempt in host_claim["runner_attempts"]
            if attempt["participant"] == "host" and attempt["current"]
        )
        api.runner_release(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-phase",
            attempt_id=host_attempt_id,
            status="exited",
            reason="diagnostic_release",
        )
        snapshot = api.room(room.room_id, host_token=room.host_token)["room"]
        hint_codes = [str((hint or {}).get("code") or "") for hint in snapshot.get("root_cause_hints") or []]
        assert "runner_lost_before_first_relay" in hint_codes
        assert "runner_lost_during_reply_generation" in hint_codes
        reply_generation_hint = next(
            hint for hint in snapshot["root_cause_hints"] if hint["code"] == "runner_lost_during_reply_generation"
        )
        assert any("reply_generating" in str(item) for item in reply_generation_hint["evidence"])
    finally:
        _cleanup(api, room)


def test_ct34_root_cause_hints_surface_runner_termination_signal(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        host_claim = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-signal",
            status="active",
            execution_mode="managed_attached",
            phase="waiting_for_peer_join",
            phase_detail="initiator_waiting_for_peer",
        )["room"]
        host_attempt_id = next(
            attempt["attempt_id"]
            for attempt in host_claim["runner_attempts"]
            if attempt["participant"] == "host" and attempt["current"]
        )
        api.runner_release(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-signal",
            attempt_id=host_attempt_id,
            status="exited",
            reason="signal_term",
            last_error="signal:TERM",
        )
        snapshot = api.room(room.room_id, host_token=room.host_token)["room"]
        hint_codes = [str((hint or {}).get("code") or "") for hint in snapshot.get("root_cause_hints") or []]
        assert "runner_received_termination_signal" in hint_codes
        targeted = next(
            hint for hint in snapshot["root_cause_hints"] if hint["code"] == "runner_received_termination_signal"
        )
        assert targeted["evidence"] == ["host:waiting_for_peer_join:signal_term"]
    finally:
        _cleanup(api, room)


def test_ct35_root_cause_hints_surface_lease_expired_phase(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        host_claim = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-lease",
            status="active",
            execution_mode="managed_attached",
            phase="waiting_for_peer_join",
            phase_detail="initiator_waiting_for_peer",
        )["room"]
        host_attempt_id = next(
            attempt["attempt_id"]
            for attempt in host_claim["runner_attempts"]
            if attempt["participant"] == "host" and attempt["current"]
        )
        api.runner_release(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-lease",
            attempt_id=host_attempt_id,
            status="abandoned",
            reason="lease_expired",
        )
        snapshot = api.room(room.room_id, host_token=room.host_token)["room"]
        hint_codes = [str((hint or {}).get("code") or "") for hint in snapshot.get("root_cause_hints") or []]
        assert "lease_expired_during_relay_wait" in hint_codes
        targeted = next(
            hint for hint in snapshot["root_cause_hints"] if hint["code"] == "lease_expired_during_relay_wait"
        )
        assert targeted["evidence"] == ["host:waiting_for_peer_join:initiator_waiting_for_peer"]
    finally:
        _cleanup(api, room)


def test_ct36_runner_lease_low_surfaces_before_first_relay(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        claimed = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-low-lease",
            status="active",
            execution_mode="managed_attached",
            phase="waiting_for_peer_join",
            phase_detail="initiator_waiting_for_peer",
            lease_seconds=5,
        )["room"]
        reasons = list((claimed.get("execution_attention") or {}).get("reasons") or [])
        assert "runner_lease_low" in reasons
        hint_codes = [str((hint or {}).get("code") or "") for hint in claimed.get("root_cause_hints") or []]
        assert "runner_lease_low" in hint_codes
        attempt = next(
            attempt for attempt in claimed["runner_attempts"]
            if attempt["participant"] == "host" and attempt["current"]
        )
        assert isinstance(attempt.get("phase_age_ms"), (int, float))
        assert attempt.get("lease_remaining_ms") is not None
        assert int(attempt["lease_remaining_ms"]) <= 10000
    finally:
        _cleanup(api, room)


def test_ct40_root_cause_hints_surface_runnerd_restart_exhausted_after_claim(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        host_claim = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-runnerd",
            status="active",
            execution_mode="managed_attached",
            phase="reply_generating",
            phase_detail="llm_waiting_on_local_runtime",
        )["room"]
        host_attempt_id = next(
            attempt["attempt_id"]
            for attempt in host_claim["runner_attempts"]
            if attempt["participant"] == "host" and attempt["current"]
        )
        api.runner_release(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-runnerd",
            attempt_id=host_attempt_id,
            status="abandoned",
            reason="runnerd_restart_exhausted_after_claim",
        )
        snapshot = api.room(room.room_id, host_token=room.host_token)["room"]
        hint_codes = [str((hint or {}).get("code") or "") for hint in snapshot.get("root_cause_hints") or []]
        assert "runnerd_restart_exhausted_after_claim" in hint_codes
        targeted = next(
            hint for hint in snapshot["root_cause_hints"] if hint["code"] == "runnerd_restart_exhausted_after_claim"
        )
        assert targeted["evidence"] == [
            "host:reply_generating:llm_waiting_on_local_runtime:runnerd_restart_exhausted_after_claim"
        ]
    finally:
        _cleanup(api, room)


def test_ct41_root_cause_hints_surface_live_session_lock(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        snapshot = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-lock",
            status="active",
            execution_mode="managed_attached",
            phase="reply_generating",
            phase_detail="relay",
            last_error="openclaw_session_file_locked",
            recovery_reason="session_lock_during_reply_generation",
        )["room"]
        hint_codes = [str((hint or {}).get("code") or "") for hint in snapshot.get("root_cause_hints") or []]
        assert "local_session_lock_during_reply_generation" in hint_codes
        targeted = next(
            hint for hint in snapshot["root_cause_hints"] if hint["code"] == "local_session_lock_during_reply_generation"
        )
        assert targeted["evidence"] == ["host:reply_generating:relay:session_lock_during_reply_generation"]
    finally:
        _cleanup(api, room)


def test_ct42_root_cause_hints_surface_live_gateway_timeout(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        snapshot = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-timeout",
            status="active",
            execution_mode="managed_attached",
            phase="reply_generating",
            phase_detail="relay",
            last_error="openclaw_gateway_timeout",
            recovery_reason="gateway_timeout_during_reply_generation",
        )["room"]
        hint_codes = [str((hint or {}).get("code") or "") for hint in snapshot.get("root_cause_hints") or []]
        assert "gateway_timeout_during_reply_generation" in hint_codes
        targeted = next(
            hint for hint in snapshot["root_cause_hints"] if hint["code"] == "gateway_timeout_during_reply_generation"
        )
        assert targeted["evidence"] == ["host:reply_generating:relay:gateway_timeout_during_reply_generation"]
    finally:
        _cleanup(api, room)


def test_ct37_participant_session_token_survives_repair_invite_reissue(api: Any) -> None:
    room = api.create_room()
    try:
        joined = api.join(room.room_id, room.invites["host"], "CT-Host")
        participant_token = str(joined.get("participant_token") or "")
        assert participant_token.startswith("ptok_")

        before = api.events(room.room_id, participant_token=participant_token, after=0)
        assert int(before["next_cursor"]) >= 0

        repair = api.repair_invite(room.room_id, room.host_token, "host")
        assert repair["invalidates_previous_invite"] is True

        old_invite = api.events_response(room.room_id, room.invites["host"], after=0)
        assert old_invite.status_code == 401

        session_batch = api.events(room.room_id, participant_token=participant_token, after=0)
        assert int(session_batch["next_cursor"]) >= 0
    finally:
        _cleanup(api, room)


def test_ct38_distinguishes_missing_managed_runner_from_runner_loss(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-only",
            status="active",
            execution_mode="managed_attached",
            phase="event_polling",
            phase_detail="poll_ready",
        )
        api.send(
            room.room_id,
            room.invites["guest"],
            _base_message(intent="ASK", text="guest can talk but never attached a managed runner", expect_reply=True),
        )
        snapshot = api.room(room.room_id, host_token=room.host_token)["room"]
        hint_codes = [str((hint or {}).get("code") or "") for hint in snapshot.get("root_cause_hints") or []]
        assert "single_sided_missing_managed_runner_after_first_relay" in hint_codes
        assert "single_sided_runner_loss_after_first_relay" not in hint_codes
        current_guest_actions = [
            action for action in snapshot.get("recovery_actions") or []
            if action.get("participant") == "guest" and action.get("current")
        ]
        assert current_guest_actions
        assert current_guest_actions[0]["reason"] == "no_managed_runner"
    finally:
        _cleanup(api, room)


def test_ct39_closed_room_retains_full_managed_coverage_for_terminal_classification(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        host_claim = api.runner_claim(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-terminal",
            status="active",
            execution_mode="managed_attached",
            managed_certified=True,
            recovery_policy="automatic",
        )
        host_attempt = str(host_claim["attempt_id"])
        guest_claim = api.runner_claim(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-terminal",
            status="active",
            execution_mode="managed_attached",
            managed_certified=True,
            recovery_policy="automatic",
        )
        guest_attempt = str(guest_claim["attempt_id"])

        api.runner_release(
            room.room_id,
            room.invites["host"],
            runner_id="runner-host-terminal",
            attempt_id=host_attempt,
            reason="normal_exit",
        )
        api.runner_release(
            room.room_id,
            room.invites["guest"],
            runner_id="runner-guest-terminal",
            attempt_id=guest_attempt,
            reason="normal_exit",
        )
        closed = api.close(room.room_id, room.host_token, reason="ct39_terminal_check")["room"]
        assert closed["status"] == "closed"
        assert closed["managed_coverage"] == "full"
        assert closed["product_owned"] is True
    finally:
        _cleanup(api, room)


def test_ct43_start_slo_tracks_full_join_latency(api: Any) -> None:
    room = api.create_room()
    try:
        _join_both(api, room)
        snapshot = api.room(room.room_id, host_token=room.host_token)["room"]
        start_slo = dict(snapshot.get("start_slo") or {})
        assert start_slo.get("room_created_at")
        assert start_slo.get("first_joined_at")
        assert start_slo.get("all_joined_at")
        assert start_slo.get("join_latency_ms") is not None
        assert start_slo.get("full_join_latency_ms") is not None
        assert int(start_slo["full_join_latency_ms"]) >= int(start_slo["join_latency_ms"])
        participants = {participant["name"]: participant for participant in snapshot.get("participants") or []}
        assert participants["host"]["joined_at"]
        assert participants["guest"]["joined_at"]
    finally:
        _cleanup(api, room)


def test_ct44_repeated_join_reuses_participant_session_token(api: Any) -> None:
    room = api.create_room()
    try:
        first = api.join(room.room_id, room.invites["host"], "CT-Host")
        token1 = str(first.get("participant_token") or "")
        assert token1.startswith("ptok_")

        second = api.join(room.room_id, room.invites["host"], "CT-Host-Again")
        token2 = str(second.get("participant_token") or "")
        assert token2 == token1

        events = api.events(room.room_id, participant_token=token1, after=0)
        assert int(events["next_cursor"]) >= 0
    finally:
        _cleanup(api, room)
