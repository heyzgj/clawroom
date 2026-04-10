#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import sys
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
    poller_session_path,
    parse_join_url,
    request_json,
    room_dir,
    write_json_atomic,
)
from state_paths import resolve_state_root


DEFAULT_API_BASE = "https://api.clawroom.cc"


def recommended_poller_exec_timeout_seconds(room: dict[str, Any]) -> int:
    try:
        timeout_minutes = int(room.get("timeout_minutes") or 30)
    except Exception:  # noqa: BLE001
        timeout_minutes = 30
    return max(1800, timeout_minutes * 60 + 300)


def verify_joined(room: dict[str, Any], participant_name: str) -> bool:
    for participant in room.get("participants") or []:
        if str(participant.get("name") or "") == participant_name and bool(participant.get("joined")):
            return True
    return False


def participant_snapshot(room: dict[str, Any], participant_name: str) -> dict[str, Any]:
    for participant in room.get("participants") or []:
        if str(participant.get("name") or "") == participant_name:
            return participant
    return {}


def build_poller_argv(
    *,
    api_base: str,
    room_id: str,
    participant_token: str,
    participant_name: str,
    owner_context_target: Path,
    role: str,
    agent_id: str,
    owner_session_id: str,
    session_id: str,
    client_name: str,
    poll_seconds: float,
    openclaw_timeout: int,
    owner_wait_timeout: int,
    heartbeat_seconds: float,
    thinking: str,
    after: int,
    reply_channel: str | None,
    reply_to: str | None,
    reply_account: str | None,
) -> list[str]:
    poller_script = Path(__file__).with_name("room_poller.py")
    argv = [
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
        role,
        "--agent-id",
        agent_id,
        "--owner-session-id",
        owner_session_id,
        "--session-id",
        session_id,
        "--client-name",
        client_name,
        "--poll-seconds",
        str(poll_seconds),
        "--openclaw-timeout",
        str(openclaw_timeout),
        "--owner-wait-timeout",
        str(owner_wait_timeout),
        "--heartbeat-seconds",
        str(heartbeat_seconds),
        "--thinking",
        thinking,
        "--after",
        str(after),
    ]
    if reply_channel:
        argv.extend(["--reply-channel", reply_channel])
    if reply_to:
        argv.extend(["--reply-to", reply_to])
    if reply_account:
        argv.extend(["--reply-account", reply_account])
    return argv


def build_poller_command(argv: list[str]) -> str:
    return shlex.join(argv)


def build_cron_job(
    *,
    room_id: str,
    participant_token: str,
    participant_name: str,
    api_base: str,
    owner_context: dict[str, Any],
    room: dict[str, Any],
) -> dict[str, Any]:
    """Build a cron job specification the agent can pass to cron.add.

    This is the recommended auto-monitoring path — survives process restarts,
    no SIGKILL risk, and works on any runtime with cron support (OpenClaw, Hermes).
    """
    status_url = f"{api_base}/act/{room_id}/status?token={participant_token}"
    send_url = f"{api_base}/act/{room_id}/send?token={participant_token}"
    done_url = f"{api_base}/act/{room_id}/done?token={participant_token}"

    # Extract owner constraints for the cron prompt
    constraints = []
    do_not_share = []
    task_context = ""
    if owner_context:
        for fact in owner_context.get("confirmed_facts") or []:
            constraints.append(str(fact))
        for item in owner_context.get("do_not_share") or []:
            do_not_share.append(str(item))
        task_context = str(owner_context.get("task_context") or "")

    topic = str(room.get("topic") or "")
    goal = str(room.get("goal") or "")
    required_fields = room.get("required_fields") or room.get("expected_outcomes") or []

    prompt_lines = [
        f"You are auto-monitoring ClawRoom room {room_id} as {participant_name}.",
        f"Topic: {topic}",
        f"Goal: {goal}",
        f"Required fields: {', '.join(str(f) for f in required_fields)}",
        "",
        f"Status: web_fetch(\"{status_url}\")",
        f"Send: web_fetch(\"{send_url}&text=URL_ENCODED&intent=ANSWER&expect_reply=true&fills=URL_ENCODED_JSON\")",
        f"Done: web_fetch(\"{done_url}&text=URL_ENCODED_SUMMARY\")",
        "",
    ]
    if task_context:
        prompt_lines.append(f"Owner task: {task_context}")
    if constraints:
        prompt_lines.append(f"Owner context: {'; '.join(constraints[:5])}")
    if do_not_share:
        prompt_lines.append(f"NEVER reveal: {'; '.join(do_not_share[:5])}")
    prompt_lines.extend([
        "",
        "Steps:",
        "1. web_fetch the status URL. Parse the JSON response.",
        "2. Check room.status — if 'closed', report the final result to the owner and remove this cron job (cron.remove). STOP.",
        "3. Check events[] for new msg events from the counterpart (sender != you). If none, STOP (do nothing).",
        "4. If there are new messages, read them and respond using the send URL. Include fills= with every field you can contribute.",
        "5. If all required_fields are filled (check room.fields), send DONE with a summary.",
        "6. Respond in the language your owner uses. Fill with prose, never JSON-in-a-string.",
    ])

    return {
        "name": f"clawroom-{room_id}",
        "schedule_ms": 60000,
        "session_target": "isolated",
        "prompt": "\n".join(prompt_lines),
        "description": f"Auto-monitor ClawRoom room: {topic}",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Join a ClawRoom participant, verify it, and print the poller command for a separate exec call.")
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
    return parser


def join_participant(args: argparse.Namespace) -> dict[str, Any]:
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

    poller_log = participant_room_dir / "poller.log"
    session_id = str(args.session_id or f"clawroom-{room_id}-{participant_name}")
    poller_argv = build_poller_argv(
        api_base=api_base,
        room_id=room_id,
        participant_token=participant_token,
        participant_name=participant_name,
        owner_context_target=owner_context_target,
        role=args.role,
        agent_id=args.agent_id,
        owner_session_id=args.owner_session_id,
        session_id=session_id,
        client_name=args.client_name,
        poll_seconds=args.poll_seconds,
        openclaw_timeout=args.openclaw_timeout,
        owner_wait_timeout=args.owner_wait_timeout,
        heartbeat_seconds=args.heartbeat_seconds,
        thinking=args.thinking,
        after=args.after,
        reply_channel=args.reply_channel,
        reply_to=args.reply_to,
        reply_account=args.reply_account,
    )
    cron_job = build_cron_job(
        room_id=room_id,
        participant_token=participant_token,
        participant_name=participant_name,
        api_base=api_base,
        owner_context=owner_context,
        room=room,
    )

    return {
        "status": "joined",
        "room_id": room_id,
        "participant_name": participant_name,
        "participant_token": participant_token,
        "watch_link": watch_link,
        "cron_job": cron_job,
        "poller_command": build_poller_command(poller_argv),
        "poller_args": poller_argv,
        "poller_exec_timeout_seconds": recommended_poller_exec_timeout_seconds(room),
        "poller_log": str(poller_log),
        "poller_pid_file": str(participant_room_dir / "poller.pid"),
        "poller_session_file": str(poller_session_path(room_id, participant_name)),
        "state_root": str(state_root),
        "owner_context_file": str(owner_context_target),
        "session_id": session_id,
    }


def main() -> None:
    args = build_parser().parse_args()
    print(json.dumps(join_participant(args), indent=2))


if __name__ == "__main__":
    main()
