#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from room_poller import poller_runtime_path, poller_session_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render the owner-facing host ready message only after the poller session is recorded.")
    parser.add_argument("--launch-json", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--install-command", default="npx skills add heyzgj/clawroom")
    parser.add_argument("--plain", action="store_true", help="Print the final owner-facing text instead of JSON.")
    return parser


def load_launch(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("launch json must be an object")
    return raw


def zh_text(launch: dict[str, object], install_command: str) -> str:
    topic = str(launch.get("topic") or "").strip()
    goal = str(launch.get("goal") or "").strip()
    monitor_link = str(launch.get("monitor_link") or "").strip()
    join_url = str(launch.get("counterpart_join_url") or "").strip()
    timeout_minutes = int(launch.get("timeout_minutes") or 30)
    required_fields = [str(item).strip() for item in (launch.get("required_fields") or []) if str(item).strip()]
    bring_back = "、".join(required_fields) if required_fields else "双方同步后的最终安排"
    return (
        f"房间准备好了。进度：{monitor_link}\n\n"
        "~~~\n"
        "ClawRoom Invite\n\n"
        f"主题：{topic}\n"
        f"目标：{goal}\n"
        f"带回：{bring_back}\n"
        f"时限：{timeout_minutes} 分钟\n\n"
        "动作：使用你已安装的 ClawRoom skill 立即加入这个房间。\n\n"
        f"Join here: {join_url}\n\n"
        f"第一次使用？先安装：{install_command}\n"
        "~~~"
    )


def en_text(launch: dict[str, object], install_command: str) -> str:
    topic = str(launch.get("topic") or "").strip()
    goal = str(launch.get("goal") or "").strip()
    monitor_link = str(launch.get("monitor_link") or "").strip()
    join_url = str(launch.get("counterpart_join_url") or "").strip()
    timeout_minutes = int(launch.get("timeout_minutes") or 30)
    required_fields = [str(item).strip() for item in (launch.get("required_fields") or []) if str(item).strip()]
    bring_back = ", ".join(required_fields) if required_fields else "the aligned final schedule"
    return (
        f"Room ready. Watch here: {monitor_link}\n\n"
        "~~~\n"
        "ClawRoom Invite\n\n"
        f"Topic: {topic}\n"
        f"Goal: {goal}\n"
        f"Bring back: {bring_back}\n"
        f"Deadline: {timeout_minutes} minutes\n\n"
        "Action: Use your installed ClawRoom skill to join this room now.\n\n"
        f"Join here: {join_url}\n\n"
        f"First time? Install first: {install_command}\n"
        "~~~"
    )


def main() -> None:
    args = build_parser().parse_args()
    launch_path = Path(args.launch_json).expanduser()
    launch = load_launch(launch_path)
    room_id = str(launch.get("room_id") or "").strip()
    participant_name = str(launch.get("host_name") or "host_openclaw").strip()
    session_path = poller_session_path(room_id, participant_name)
    if not session_path.exists():
        raise SystemExit(f"host poller session not recorded yet: {session_path}")
    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    session_id = str(session_payload.get("session_id") or "").strip()
    if not session_id:
        raise SystemExit(f"host poller session missing session_id: {session_path}")
    runtime_path = poller_runtime_path(room_id, participant_name)
    if not runtime_path.exists():
        raise SystemExit(f"host poller runtime not recorded yet: {runtime_path}")
    runtime_payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    last_heartbeat_at = runtime_payload.get("last_heartbeat_at")
    if not isinstance(last_heartbeat_at, int) or last_heartbeat_at <= 0:
        raise SystemExit(f"host poller has not emitted a heartbeat yet: {runtime_path}")
    if time.time() - last_heartbeat_at > 45:
        raise SystemExit(f"host poller heartbeat is stale: {runtime_path}")

    language = str(args.language or "en").strip().lower()
    text = zh_text(launch, args.install_command) if language.startswith("zh") else en_text(launch, args.install_command)

    if args.plain:
        print(text)
        return

    print(
        json.dumps(
            {
                "status": "ready",
                "room_id": room_id,
                "participant_name": participant_name,
                "poller_session_id": session_id,
                "message": text,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
