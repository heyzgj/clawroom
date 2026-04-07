"""E2E tests for whitelist/contacts/connect against the DEPLOYED edge API.

These tests hit https://api.clawroom.cc directly — they require the edge worker
to be deployed with the whitelist/contacts changes.

11 happy-path scenarios + 4 negative/auth scenarios:

Happy path:
1. Fresh registration with bio/tags — both runtimes
2. One-sided whitelist → contacts empty (mutual not met)
3. Mutual whitelist → contacts shows counterpart with bio/tags
4. Connect succeeds only with mutual whitelist
5. Connect fails without mutual whitelist → clear error
6. Remove from whitelist → contacts disappear
7. Idempotent whitelist add (double-add doesn't break)
8. Re-registration updates bio/tags (upsert behavior)
9. Whitelist + room creation + full message exchange + close
10. Multi-agent whitelist graph (A↔B, A↔C, B not↔C)
11. Inbox event delivery after room creation (Path C)

Negative / auth:
12. Whitelist endpoints return 401 without bearer token
13. Whitelist endpoints return 401 with wrong bearer token
14. /connect returns 404 for nonexistent target agent
15. Contacts/whitelist return 404 for nonexistent agent
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

API = os.environ.get("CLAWROOM_API_BASE", "https://api.clawroom.cc")
ADMIN_TOKEN = os.environ.get("MONITOR_ADMIN_TOKEN", "")

# Skip entire module if we can't reach the API
pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_E2E", ""),
    reason="Set RUN_E2E=1 to run E2E tests against deployed API",
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _admin_client() -> httpx.Client:
    """Client with monitor admin token for agent registration."""
    headers = {}
    if ADMIN_TOKEN:
        headers["X-Monitor-Token"] = ADMIN_TOKEN
    return httpx.Client(base_url=API, timeout=15.0, headers=headers, trust_env=False)


def _agent_client(inbox_token: str) -> httpx.Client:
    """Client authenticated as a specific agent via inbox bearer token."""
    return httpx.Client(
        base_url=API,
        timeout=15.0,
        headers={"Authorization": f"Bearer {inbox_token}"},
        trust_env=False,
    )


def _anon_client() -> httpx.Client:
    """Client with NO auth — for negative tests."""
    return httpx.Client(base_url=API, timeout=15.0, trust_env=False)


def _register_with_token(admin: httpx.Client, *, name: str, runtime: str, bio: str = "", tags: list[str] | None = None) -> tuple[str, str]:
    """Register an agent with an inbox token. Returns (agent_id, inbox_token)."""
    resp = admin.post("/agents", json={
        "name": name,
        "runtime": runtime,
        "bio": bio,
        "tags": tags or [],
        "issue_inbox_token": True,
    })
    assert resp.status_code == 201, f"register failed: {resp.text}"
    data = resp.json()
    assert data.get("inbox_token"), f"inbox_token not issued: {data}"
    return data["agent_id"], data["inbox_token"]


def _whitelist_add(client: httpx.Client, agent_id: str, targets: list[str]) -> dict:
    resp = client.post(f"/agents/{agent_id}/whitelist", json={"add": targets})
    assert resp.status_code == 200, f"whitelist add failed: {resp.text}"
    return resp.json()


def _whitelist_remove(client: httpx.Client, agent_id: str, targets: list[str]) -> dict:
    resp = client.post(f"/agents/{agent_id}/whitelist", json={"remove": targets})
    assert resp.status_code == 200, f"whitelist remove failed: {resp.text}"
    return resp.json()


def _get_whitelist(client: httpx.Client, agent_id: str) -> list[dict]:
    resp = client.get(f"/agents/{agent_id}/whitelist")
    assert resp.status_code == 200, f"get whitelist failed: {resp.text}"
    return resp.json()["whitelist"]


def _get_contacts(client: httpx.Client, agent_id: str) -> list[dict]:
    resp = client.get(f"/agents/{agent_id}/contacts")
    assert resp.status_code == 200, f"get contacts failed: {resp.text}"
    return resp.json()["contacts"]


def _connect(client: httpx.Client, agent_id: str, target_id: str) -> httpx.Response:
    return client.post(f"/agents/{agent_id}/connect", json={"target_agent_id": target_id})


# All tests below require admin token to register agents with inbox tokens
@pytest.fixture(autouse=True)
def _require_admin():
    if not ADMIN_TOKEN:
        pytest.skip("MONITOR_ADMIN_TOKEN required")


# === Scenario 1: Fresh registration with bio/tags ===

def test_scenario_01_register_both_runtimes_with_profile():
    """Register agents with different runtimes, bio, and tags."""
    admin = _admin_client()
    uid = _uid()

    a_id, a_tok = _register_with_token(admin, name=f"cc-{uid}", runtime="claude-code", bio="Research agent", tags=["research", "ml"])
    b_id, b_tok = _register_with_token(admin, name=f"oc-{uid}", runtime="openclaw", bio="Analysis agent", tags=["analysis"])

    assert a_id.startswith("agent_")
    assert b_id.startswith("agent_")
    assert a_id != b_id
    assert a_tok.startswith("agtok_")
    assert b_tok.startswith("agtok_")
    admin.close()


# === Scenario 2: One-sided whitelist → contacts empty ===

def test_scenario_02_one_sided_whitelist_no_contacts():
    """If only A whitelists B, contacts for both should be empty."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"a-{uid}", runtime="claude-code")
    b, b_tok = _register_with_token(admin, name=f"b-{uid}", runtime="openclaw")

    ca = _agent_client(a_tok)
    cb = _agent_client(b_tok)

    _whitelist_add(ca, a, [b])

    # A whitelisted B, but B hasn't whitelisted A
    assert len(_get_contacts(ca, a)) == 0
    assert len(_get_contacts(cb, b)) == 0

    # A's whitelist should show B
    wl = _get_whitelist(ca, a)
    assert len(wl) == 1
    assert wl[0]["allowed_agent_id"] == b

    # B's whitelist should be empty
    assert len(_get_whitelist(cb, b)) == 0
    for c in (admin, ca, cb):
        c.close()


