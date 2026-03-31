#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from clawroom_launch_participant import DEFAULT_API_BASE
from room_poller import load_owner_context, owner_context_path, request_json, room_dir, write_json_atomic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a ClawRoom, verify it, join as host, and launch the host poller.")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--required-field", action="append", dest="required_fields", default=[])
    parser.add_argument("--timeout-minutes", type=int, default=30)
    parser.add_argument("--turn-limit", type=int, default=10)
    parser.add_argument("--owner-context-file", required=True)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--host-name", default="host_openclaw")
    parser.add_argument("--counterpart-name", default="counterpart_openclaw")
    parser.add_argument("--agent-id", default="main")
    parser.add_argument("--owner-session-id", default="main")
    parser.add_argument("--session-id")
    parser.add_argument("--client-name", default="ClawRoomPoller")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--openclaw-timeout", type=int, default=90)
    parser.add_argument("--owner-wait-timeout", type=int, default=300)
    parser.add_argument("--heartbeat-seconds", type=float, default=20.0)
    parser.add_argument("--thinking", default="minimal")
    parser.add_argument("--reply-channel")
    parser.add_argument("--reply-to")
    parser.add_argument("--reply-account")
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--verify-timeout", type=int, default=15)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    owner_context = load_owner_context(Path(args.owner_context_file).expanduser())
    participants = [str(args.host_name).strip(), str(args.counterpart_name).strip()]
    required_fields = [str(item).strip() for item in args.required_fields if str(item).strip()]
    if len(participants) != 2 or not participants[0] or not participants[1] or participants[0] == participants[1]:
        raise SystemExit("host-name and counterpart-name must be two unique non-empty names")
    if not required_fields:
        raise SystemExit("at least one --required-field is required")

    create_payload = {
        "topic": str(args.topic).strip(),
        "goal": str(args.goal).strip(),
        "participants": participants,
        "required_fields": required_fields,
        "timeout_minutes": int(args.timeout_minutes),
        "turn_limit": int(args.turn_limit),
    }
    create_response = request_json("POST", f"{args.api_base.rstrip('/')}/rooms", payload=create_payload)
    room = create_response.get("room") or {}
    room_id = str(room.get("id") or "").strip()
    host_token = str(create_response.get("host_token") or "").strip()
    join_links = create_response.get("join_links") or {}
    monitor_link = str(create_response.get("monitor_link") or "").strip()
    host_join_relative = str(join_links.get(args.host_name) or "").strip()
    counterpart_join_relative = str(join_links.get(args.counterpart_name) or "").strip()
    if not room_id or not host_token or not host_join_relative or not counterpart_join_relative:
        raise SystemExit("create response missing room_id, host_token, or join links")

    verified = request_json(
        "GET",
        f"{args.api_base.rstrip('/')}/rooms/{room_id}?host_token={host_token}",
    )
    verified_room = verified.get("room") or {}
    if str(verified_room.get("id") or "").strip() != room_id:
        raise SystemExit("live room verification failed")

    host_room_dir = room_dir(room_id, args.host_name)
    host_room_dir.mkdir(parents=True, exist_ok=True)
    host_context_path = owner_context_path(room_id, args.host_name)
    write_json_atomic(host_context_path, owner_context)

    launcher_script = SCRIPT_DIR / "clawroom_launch_participant.py"
    launch_command = [
        sys.executable,
        str(launcher_script),
        "--join-url",
        f"{args.api_base.rstrip('/')}{host_join_relative}",
        "--owner-context-file",
        str(host_context_path),
        "--role",
        "host",
        "--agent-id",
        args.agent_id,
        "--owner-session-id",
        args.owner_session_id,
        "--session-id",
        str(args.session_id or f"clawroom-{room_id}-{args.host_name}"),
        "--client-name",
        args.client_name,
        "--poll-seconds",
        str(args.poll_seconds),
        "--openclaw-timeout",
        str(args.openclaw_timeout),
        "--owner-wait-timeout",
        str(args.owner_wait_timeout),
        "--heartbeat-seconds",
        str(args.heartbeat_seconds),
        "--thinking",
        args.thinking,
        "--after",
        str(args.after),
        "--verify-timeout",
        str(args.verify_timeout),
    ]
    if args.reply_channel:
        launch_command.extend(["--reply-channel", args.reply_channel])
    if args.reply_to:
        launch_command.extend(["--reply-to", args.reply_to])
    if args.reply_account:
        launch_command.extend(["--reply-account", args.reply_account])

    launch_result = subprocess.run(
        launch_command,
        capture_output=True,
        text=True,
        timeout=max(30, args.openclaw_timeout + args.verify_timeout + 20),
        check=False,
    )
    if launch_result.returncode != 0:
        raise SystemExit((launch_result.stderr or launch_result.stdout or "host launcher failed").strip())
    launch_payload = json.loads(launch_result.stdout)

    print(
        json.dumps(
            {
                "status": "ready",
                "room_id": room_id,
                "topic": create_payload["topic"],
                "goal": create_payload["goal"],
                "required_fields": required_fields,
                "host_name": args.host_name,
                "counterpart_name": args.counterpart_name,
                "host_token": host_token,
                "monitor_link": monitor_link,
                "counterpart_join_url": f"{args.api_base.rstrip('/')}{counterpart_join_relative}",
                "host_launch": launch_payload,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
