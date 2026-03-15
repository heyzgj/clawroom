from __future__ import annotations


def _create_room(client):
    resp = client.post(
        "/rooms",
        json={
            "topic": "Marketing sync",
            "goal": "Collect ICP and KPI",
            "participants": ["a_openclaw", "b_openclaw"],
            "required_fields": ["ICP", "primary_kpi"],
            "turn_limit": 12,
            "timeout_minutes": 30,
            "stall_limit": 2,
            "metadata": {},
        },
    )
    assert resp.status_code == 200
    return resp.json()


def test_create_join_message_result(client):
    created = _create_room(client)
    room = created["room"]
    room_id = room["id"]
    token_a = created["invites"]["a_openclaw"]
    token_b = created["invites"]["b_openclaw"]

    ja = client.post(
        f"/rooms/{room_id}/join",
        headers={"X-Invite-Token": token_a},
        json={"client_name": "A"},
    )
    jb = client.post(
        f"/rooms/{room_id}/join",
        headers={"X-Invite-Token": token_b},
        json={"client_name": "B"},
    )
    assert ja.status_code == 200
    assert jb.status_code == 200

    send_a = client.post(
        f"/rooms/{room_id}/messages",
        headers={"X-Invite-Token": token_a},
        json={
            "intent": "ASK",
            "text": "What is your ICP and primary KPI?",
            "fills": {},
            "facts": [],
            "questions": ["ICP?", "KPI?"],
            "expect_reply": True,
            "meta": {},
        },
    )
    assert send_a.status_code == 200
    assert send_a.json()["relay_recipients"] == ["b_openclaw"]

    events_b = client.get(
        f"/rooms/{room_id}/events?after=0&limit=200",
        headers={"X-Invite-Token": token_b},
    )
    assert events_b.status_code == 200
    relay_types = [e["type"] for e in events_b.json()["events"]]
    assert "relay" in relay_types

    send_b = client.post(
        f"/rooms/{room_id}/messages",
        headers={"X-Invite-Token": token_b},
        json={
            "intent": "ANSWER",
            "text": "Our ICP is AI founders, KPI is qualified demos.",
            "fills": {"ICP": "AI founders", "primary_kpi": "qualified demos"},
            "facts": ["B2B SaaS focus"],
            "questions": [],
            "expect_reply": False,
            "meta": {},
        },
    )
    assert send_b.status_code == 200

    room_after = client.get(f"/rooms/{room_id}", headers={"X-Invite-Token": token_a})
    assert room_after.status_code == 200
    assert room_after.json()["room"]["status"] == "closed"
    assert room_after.json()["room"]["stop_reason"] == "goal_done"

    result = client.get(f"/rooms/{room_id}/result", headers={"X-Invite-Token": token_a})
    assert result.status_code == 200
    assert result.json()["result"]["required_filled"] == 2


def test_owner_loop_no_pause(client):
    created = _create_room(client)
    room_id = created["room"]["id"]
    token_a = created["invites"]["a_openclaw"]
    token_b = created["invites"]["b_openclaw"]

    client.post(f"/rooms/{room_id}/join", headers={"X-Invite-Token": token_a}, json={"client_name": "A"})
    client.post(f"/rooms/{room_id}/join", headers={"X-Invite-Token": token_b}, json={"client_name": "B"})

    ask_owner = client.post(
        f"/rooms/{room_id}/messages",
        headers={"X-Invite-Token": token_a},
        json={
            "intent": "ASK_OWNER",
            "text": "Need owner decision on KPI target",
            "fills": {},
            "facts": [],
            "questions": ["KPI target?"],
            "expect_reply": False,
            "meta": {},
        },
    )
    assert ask_owner.status_code == 200
    assert ask_owner.json()["room"]["status"] == "active"

    events_b = client.get(
        f"/rooms/{room_id}/events?after=0&limit=200",
        headers={"X-Invite-Token": token_b},
    )
    assert events_b.status_code == 200
    types = [e["type"] for e in events_b.json()["events"]]
    assert "msg" in types
    assert "relay" not in types
    assert "owner_wait" in types

    owner_reply = client.post(
        f"/rooms/{room_id}/messages",
        headers={"X-Invite-Token": token_a},
        json={
            "intent": "OWNER_REPLY",
            "text": "Owner confirms KPI is weekly SQL growth",
            "fills": {"primary_kpi": "weekly SQL growth", "ICP": "AI founders"},
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {},
        },
    )
    assert owner_reply.status_code == 200

    room_after = client.get(f"/rooms/{room_id}", headers={"X-Invite-Token": token_b})
    assert room_after.status_code == 200
    assert room_after.json()["room"]["status"] == "closed"
    assert room_after.json()["room"]["stop_reason"] == "goal_done"