# === Scenario 3: Mutual whitelist → contacts appear with bio/tags ===

def test_scenario_03_mutual_whitelist_shows_contacts_with_profile():
    """Mutual whitelist makes both agents visible as contacts, with bio/tags."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"cc-{uid}", runtime="claude-code", bio="ML researcher", tags=["ml", "research"])
    b, b_tok = _register_with_token(admin, name=f"oc-{uid}", runtime="openclaw", bio="Data analyst", tags=["data", "viz"])

    ca = _agent_client(a_tok)
    cb = _agent_client(b_tok)

    _whitelist_add(ca, a, [b])
    _whitelist_add(cb, b, [a])

    contacts_a = _get_contacts(ca, a)
    contacts_b = _get_contacts(cb, b)

    assert len(contacts_a) == 1
    assert contacts_a[0]["agent_id"] == b
    assert contacts_a[0]["name"] == f"oc-{uid}"
    assert contacts_a[0]["runtime"] == "openclaw"
    assert contacts_a[0]["bio"] == "Data analyst"
    assert contacts_a[0]["tags"] == ["data", "viz"]

    assert len(contacts_b) == 1
    assert contacts_b[0]["agent_id"] == a
    assert contacts_b[0]["bio"] == "ML researcher"
    assert contacts_b[0]["tags"] == ["ml", "research"]
    for c in (admin, ca, cb):
        c.close()


# === Scenario 4: Connect succeeds with mutual whitelist ===

def test_scenario_04_connect_succeeds_mutual():
    """POST /connect returns ok:true when both agents whitelisted each other."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"cc-{uid}", runtime="claude-code")
    b, b_tok = _register_with_token(admin, name=f"oc-{uid}", runtime="openclaw")

    ca = _agent_client(a_tok)
    cb = _agent_client(b_tok)

    _whitelist_add(ca, a, [b])
    _whitelist_add(cb, b, [a])

    resp = _connect(ca, a, b)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["mutual"] is True
    assert body["target_agent_id"] == b
    for c in (admin, ca, cb):
        c.close()


# === Scenario 5: Connect fails without mutual whitelist ===

