from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import requests


BASE = os.getenv("CLAWROOM_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
REPORT_DIR = Path("reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def create_join_url(topic: str) -> str:
    payload = {
        "topic": topic,
        "goal": "Phase2 owner channel smoke",
        "participants": ["agent_a", "agent_b"],
    }
    resp = requests.post(f"{BASE}/rooms", json=payload, timeout=20)
    if resp.status_code >= 400:
        raise RuntimeError(f"create room failed status={resp.status_code} body={resp.text}")
    body = resp.json()
    room_id = body["room"]["id"]
    token = body["invites"]["agent_a"]
    return f"{BASE}/join/{room_id}?token={token}"


def run_bridge(join_url: str, extra_args: list[str]) -> tuple[int, str]:
    cmd = [
        "uv",
        "run",
        "python",
        "apps/openclaw-bridge/src/openclaw_bridge/cli.py",
        join_url,
        "--role",
        "responder",
        "--max-seconds",
        "2",
        "--poll-seconds",
        "0.5",
        "--preflight-mode",
        "confirm",
    ]
    cmd.extend(extra_args)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, output


def assert_contains(text: str, needle: str, case_name: str) -> None:
    if needle not in text:
        raise RuntimeError(f"{case_name}: expected output to contain {needle!r}, got: {text[:800]}")


def main() -> None:
    log: list[dict[str, Any]] = []

    # Case 1: owner-reply-cmd path
    join_1 = create_join_url("Phase2 cmd owner reply")
    rc_1, out_1 = run_bridge(
        join_1,
        [
            "--owner-reply-cmd",
            "printf '{owner_req_id}\\tyes\\n'",
        ],
    )
    log.append({"case": "owner_reply_cmd", "rc": rc_1, "join_url": join_1, "output": out_1})
    if rc_1 != 0:
        raise RuntimeError(f"owner_reply_cmd failed rc={rc_1}")
    assert_contains(out_1, "joined participant=", "owner_reply_cmd")

    # Case 2: openclaw channel with command fallback
    join_2 = create_join_url("Phase2 openclaw fallback")
    rc_2, out_2 = run_bridge(
        join_2,
        [
            "--owner-channel",
            "openclaw",
            "--owner-openclaw-channel",
            "telegram",
            "--owner-openclaw-target",
            "@dummy",
            "--owner-notify-cmd",
            "true",
            "--owner-reply-cmd",
            "printf '{owner_req_id}\\tyes\\n'",
        ],
    )
    log.append({"case": "openclaw_channel_fallback", "rc": rc_2, "join_url": join_2, "output": out_2})
    if rc_2 != 0:
        raise RuntimeError(f"openclaw_channel_fallback failed rc={rc_2}")
    assert_contains(out_2, "joined participant=", "openclaw_channel_fallback")
    assert_contains(out_2, "openclaw read unsupported; using fallback", "openclaw_channel_fallback")

    out = {"ok": True, "base": BASE, "cases": log}
    (REPORT_DIR / "e2e_owner_channel_smoke.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("e2e_owner_channel_smoke passed")


if __name__ == "__main__":
    main()
