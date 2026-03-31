#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from state_paths import resolve_state_root


def spool_root() -> Path:
    return resolve_state_root() / "rooms"


def participant_key(participant_name: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in participant_name.strip().lower())
    return text or "participant"


def room_root_dir(room_id: str) -> Path:
    return spool_root() / room_id


def room_dir(room_id: str, participant_name: str) -> Path:
    return room_root_dir(room_id) / participant_key(participant_name)


def pending_question_path(room_id: str, participant_name: str) -> Path:
    return room_dir(room_id, participant_name) / "pending_question.json"


def owner_reply_path(room_id: str, participant_name: str) -> Path:
    return room_dir(room_id, participant_name) / "owner_reply.json"


def find_pending_rooms() -> list[tuple[str, str]]:
    root = spool_root()
    if not root.exists():
        return []
    matches: list[tuple[str, str]] = []
    for path in root.glob("*/*/pending_question.json"):
        participant_name = path.parent.name
        room_id = path.parent.parent.name
        matches.append((room_id, participant_name))
    return sorted(matches)


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write an owner reply for the active ClawRoom pending question.")
    parser.add_argument("--reply", required=True, help="Owner reply text to hand back to the room poller.")
    parser.add_argument("--room-id", help="Room id. Optional when exactly one room has a pending question.")
    parser.add_argument("--participant-name", help="Participant name when more than one participant runtime exists for the same room.")
    args = parser.parse_args()

    room_id = (args.room_id or "").strip()
    participant_name = str(args.participant_name or "").strip()
    if not room_id:
        rooms = find_pending_rooms()
        if len(rooms) != 1:
            labels = [f"{rid}/{pname}" for rid, pname in rooms]
            raise SystemExit(f"expected exactly one pending room, found {len(rooms)}: {', '.join(labels) or '(none)'}")
        room_id, participant_name = rooms[0]
    elif not participant_name:
        matches = [entry for entry in find_pending_rooms() if entry[0] == room_id]
        if len(matches) != 1:
            labels = [f"{rid}/{pname}" for rid, pname in matches]
            raise SystemExit(
                f"expected exactly one pending participant for room {room_id}, found {len(matches)}: {', '.join(labels) or '(none)'}"
            )
        _, participant_name = matches[0]

    question_file = pending_question_path(room_id, participant_name)
    if not question_file.exists():
        raise SystemExit(f"no pending question for room {room_id}/{participant_name}")

    question = json.loads(question_file.read_text(encoding="utf-8"))
    request_id = str(question.get("request_id") or "").strip()
    if not request_id:
        raise SystemExit(f"pending question for room {room_id}/{participant_name} is missing request_id")

    payload = {
        "request_id": request_id,
        "reply": str(args.reply).strip(),
    }
    write_json_atomic(owner_reply_path(room_id, participant_name), payload)
    print(json.dumps({"room_id": room_id, "participant_name": participant_name, "request_id": request_id, "status": "written"}, indent=2))


if __name__ == "__main__":
    main()
