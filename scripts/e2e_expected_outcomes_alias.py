from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx


BASE = os.getenv("CLAWROOM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
REPORT_DIR = Path("reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def req(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    expect_status: int = 200,
) -> dict[str, Any]:
    with httpx.Client(timeout=20.0, trust_env=False) as client:
        resp = client.request(method, f"{BASE}{path}", json=payload)
    if resp.status_code != expect_status:
        raise RuntimeError(
            f"{method} {path} failed status={resp.status_code} expect={expect_status} body={resp.text}"
        )
    return resp.json()


def main() -> None:
    log: list[dict[str, Any]] = []

    # Case 1: expected_outcomes only (alias path)
    created = req(
        "POST",
        "/rooms",
        payload={
            "topic": "Alias check",
            "goal": "Verify expected_outcomes alias",
            "participants": ["a", "b"],
            "expected_outcomes": ["ICP", "primary_kpi"],
            "turn_limit": 12,
            "timeout_minutes": 20,
            "stall_limit": 2,
            "metadata": {"source": "e2e_expected_outcomes_alias"},
        },
    )
    log.append({"step": "create_expected_outcomes_only", "data": created})
    room = created["room"]
    assert room["required_fields"] == ["ICP", "primary_kpi"]
    assert room["expected_outcomes"] == ["ICP", "primary_kpi"]
    room_id = room["id"]
    token_a = created["invites"]["a"]
    token_b = created["invites"]["b"]

    # Fill outcomes and verify result summary fields
    with httpx.Client(timeout=20.0, trust_env=False) as client:
        ja = client.post(
            f"{BASE}/rooms/{room_id}/join",
            headers={"X-Invite-Token": token_a},
            json={"client_name": "alias-a"},
        )
        jb = client.post(
            f"{BASE}/rooms/{room_id}/join",
            headers={"X-Invite-Token": token_b},
            json={"client_name": "alias-b"},
        )
        if ja.status_code != 200 or jb.status_code != 200:
            raise RuntimeError(f"join failed ja={ja.status_code} jb={jb.status_code}")

        msg = client.post(
            f"{BASE}/rooms/{room_id}/messages",
            headers={"X-Invite-Token": token_a},
            json={
                "intent": "ANSWER",
                "text": "ICP is AI founders and KPI is weekly SQL growth",
                "fills": {"ICP": "AI founders", "primary_kpi": "weekly SQL growth"},
                "facts": [],
                "questions": [],
                "expect_reply": False,
                "meta": {"source": "e2e_expected_outcomes_alias"},
            },
        )
        if msg.status_code != 200:
            raise RuntimeError(f"message failed status={msg.status_code} body={msg.text}")

        result_resp = client.get(
            f"{BASE}/rooms/{room_id}/result",
            headers={"X-Invite-Token": token_a},
        )
        if result_resp.status_code != 200:
            raise RuntimeError(
                f"result failed status={result_resp.status_code} body={result_resp.text}"
            )
        result_json = result_resp.json()

    log.append({"step": "result", "data": result_json})
    result = result_json["result"]
    assert result["expected_outcomes"] == ["ICP", "primary_kpi"]
    assert result["outcomes_filled"]["ICP"] == "AI founders"
    assert result["outcomes_filled"]["primary_kpi"] == "weekly SQL growth"
    assert result["outcomes_missing"] == []
    assert result["outcomes_completion"] == {"filled": 2, "total": 2}

    # Case 2: both provided with conflict -> 400 + outcomes_conflict
    conflict = req(
        "POST",
        "/rooms",
        payload={
            "topic": "Alias conflict",
            "goal": "Verify conflict handling",
            "participants": ["a", "b"],
            "required_fields": ["ICP"],
            "expected_outcomes": ["primary_kpi"],
            "turn_limit": 12,
            "timeout_minutes": 20,
            "stall_limit": 2,
            "metadata": {"source": "e2e_expected_outcomes_alias"},
        },
        expect_status=400,
    )
    log.append({"step": "create_conflict", "data": conflict})
    assert conflict.get("error_code") == "outcomes_conflict"

    out = {"ok": True, "cases": ["expected_outcomes_only", "conflict_400"], "log": log}
    (REPORT_DIR / "e2e_expected_outcomes_alias.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("e2e_expected_outcomes_alias passed")


if __name__ == "__main__":
    main()
