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

from room_poller import poller_session_path, write_json_atomic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record the OpenClaw background exec session id for a ClawRoom poller.")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--participant-name", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--status", default="running")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    room_id = str(args.room_id or "").strip()
    participant_name = str(args.participant_name or "").strip()
    session_id = str(args.session_id or "").strip()
    if not room_id:
        raise SystemExit("room-id is required")
    if not participant_name:
        raise SystemExit("participant-name is required")
    if not session_id:
        raise SystemExit("session-id is required")

    path = poller_session_path(room_id, participant_name)
    payload = {
        "room_id": room_id,
        "participant_name": participant_name,
        "session_id": session_id,
        "pid": args.pid,
        "status": str(args.status or "running").strip() or "running",
        "recorded_at": int(time.time()),
    }
    write_json_atomic(path, payload)
    print(json.dumps({"status": "recorded", "path": str(path), "session_id": session_id}, indent=2))


if __name__ == "__main__":
    main()