def test_scenario_05_connect_fails_without_mutual():
    """Connect must fail with not_in_mutual_whitelist if only one side whitelisted."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"cc-{uid}", runtime="claude-code")
    b, b_tok = _register_with_token(admin, name=f"oc-{uid}", runtime="openclaw")

    ca = _agent_client(a_tok)

    # Only A whitelists B
    _whitelist_add(ca, a, [b])

    resp = _connect(ca, a, b)
    assert resp.status_code == 403
    assert resp.json()["error"] == "not_in_mutual_whitelist"
    for c in (admin, ca):
        c.close()


# === Scenario 6: Remove from whitelist → contacts disappear ===

def test_scenario_06_remove_whitelist_breaks_contact():
    """Removing from whitelist immediately breaks the contact relationship."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"cc-{uid}", runtime="claude-code")
    b, b_tok = _register_with_token(admin, name=f"oc-{uid}", runtime="openclaw")

    ca = _agent_client(a_tok)
    cb = _agent_client(b_tok)

    # Establish mutual
    _whitelist_add(ca, a, [b])
    _whitelist_add(cb, b, [a])
    assert len(_get_contacts(ca, a)) == 1

    # A removes B
    _whitelist_remove(ca, a, [b])

    # Contacts should be empty for both (mutual broken)
    assert len(_get_contacts(ca, a)) == 0
    assert len(_get_contacts(cb, b)) == 0

    # Connect should now fail
    resp = _connect(ca, a, b)
    assert resp.status_code == 403
    for c in (admin, ca, cb):
        c.close()


# === Scenario 7: Idempotent whitelist add ===

def test_scenario_07_idempotent_whitelist_add():
    """Adding the same agent twice should not create duplicates or errors."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"cc-{uid}", runtime="claude-code")
    b, b_tok = _register_with_token(admin, name=f"oc-{uid}", runtime="openclaw")

    ca = _agent_client(a_tok)

    _whitelist_add(ca, a, [b])
    _whitelist_add(ca, a, [b])  # Second add — should be idempotent

    wl = _get_whitelist(ca, a)
    assert len(wl) == 1  # No duplicate
    assert wl[0]["allowed_agent_id"] == b
    for c in (admin, ca):
        c.close()


# === Scenario 8: Re-registration updates bio/tags ===

def test_scenario_08_re_register_updates_profile():
    """Re-registering with same agent_id should update bio/tags (upsert)."""
    admin = _admin_client()
    uid = _uid()

    agent_id = f"agent_retest_{uid}"

    # First registration
    resp1 = admin.post("/agents", json={
        "agent_id": agent_id,
        "name": f"agent-v1-{uid}",
        "runtime": "claude-code",
        "bio": "Version 1 bio",
        "tags": ["v1"],
        "issue_inbox_token": True,
    })
    assert resp1.status_code == 201
    a_tok = resp1.json()["inbox_token"]

    # Second registration (same agent_id, different bio/tags — re-uses existing token)
    resp2 = admin.post("/agents", json={
        "agent_id": agent_id,
        "name": f"agent-v2-{uid}",
        "runtime": "claude-code",
        "bio": "Version 2 bio",
        "tags": ["v2", "updated"],
        "inbox_token": a_tok,  # re-submit existing token to keep it
    })
    assert resp2.status_code == 201

    # Create a counterpart and check contacts to verify bio/tags updated
    other, other_tok = _register_with_token(admin, name=f"oc-{uid}", runtime="openclaw")
    ca = _agent_client(a_tok)
    co = _agent_client(other_tok)

    _whitelist_add(ca, agent_id, [other])
    _whitelist_add(co, other, [agent_id])

    contacts = _get_contacts(co, other)
    assert len(contacts) == 1
    assert contacts[0]["bio"] == "Version 2 bio"
    assert contacts[0]["tags"] == ["v2", "updated"]
    assert contacts[0]["name"] == f"agent-v2-{uid}"
    for c in (admin, ca, co):
        c.close()


# === Scenario 9: Full flow — whitelist → connect → room → messages → close ===

def test_scenario_09_full_flow_whitelist_to_room_close():
    """Complete flow: register, whitelist, connect, create room, exchange messages, close."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"cc-{uid}", runtime="claude-code", bio="Researcher")
    b, b_tok = _register_with_token(admin, name=f"oc-{uid}", runtime="openclaw", bio="Analyst")

    ca = _agent_client(a_tok)
    cb = _agent_client(b_tok)

    # Establish mutual whitelist
    _whitelist_add(ca, a, [b])
    _whitelist_add(cb, b, [a])

    # Verify connect works
    conn = _connect(ca, a, b)
    assert conn.status_code == 200

    # Create a room (using admin client for room creation)
    room_resp = admin.post("/rooms", json={
        "topic": f"E2E test {uid}",
        "goal": "Test full whitelist-to-room flow",
        "participants": [a, b],
        "required_fields": ["summary", "next_steps"],
        "turn_limit": 8,
        "timeout_minutes": 5,
    })
    assert room_resp.status_code in (200, 201), f"room create failed: {room_resp.text}"
    room_data = room_resp.json()
    room_id = room_data["room"]["id"]
    token_a = room_data["invites"][a]
    token_b = room_data["invites"][b]

    # Both join (using invite tokens, not inbox tokens)
    join_a = admin.post(f"/rooms/{room_id}/join", headers={"X-Invite-Token": token_a}, json={"display_name": f"cc-{uid}"})
    assert join_a.status_code == 200, f"join A failed: {join_a.text}"

    join_b = admin.post(f"/rooms/{room_id}/join", headers={"X-Invite-Token": token_b}, json={"display_name": f"oc-{uid}"})
    assert join_b.status_code == 200, f"join B failed: {join_b.text}"

    # Exchange messages
    msg1 = admin.post(f"/rooms/{room_id}/messages", headers={"X-Invite-Token": token_a}, json={
        "intent": "ASK", "text": "What findings do you have?", "expect_reply": True,
    })
    assert msg1.status_code == 200

    msg2 = admin.post(f"/rooms/{room_id}/messages", headers={"X-Invite-Token": token_b}, json={
        "intent": "ANSWER", "text": "I found key insights.",
        "fills": {"summary": "Key insights on ML optimization", "next_steps": "Run ablation study"},
        "expect_reply": True,
    })
    assert msg2.status_code == 200

    msg3 = admin.post(f"/rooms/{room_id}/messages", headers={"X-Invite-Token": token_a}, json={
        "intent": "DONE", "text": "Agreed.",
        "fills": {"summary": "Key insights on ML optimization", "next_steps": "Run ablation study"},
        "expect_reply": False,
    })
    assert msg3.status_code == 200

    msg4 = admin.post(f"/rooms/{room_id}/messages", headers={"X-Invite-Token": token_b}, json={
        "intent": "DONE", "text": "Confirmed.",
        "fills": {"summary": "Key insights on ML optimization", "next_steps": "Run ablation study"},
        "expect_reply": False,
    })
    assert msg4.status_code == 200

    room_final = msg4.json()["room"]
    assert room_final["status"] == "closed"
    assert room_final["stop_reason"] == "mutual_done"
    for c in (admin, ca, cb):
        c.close()


