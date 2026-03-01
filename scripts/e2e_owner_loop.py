from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx


BASE = "http://127.0.0.1:8080"
REPORT_DIR = Path("reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def req(method: str, path: str, token: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {}
    if token:
        headers["X-Invite-Token"] = token
    with httpx.Client(timeout=20.0) as client:
        resp = client.request(method, f"{BASE}{path}", headers=headers, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"{method} {path} failed status={resp.status_code} body={resp.text}")
    return resp.json()


def main() -> None:
    log: list[dict[str, Any]] = []

    created = req(
        "POST",
        "/rooms",
        payload={
            "topic": "Owner loop E2E",
            "goal": "resolve owner escalation",
            "participants": ["a", "b"],
            "required_fields": ["decision"],
            "turn_limit": 12,
            "timeout_minutes": 20,
            "stall_limit": 2,
            "metadata": {"source": "e2e_owner_loop"},
        },
    )
    log.append({"step": "create", "data": created})

    room_id = created["room"]["id"]
    ta = created["invites"]["a"]
    tb = created["invites"]["b"]

    req("POST", f"/rooms/{room_id}/join", token=ta, payload={"client_name": "owner-a"})
    req("POST", f"/rooms/{room_id}/join", token=tb, payload={"client_name": "owner-b"})

    ask_owner = req(
        "POST",
        f"/rooms/{room_id}/messages",
        token=ta,
        payload={
            "intent": "ASK_OWNER",
            "text": "Need owner decision before continue",
            "fills": {},
            "facts": [],
            "questions": ["Approve budget?"],
            "expect_reply": False,
            "meta": {"source": "e2e_owner_loop"},
        },
    )
    log.append({"step": "ask_owner", "data": ask_owner})

    events_b = req("GET", f"/rooms/{room_id}/events?after=0&limit=200", token=tb)
    log.append({"step": "events_b", "data": events_b})
    event_types = [x["type"] for x in events_b.get("events", [])]
    assert "owner_wait" in event_types
    assert "relay" not in event_types

    owner_reply = req(
        "POST",
        f"/rooms/{room_id}/messages",
        token=ta,
        payload={
            "intent": "OWNER_REPLY",
            "text": "Owner approves budget and go-live",
            "fills": {"decision": "approved"},
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {"source": "e2e_owner_loop"},
        },
    )
    log.append({"step": "owner_reply", "data": owner_reply})

    result = req("GET", f"/rooms/{room_id}/result", token=ta)
    log.append({"step": "result", "data": result})

    assert result["result"]["status"] == "closed"
    assert result["result"]["stop_reason"] == "goal_done"

    (REPORT_DIR / "e2e_owner.log").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print("e2e_owner_loop passed", room_id)


if __name__ == "__main__":
    main()
