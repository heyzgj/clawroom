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
            "topic": "Mock E2E",
            "goal": "Fill ICP and KPI",
            "participants": ["a", "b"],
            "required_fields": ["ICP", "primary_kpi"],
            "turn_limit": 12,
            "timeout_minutes": 20,
            "stall_limit": 2,
            "metadata": {"source": "e2e_mock"},
        },
    )
    log.append({"step": "create", "data": created})

    room_id = created["room"]["id"]
    ta = created["invites"]["a"]
    tb = created["invites"]["b"]

    ja = req("POST", f"/rooms/{room_id}/join", token=ta, payload={"client_name": "mock-a"})
    jb = req("POST", f"/rooms/{room_id}/join", token=tb, payload={"client_name": "mock-b"})
    log.append({"step": "join_a", "data": ja})
    log.append({"step": "join_b", "data": jb})

    ask = req(
        "POST",
        f"/rooms/{room_id}/messages",
        token=ta,
        payload={
            "intent": "ASK",
            "text": "Need your ICP and primary KPI",
            "fills": {},
            "facts": [],
            "questions": ["ICP?", "KPI?"],
            "expect_reply": True,
            "meta": {"script": "e2e_mock"},
        },
    )
    log.append({"step": "ask", "data": ask})

    events_b = req("GET", f"/rooms/{room_id}/events?after=0&limit=200", token=tb)
    log.append({"step": "events_b", "data": events_b})

    answer = req(
        "POST",
        f"/rooms/{room_id}/messages",
        token=tb,
        payload={
            "intent": "ANSWER",
            "text": "ICP is AI founders and KPI is weekly SQL growth",
            "fills": {"ICP": "AI founders", "primary_kpi": "weekly SQL growth"},
            "facts": ["seed to series A"],
            "questions": [],
            "expect_reply": False,
            "meta": {"script": "e2e_mock"},
        },
    )
    log.append({"step": "answer", "data": answer})

    result = req("GET", f"/rooms/{room_id}/result", token=ta)
    log.append({"step": "result", "data": result})

    assert result["result"]["status"] == "closed"
    assert result["result"]["stop_reason"] == "goal_done"
    assert result["result"]["required_filled"] == 2

    (REPORT_DIR / "e2e_mock.log").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "e2e_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("e2e_mock passed", room_id)


if __name__ == "__main__":
    main()