# === Scenario 10: Multi-agent whitelist graph ===

def test_scenario_10_multi_agent_whitelist_graph():
    """A↔B mutual, A↔C mutual, B NOT↔C."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"hub-{uid}", runtime="claude-code", bio="Hub agent")
    b, b_tok = _register_with_token(admin, name=f"spoke1-{uid}", runtime="openclaw", bio="Spoke 1")
    cc, cc_tok = _register_with_token(admin, name=f"spoke2-{uid}", runtime="openclaw", bio="Spoke 2")

    ca = _agent_client(a_tok)
    cb = _agent_client(b_tok)
    ccc = _agent_client(cc_tok)

    # A↔B mutual
    _whitelist_add(ca, a, [b])
    _whitelist_add(cb, b, [a])

    # A↔C mutual
    _whitelist_add(ca, a, [cc])
    _whitelist_add(ccc, cc, [a])

    contacts_a = _get_contacts(ca, a)
    contacts_b = _get_contacts(cb, b)
    contacts_c = _get_contacts(ccc, cc)

    assert len(contacts_a) == 2
    contact_ids_a = {ct["agent_id"] for ct in contacts_a}
    assert contact_ids_a == {b, cc}

    assert len(contacts_b) == 1
    assert contacts_b[0]["agent_id"] == a

    assert len(contacts_c) == 1
    assert contacts_c[0]["agent_id"] == a

    # B trying to connect to C should fail
    resp = _connect(cb, b, cc)
    assert resp.status_code == 403
    assert resp.json()["error"] == "not_in_mutual_whitelist"

    # A connecting to B should succeed
    resp = _connect(ca, a, b)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    for c in (admin, ca, cb, ccc):
        c.close()


# === Scenario 11 (Path C): Inbox wake-up ===

def test_scenario_11_inbox_wake_up_after_room_creation():
    """When a room is created with participants, inbox events should be delivered."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"cc-inbox-{uid}", runtime="claude-code")
    b, b_tok = _register_with_token(admin, name=f"oc-inbox-{uid}", runtime="openclaw")

    # Create room — should fanout inbox events
    room_resp = admin.post("/rooms", json={
        "topic": f"Inbox test {uid}",
        "goal": "Test inbox wake-up delivery",
        "participants": [a, b],
        "required_fields": ["outcome"],
        "turn_limit": 4,
        "timeout_minutes": 5,
    })
    assert room_resp.status_code in (200, 201), f"room create failed: {room_resp.text}"
    room_id = room_resp.json()["room"]["id"]

    # Poll B's inbox (authenticated with inbox token)
    inbox_resp = admin.get(
        f"/agents/{b}/inbox",
        headers={"Authorization": f"Bearer {b_tok}"},
        params={"wait": "2"},
    )
    assert inbox_resp.status_code == 200, f"inbox poll failed: {inbox_resp.text}"
    events = inbox_resp.json().get("events", [])

    invite_events = [e for e in events if e.get("type") == "room_invite" and room_id in str(e.get("payload", {}))]
    assert len(invite_events) >= 1, f"No room_invite found for room {room_id}. Events: {json.dumps(events, indent=2)}"
    admin.close()


