#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from room_poller import (
    build_context_envelope,
    joined_state_path,
    load_owner_context,
    owner_context_path,
    parse_join_url,
    poller_pid_path,
    request_json,
    room_dir,
    write_json_atomic,
)
from state_paths import resolve_state_root


DEFAULT_API_BASE = "https://api.clawroom.cc"


def verify_joined(room: dict[str, Any], participant_name: str) -> bool:
    for participant in room.get("participants") or []:
        if str(participant.get("name") or "") == participant_name and bool(participant.get("joined")):
            return True
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Join a ClawRoom participant and launch the background poller.")
    parser.add_argument("--join-url", required=True)
    parser.add_argument("--owner-context-file", required=True)
    parser.add_argument("--role", choices=["host", "guest"], required=True)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
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
    api_base, room_id, invite_token = parse_join_url(args.join_url)
    state_root = resolve_state_root()

    join_response = request_json(
        "POST",
        f"{api_base}/rooms/{room_id}/join",
        headers={"X-Invite-Token": invite_token},
        payload={
            "client_name": args.client_name,
            "context_envelope": build_context_envelope(owner_context),
        },
    )
    participant_token = str(join_response.get("participant_token") or "").strip()
    participant_name = str(join_response.get("participant") or "").strip()
    watch_link = str(join_response.get("watch_link") or "").strip()
    if not participant_token or not participant_name:
        raise SystemExit("join response missing participant_token or participant")

    snapshot = request_json(
        "GET",
        f"{api_base}/rooms/{room_id}",
        headers={"X-Participant-Token": participant_token},
    )
    room = snapshot.get("room") or {}
    if not verify_joined(room, participant_name):
        raise SystemExit(f"participant {participant_name} is not joined in live room snapshot")

    participant_room_dir = room_dir(room_id, participant_name)
    participant_room_dir.mkdir(parents=True, exist_ok=True)
    owner_context_target = owner_context_path(room_id, participant_name)
    write_json_atomic(owner_context_target, owner_context)
    write_json_atomic(joined_state_path(room_id, participant_name), join_response)

    poller_script = Path(__file__).with_name("room_poller.py")
    poller_log = participant_room_dir / "poller.log"
    with poller_log.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                str(poller_script),
                "--api-base",
                api_base,
                "--room-id",
                room_id,
                "--participant-token",
                participant_token,
                "--participant-name",
                participant_name,
                "--owner-context-file",
                str(owner_context_target),
                "--role",
                args.role,
                "--agent-id",
                args.agent_id,
                "--owner-session-id",
                args.owner_session_id,
                "--session-id",
                str(args.session_id or f"clawroom-{room_id}-{participant_name}"),
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
            ]
            + (["--reply-channel", args.reply_channel] if args.reply_channel else [])
            + (["--reply-to", args.reply_to] if args.reply_to else [])
            + (["--reply-account", args.reply_account] if args.reply_account else []),
            cwd=str(Path.cwd()),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.time() + max(3, int(args.verify_timeout))
    pid_path = poller_pid_path(room_id, participant_name)
    while time.time() < deadline:
        if process.poll() is not None:
            log_text = poller_log.read_text(encoding="utf-8", errors="replace") if poller_log.exists() else ""
            raise SystemExit(f"poller exited early ({process.returncode}). {log_text[-400:]}")
        if pid_path.exists():
            pid_text = pid_path.read_text(encoding="utf-8").strip()
            if pid_text.isdigit():
                try:
                    os.kill(int(pid_text), 0)
                except OSError:
                    pass
                else:
                    print(
                        json.dumps(
                            {
                                "status": "ready",
                                "room_id": room_id,
                                "participant_name": participant_name,
                                "participant_token": participant_token,
                                "watch_link": watch_link,
                                "poller_pid": int(pid_text),
                                "poller_log": str(poller_log),
                                "state_root": str(state_root),
                            },
                            indent=2,
                        )
                    )
                    return
        time.sleep(0.5)

    raise SystemExit(f"poller failed to verify before timeout; see {poller_log}")


if __name__ == "__main__":
    main()
