from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.autoresearch_sync_demo.common import (
    api_base_url,
    http_json,
    parse_refs_arg,
    request_monitor_result,
    split_assignment_text,
)

DEFAULT_REQUIRED_FIELDS = [
    "best_result_summary",
    "dead_ends_summary",
    "assignment_a1",
    "assignment_a2",
]
SYNC_BLOCK_START = "<!-- CLAWROOM_SYNC_DIRECTIVES_START -->"
SYNC_BLOCK_END = "<!-- CLAWROOM_SYNC_DIRECTIVES_END -->"


def _field_value(fields: dict[str, Any], key: str) -> str:
    return str(fields.get(key, {}).get("value") or "").strip()


def validate_sync_fields(fields: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    best_result = _field_value(fields, "best_result_summary")
    dead_ends = _field_value(fields, "dead_ends_summary")
    assignment_a1 = _field_value(fields, "assignment_a1")
    assignment_a2 = _field_value(fields, "assignment_a2")

    if len(best_result) < 24:
        issues.append("best_result_summary is too short to be actionable")
    if not re.search(r"\d", best_result):
        issues.append("best_result_summary should include at least one concrete metric or numeric setting")

    dead_end_items = [item.strip(" -") for item in re.split(r"[;\n]+", dead_ends) if item.strip()]
    dead_ends_lower = dead_ends.lower()
    if not dead_end_items and "no confirmed dead ends" not in dead_ends_lower:
        issues.append("dead_ends_summary should name at least one concrete dead end or explicitly say there are no confirmed dead ends yet")
    if dead_end_items and len(dead_ends.strip()) < 16:
        issues.append("dead_ends_summary is too short to be actionable")

    for key, raw in (("assignment_a1", assignment_a1), ("assignment_a2", assignment_a2)):
        focus, constraints = split_assignment_text(raw)
        if len(focus.strip()) < 12:
            issues.append(f"{key} is missing a concrete focus")
        if len(constraints.strip()) < 8:
            issues.append(f"{key} is missing usable constraints; if there are no special constraints, say so explicitly")

    if assignment_a1 and assignment_a2 and assignment_a1.strip() == assignment_a2.strip():
        issues.append("assignment_a1 and assignment_a2 should not be identical")

    return issues


def build_carry_forward_summary(fields: dict[str, Any]) -> str:
    issues = validate_sync_fields(fields)
    if issues:
        raise ValueError("cannot build carry-forward summary from weak outcome: " + "; ".join(issues))
    best = _field_value(fields, "best_result_summary")
    dead = _field_value(fields, "dead_ends_summary")
    a1 = _field_value(fields, "assignment_a1")
    a2 = _field_value(fields, "assignment_a2")
    return f"Previous cycle best: {best} Dead ends: {dead} Next split: {a1} {a2}".strip()


def build_room_payload(args: argparse.Namespace) -> dict[str, Any]:
    required_fields = args.required_field or list(DEFAULT_REQUIRED_FIELDS)
    payload: dict[str, Any] = {
        "topic": args.topic,
        "goal": args.goal,
        "participants": args.participants,
        "required_fields": required_fields,
        "turn_limit": int(args.turn_limit),
        "timeout_minutes": int(args.timeout_minutes),
        "stall_limit": int(args.stall_limit),
        "metadata": {},
        "outcome_contract": {
            "close_conditions": {
                "min_turns": int(args.min_turns),
                "min_unique_participants": int(args.min_unique_participants),
                "require_explicit_consensus": True,
            },
            "resolution_mode": "consensus",
        },
    }
    if args.parent_room_id:
        payload["parent_room_id"] = args.parent_room_id
        payload["prior_outcome_summary"] = args.prior_outcome_summary
        payload["prior_outcome_refs"] = parse_refs_arg(args.prior_outcome_refs)
    return payload


def build_program_sync_block(fields: dict[str, Any], assignment_field: str) -> str:
    assignment_value = _field_value(fields, assignment_field)
    dead_ends = _field_value(fields, "dead_ends_summary")
    best_result = _field_value(fields, "best_result_summary")
    focus, constraints = split_assignment_text(assignment_value)
    dead_end_lines = [item.strip(" -") for item in re.split(r"[;\n]+", dead_ends) if item.strip()]
    rendered_dead_ends = "\n".join(f"- {item}" for item in dead_end_lines) if dead_end_lines else "- None yet."
    rendered_constraints = constraints or "None."
    return (
        f"{SYNC_BLOCK_START}\n"
        "## Current Focus\n"
        f"{focus}\n\n"
        "## Constraints\n"
        f"{rendered_constraints}\n\n"
        "## Known Dead Ends\n"
        f"{rendered_dead_ends}\n\n"
        "## Global Best So Far\n"
        f"{best_result}\n"
        f"{SYNC_BLOCK_END}\n"
    )


def apply_assignment_to_program(program_path: Path, fields: dict[str, Any], assignment_field: str) -> str:
    existing = program_path.read_text() if program_path.exists() else ""
    block = build_program_sync_block(fields, assignment_field)
    pattern = re.compile(rf"{re.escape(SYNC_BLOCK_START)}.*?{re.escape(SYNC_BLOCK_END)}\n?", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub(block, existing).rstrip() + "\n"
    else:
        separator = "\n\n" if existing and not existing.endswith("\n\n") else ""
        updated = f"{existing.rstrip()}{separator}{block}" if existing.strip() else block
    program_path.write_text(updated)
    return updated


def cmd_create_room(args: argparse.Namespace) -> None:
    out = http_json("POST", f"{api_base_url(args.base_url)}/rooms", payload=build_room_payload(args))
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_wait_close(args: argparse.Namespace) -> None:
    deadline = time.time() + max(1, int(args.timeout))
    last_room: dict[str, Any] | None = None
    while time.time() < deadline:
        batch = http_json(
            "GET",
            f"{api_base_url(args.base_url)}/rooms/{args.room_id}",
            host_token=args.host_token,
        )
        room = batch.get("room") or {}
        last_room = room
        if room.get("status") == "closed":
            out = request_monitor_result(base_url=args.base_url, room_id=args.room_id, host_token=args.host_token)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return
        time.sleep(max(0.2, float(args.poll_seconds)))
    print(json.dumps({"room": last_room or {"id": args.room_id, "status": "active"}}, ensure_ascii=False, indent=2))


def cmd_apply_assignments(args: argparse.Namespace) -> None:
    out = request_monitor_result(base_url=args.base_url, room_id=args.room_id, host_token=args.host_token)
    result = out.get("result") or {}
    fields = result.get("fields") or {}
    issues = validate_sync_fields(fields)
    if issues:
        raise SystemExit(json.dumps({"room_id": args.room_id, "error": "weak_outcome", "issues": issues}, ensure_ascii=False, indent=2))
    mapping = [("assignment_a1", Path(args.a1_dir) / "program.md"), ("assignment_a2", Path(args.a2_dir) / "program.md")]
    applied: dict[str, str] = {}
    for assignment_field, path in mapping:
        path.parent.mkdir(parents=True, exist_ok=True)
        apply_assignment_to_program(path, fields, assignment_field)
        applied[assignment_field] = str(path)
    print(json.dumps({"room_id": args.room_id, "applied": applied, "fields": fields}, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin room orchestration helper for the autoresearch x ClawRoom demo")
    parser.add_argument("--base-url", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create-room")
    create.add_argument("--topic", required=True)
    create.add_argument("--goal", required=True)
    create.add_argument("--participants", nargs="+", default=["agent_a1", "agent_a2"])
    create.add_argument("--required-field", action="append", default=[])
    create.add_argument("--turn-limit", type=int, default=12)
    create.add_argument("--timeout-minutes", type=int, default=30)
    create.add_argument("--stall-limit", type=int, default=3)
    create.add_argument("--min-turns", type=int, default=4)
    create.add_argument("--min-unique-participants", type=int, default=2)
    create.add_argument("--parent-room-id")
    create.add_argument("--prior-outcome-summary")
    create.add_argument("--prior-outcome-refs")
    create.set_defaults(func=cmd_create_room)

    wait = sub.add_parser("wait-close")
    wait.add_argument("--room-id", required=True)
    wait.add_argument("--host-token", required=True)
    wait.add_argument("--timeout", type=int, default=600)
    wait.add_argument("--poll-seconds", type=float, default=2.0)
    wait.set_defaults(func=cmd_wait_close)

    apply_cmd = sub.add_parser("apply-assignments")
    apply_cmd.add_argument("--room-id", required=True)
    apply_cmd.add_argument("--host-token", required=True)
    apply_cmd.add_argument("--a1-dir", required=True)
    apply_cmd.add_argument("--a2-dir", required=True)
    apply_cmd.set_defaults(func=cmd_apply_assignments)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