def test_stall_rule(client):
    created = client.post(
        "/rooms",
        json={
            "topic": "Stall test",
            "goal": "No loop",
            "participants": ["a", "b"],
            "required_fields": [],
            "turn_limit": 12,
            "timeout_minutes": 30,
            "stall_limit": 1,
            "metadata": {},
        },
    ).json()
    room_id = created["room"]["id"]
    token_a = created["invites"]["a"]
    token_b = created["invites"]["b"]

    client.post(f"/rooms/{room_id}/join", headers={"X-Invite-Token": token_a}, json={"client_name": "A"})
    client.post(f"/rooms/{room_id}/join", headers={"X-Invite-Token": token_b}, json={"client_name": "B"})

    r1 = client.post(
        f"/rooms/{room_id}/messages",
        headers={"X-Invite-Token": token_a},
        json={
            "intent": "NOTE",
            "text": "same text",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": True,
            "meta": {},
        },
    )
    assert r1.status_code == 200

    r2 = client.post(
        f"/rooms/{room_id}/messages",
        headers={"X-Invite-Token": token_b},
        json={
            "intent": "NOTE",
            "text": "same text",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": True,
            "meta": {},
        },
    )
    assert r2.status_code == 200

    room_after = client.get(f"/rooms/{room_id}", headers={"X-Invite-Token": token_a})
    assert room_after.status_code == 200
    assert room_after.json()["room"]["status"] == "closed"
    assert room_after.json()["room"]["stop_reason"] == "stall"


def test_guest_first_multi_turn_relay_continues(client):
    created_resp = client.post(
        "/rooms",
        json={
            "topic": "Guest-first loop",
            "goal": "verify host can reply after guest kickoff",
            "participants": ["host", "guest"],
            "required_fields": [],
            "turn_limit": 12,
            "timeout_minutes": 30,
            "stall_limit": 6,
            "metadata": {"source": "pytest_multi_turn"},
        },
    )
    assert created_resp.status_code == 200
    created = created_resp.json()

    room_id = created["room"]["id"]
    host_token = created["invites"]["host"]
    guest_token = created["invites"]["guest"]

    host_join = client.post(
        f"/rooms/{room_id}/join",
        headers={"X-Invite-Token": host_token},
        json={"client_name": "host-bot"},
    )
    guest_join = client.post(
        f"/rooms/{room_id}/join",
        headers={"X-Invite-Token": guest_token},
        json={"client_name": "guest-bot"},
    )
    assert host_join.status_code == 200
    assert guest_join.status_code == 200

    kickoff = client.post(
        f"/rooms/{room_id}/messages",
        headers={"X-Invite-Token": guest_token},
        json={
            "intent": "ASK",
            "text": "Guest kickoff: can you suggest plan A and plan B?",
            "fills": {},
            "facts": [],
            "questions": ["plan A?", "plan B?"],
            "expect_reply": True,
            "meta": {"source": "pytest_multi_turn", "kickoff": "guest"},
        },
    )
    assert kickoff.status_code == 200
    assert kickoff.json()["relay_recipients"] == ["host"]

    host_events_1 = client.get(
        f"/rooms/{room_id}/events?after=0&limit=200",
        headers={"X-Invite-Token": host_token},
    )
    assert host_events_1.status_code == 200
    host_events_1_body = host_events_1.json()
    host_cursor = int(host_events_1_body["next_cursor"])
    host_relays_1 = [e for e in host_events_1_body["events"] if e["type"] == "relay"]
    assert any((e["payload"] or {}).get("from") == "guest" for e in host_relays_1)

    host_reply_1 = client.post(
        f"/rooms/{room_id}/messages",
        headers={"X-Invite-Token": host_token},
        json={
            "intent": "ANSWER",
            "text": "Host reply #1: here's plan A and plan B. Which one do you prefer?",
            "fills": {},
            "facts": ["plan A is faster", "plan B is cheaper"],
            "questions": ["which option do you prefer?"],
            "expect_reply": True,
            "meta": {"source": "pytest_multi_turn"},
        },
    )
    assert host_reply_1.status_code == 200
    assert host_reply_1.json()["relay_recipients"] == ["guest"]

    guest_events = client.get(
        f"/rooms/{room_id}/events?after=0&limit=200",
        headers={"X-Invite-Token": guest_token},
    )
    assert guest_events.status_code == 200
    guest_relays = [e for e in guest_events.json()["events"] if e["type"] == "relay"]
    assert any((e["payload"] or {}).get("from") == "host" for e in guest_relays)

    guest_reply_2 = client.post(
        f"/rooms/{room_id}/messages",
        headers={"X-Invite-Token": guest_token},
        json={
            "intent": "ANSWER",
            "text": "Guest reply #2: I prefer plan B. Can you refine the timeline?",
            "fills": {},
            "facts": ["prefer lower cost"],
            "questions": ["timeline details?"],
            "expect_reply": True,
            "meta": {"source": "pytest_multi_turn"},
        },
    )
    assert guest_reply_2.status_code == 200
    assert guest_reply_2.json()["relay_recipients"] == ["host"]

    host_events_2 = client.get(
        f"/rooms/{room_id}/events?after={host_cursor}&limit=200",
        headers={"X-Invite-Token": host_token},
    )
    assert host_events_2.status_code == 200
    host_relays_2 = [e for e in host_events_2.json()["events"] if e["type"] == "relay"]
    assert any((e["payload"] or {}).get("from") == "guest" for e in host_relays_2)

    room_state = client.get(f"/rooms/{room_id}", headers={"X-Invite-Token": host_token})
    assert room_state.status_code == 200
    assert room_state.json()["room"]["status"] == "active"
    assert room_state.json()["room"]["turn_count"] >= 3
