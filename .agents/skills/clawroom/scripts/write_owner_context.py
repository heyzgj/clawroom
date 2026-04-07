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

from room_poller import owner_context_path, write_json_atomic
from state_paths import resolve_state_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a validated ClawRoom owner_context.json into the writable state root.")
    parser.add_argument("--owner-name", required=True)
    parser.add_argument("--owner-role", required=True)
    parser.add_argument("--task-context", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--confirmed-fact", action="append", default=[])
    parser.add_argument("--do-not-share", action="append", default=[])
    parser.add_argument("--room-id")
    parser.add_argument("--participant-name")
    parser.add_argument("--output")
    return parser


def resolve_output_path(*, room_id: str, participant_name: str, explicit_output: str) -> Path:
    if explicit_output:
        return Path(explicit_output).expanduser()
    if room_id and participant_name:
        return owner_context_path(room_id, participant_name)
    draft_dir = resolve_state_root() / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    return draft_dir / f"owner_context_{int(time.time() * 1000)}.json"


def main() -> None:
    args = build_parser().parse_args()
    room_id = str(args.room_id or "").strip()
    participant_name = str(args.participant_name or "").strip()
    output_path = resolve_output_path(
        room_id=room_id,
        participant_name=participant_name,
        explicit_output=str(args.output or "").strip(),
    )
    payload = {
        "owner_name": str(args.owner_name or "").strip(),
        "owner_role": str(args.owner_role or "").strip(),
        "confirmed_facts": [str(item).strip() for item in args.confirmed_fact if str(item).strip()],
        "do_not_share": [str(item).strip() for item in args.do_not_share if str(item).strip()],
        "task_context": str(args.task_context or "").strip(),
        "language": str(args.language or "").strip(),
    }
    missing = [key for key in ("owner_name", "owner_role", "task_context", "language") if not payload[key]]
    if missing:
        raise SystemExit(f"missing required fields: {', '.join(missing)}")
    write_json_atomic(output_path, payload)
    print(
        json.dumps(
            {
                "status": "written",
                "path": str(output_path),
                "room_id": room_id,
                "participant_name": participant_name,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