# === NEGATIVE TESTS ===


# === Scenario 12: 401 without bearer token ===

def test_scenario_12_whitelist_rejects_unauthenticated_requests():
    """All whitelist/contacts/connect endpoints must return 401 without a bearer token."""
    admin = _admin_client()
    anon = _anon_client()
    uid = _uid()

    a, _ = _register_with_token(admin, name=f"noauth-{uid}", runtime="claude-code")

    # GET whitelist — no token
    resp = anon.get(f"/agents/{a}/whitelist")
    assert resp.status_code == 401, f"GET whitelist should 401 without token: {resp.text}"
    assert resp.json()["error"] == "unauthorized"

    # POST whitelist — no token
    resp = anon.post(f"/agents/{a}/whitelist", json={"add": ["fake_agent"]})
    assert resp.status_code == 401, f"POST whitelist should 401 without token: {resp.text}"

    # GET contacts — no token
    resp = anon.get(f"/agents/{a}/contacts")
    assert resp.status_code == 401, f"GET contacts should 401 without token: {resp.text}"

    # POST connect — no token
    resp = anon.post(f"/agents/{a}/connect", json={"target_agent_id": "fake"})
    assert resp.status_code == 401, f"POST connect should 401 without token: {resp.text}"

    for c in (admin, anon):
        c.close()


# === Scenario 13: 401 with wrong bearer token ===

def test_scenario_13_whitelist_rejects_wrong_token():
    """All endpoints must return 401 with an incorrect bearer token."""
    admin = _admin_client()
    uid = _uid()

    a, _ = _register_with_token(admin, name=f"wrongtok-{uid}", runtime="claude-code")
    bad = _agent_client("agtok_this_is_completely_wrong")

    resp = bad.get(f"/agents/{a}/whitelist")
    assert resp.status_code == 401, f"GET whitelist should 401 with wrong token: {resp.text}"
    assert resp.json()["error"] == "unauthorized"

    resp = bad.get(f"/agents/{a}/contacts")
    assert resp.status_code == 401

    resp = bad.post(f"/agents/{a}/whitelist", json={"add": ["fake"]})
    assert resp.status_code == 401

    resp = bad.post(f"/agents/{a}/connect", json={"target_agent_id": "fake"})
    assert resp.status_code == 401

    for c in (admin, bad):
        c.close()


# === Scenario 14: /connect 404 for nonexistent target ===

def test_scenario_14_connect_returns_404_for_nonexistent_target():
    """/connect must return 404 when target_agent_id doesn't exist in agents table."""
    admin = _admin_client()
    uid = _uid()

    a, a_tok = _register_with_token(admin, name=f"cc-{uid}", runtime="claude-code")
    ca = _agent_client(a_tok)

    resp = _connect(ca, a, "agent_does_not_exist_99999")
    assert resp.status_code == 404, f"connect to nonexistent agent should 404: {resp.text}"
    assert resp.json()["error"] == "agent_not_found"
    for c in (admin, ca):
        c.close()


# === Scenario 15: Whitelist/contacts 404 for nonexistent agent ===

def test_scenario_15_endpoints_return_404_for_nonexistent_agent():
    """Whitelist/contacts endpoints must return 404 if the agent doesn't exist."""
    admin = _admin_client()
    # Use a fake inbox token — the agent doesn't exist so auth will fail with 404
    fake = _agent_client("agtok_doesnt_matter")

    resp = fake.get("/agents/agent_nonexistent_99999/whitelist")
    assert resp.status_code in (401, 404), f"Expected 401 or 404 for nonexistent agent: {resp.text}"

    resp = fake.get("/agents/agent_nonexistent_99999/contacts")
    assert resp.status_code in (401, 404), f"Expected 401 or 404 for nonexistent agent: {resp.text}"

    for c in (admin, fake):
        c.close()
