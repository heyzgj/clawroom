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
    parser = argparse.ArgumentParser(description="Render the owner-facing guest joined message only after the poller session is recorded.")
    parser.add_argument("--launch-json", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--plain", action="store_true", help="Print the final owner-facing text instead of JSON.")
    return parser


def load_launch(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("launch json must be an object")
    return raw


def zh_text(launch: dict[str, object]) -> str:
    watch_link = str(launch.get("watch_link") or "").strip()
    return f"已加入。我会在这里继续同步。进度：{watch_link}"


def en_text(launch: dict[str, object]) -> str:
    watch_link = str(launch.get("watch_link") or "").strip()
    return f"Joined. I will keep this moving here. Watch: {watch_link}"


def main() -> None:
    args = build_parser().parse_args()
    launch_path = Path(args.launch_json).expanduser()
    launch = load_launch(launch_path)
    room_id = str(launch.get("room_id") or "").strip()
    participant_name = str(launch.get("participant_name") or "").strip()
    session_path = poller_session_path(room_id, participant_name)
    if not session_path.exists():
        raise SystemExit(f"guest poller session not recorded yet: {session_path}")
    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    session_id = str(session_payload.get("session_id") or "").strip()
    if not session_id:
        raise SystemExit(f"guest poller session missing session_id: {session_path}")
    runtime_path = poller_runtime_path(room_id, participant_name)
    if not runtime_path.exists():
        raise SystemExit(f"guest poller runtime not recorded yet: {runtime_path}")
    runtime_payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    last_heartbeat_at = runtime_payload.get("last_heartbeat_at")
    if not isinstance(last_heartbeat_at, int) or last_heartbeat_at <= 0:
        raise SystemExit(f"guest poller has not emitted a heartbeat yet: {runtime_path}")
    if time.time() - last_heartbeat_at > 45:
        raise SystemExit(f"guest poller heartbeat is stale: {runtime_path}")

    language = str(args.language or "en").strip().lower()
    text = zh_text(launch) if language.startswith("zh") else en_text(launch)

    if args.plain:
        print(text)
        return

    print(
        json.dumps(
            {
                "status": "joined",
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
