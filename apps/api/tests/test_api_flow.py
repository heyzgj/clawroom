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
