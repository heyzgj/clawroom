#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from clawroom_launch_participant import DEFAULT_API_BASE, join_participant
from room_poller import load_owner_context, owner_context_path, request_json, room_dir, write_json_atomic
from state_paths import resolve_state_root


# Dedup window: any room created in the last N seconds with a similar topic
# is treated as a probable duplicate. The agent can override with --allow-duplicate
# after asking the owner.
DEDUP_WINDOW_SECONDS = 300  # 5 minutes


def _recent_rooms_path() -> Path:
    return resolve_state_root() / "recent_rooms.json"


def _normalize_topic(topic: str) -> str:
    return " ".join(str(topic).strip().lower().split())


def _load_recent_rooms() -> list[dict[str, Any]]:
    path = _recent_rooms_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]
    except Exception:  # noqa: BLE001
        return []


def _save_recent_rooms(rooms: list[dict[str, Any]]) -> None:
    path = _recent_rooms_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, rooms)  # type: ignore[arg-type]


def _check_duplicate(topic: str) -> dict[str, Any] | None:
    """Return the most recent matching room from the last 5 minutes, or None."""
    norm = _normalize_topic(topic)
    if not norm:
        return None
    now = int(time.time())
    rooms = _load_recent_rooms()
    # Newest first
    for entry in sorted(rooms, key=lambda r: int(r.get("created_at", 0)), reverse=True):
        if int(entry.get("created_at", 0)) < now - DEDUP_WINDOW_SECONDS:
            continue
        if _normalize_topic(str(entry.get("topic", ""))) == norm:
            return entry
    return None


def _record_recent_room(room_id: str, topic: str, host_token: str) -> None:
    """Append the new room to recent_rooms.json, keeping only entries from the last 24h."""
    now = int(time.time())
    cutoff = now - 86400  # 24 hours
    rooms = [r for r in _load_recent_rooms() if int(r.get("created_at", 0)) >= cutoff]
    rooms.append({
        "room_id": room_id,
        "topic": topic,
        "host_token": host_token,
        "created_at": now,
    })
    _save_recent_rooms(rooms)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a ClawRoom, verify it live, join as host, and print the host poller command for a separate exec call.")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--required-field", action="append", dest="required_fields", default=[])
    parser.add_argument("--timeout-minutes", type=int, default=30)
    parser.add_argument("--turn-limit", type=int, default=50)
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
    parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="Override the dedup check (use after the owner confirms a second room is wanted).",
    )
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

    # Dedup check: warn if a room with the same topic was created in the last 5 minutes.
    # The agent should ask the owner before retrying with --allow-duplicate.
    if not args.allow_duplicate:
        existing = _check_duplicate(args.topic)
        if existing:
            print(
                json.dumps(
                    {
                        "status": "duplicate_detected",
                        "existing_room_id": existing.get("room_id"),
                        "existing_topic": existing.get("topic"),
                        "existing_created_at": existing.get("created_at"),
                        "message": (
                            "A room with this topic was created in the last 5 minutes. "
                            "Ask the owner if they meant to open another one. "
                            "If yes, retry with --allow-duplicate."
                        ),
                    },
                    indent=2,
                )
            )
            sys.exit(2)

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
    action_urls = create_response.get("action_urls") or {}
    cancel_url = str(action_urls.get("cancel") or "").strip()
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

    # Record the room for future dedup checks. Best-effort: if the file write
    # fails, we still proceed — dedup is a UX guard, not a correctness gate.
    try:
        _record_recent_room(room_id, args.topic, host_token)
    except Exception:  # noqa: BLE001
        pass

    host_room_dir = room_dir(room_id, args.host_name)
    host_room_dir.mkdir(parents=True, exist_ok=True)
    host_context_path = owner_context_path(room_id, args.host_name)
    write_json_atomic(host_context_path, owner_context)

    launch_payload = join_participant(
        argparse.Namespace(
            join_url=f"{args.api_base.rstrip('/')}{host_join_relative}",
            owner_context_file=str(host_context_path),
            role="host",
            api_base=args.api_base,
            agent_id=args.agent_id,
            owner_session_id=args.owner_session_id,
            session_id=str(args.session_id or f"clawroom-{room_id}-{args.host_name}"),
            client_name=args.client_name,
            poll_seconds=args.poll_seconds,
            openclaw_timeout=args.openclaw_timeout,
            owner_wait_timeout=args.owner_wait_timeout,
            heartbeat_seconds=args.heartbeat_seconds,
            thinking=args.thinking,
            reply_channel=args.reply_channel,
            reply_to=args.reply_to,
            reply_account=args.reply_account,
            after=args.after,
        )
    )

    print(
        json.dumps(
            {
                "status": "host_joined",
                "room_id": room_id,
                "topic": create_payload["topic"],
                "goal": create_payload["goal"],
                "required_fields": required_fields,
                "timeout_minutes": int(args.timeout_minutes),
                "host_name": args.host_name,
                "counterpart_name": args.counterpart_name,
                "host_token": host_token,
                "monitor_link": monitor_link,
                "counterpart_join_url": f"{args.api_base.rstrip('/')}{counterpart_join_relative}",
                "cancel_url": cancel_url,
                "host_launch": launch_payload,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
