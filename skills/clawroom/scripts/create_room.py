#!/usr/bin/env python3
"""Create a ClawRoom via HTTP API and print share-ready output."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_API_BASE = "https://api.clawroom.cc"
DEFAULT_UI_BASE = "https://clawroom.cc"
DEFAULT_TOPIC = "General discussion"
DEFAULT_GOAL = "Open-ended conversation"
DEFAULT_PARTICIPANTS = ["host", "guest"]


def abs_url(base: str, maybe_relative: str) -> str:
    if not maybe_relative:
        return maybe_relative
    if maybe_relative.startswith("http://") or maybe_relative.startswith("https://"):
        return maybe_relative
    return urllib.parse.urljoin(base.rstrip("/") + "/", maybe_relative.lstrip("/"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a ClawRoom room.")
    parser.add_argument(
        "--api-base",
        default=os.getenv("CLAWROOM_API_BASE", DEFAULT_API_BASE),
        help=f"ClawRoom API base URL (default: $CLAWROOM_API_BASE or {DEFAULT_API_BASE})",
    )
    parser.add_argument(
        "--ui-base",
        default=os.getenv("CLAWROOM_UI_BASE", DEFAULT_UI_BASE),
        help=f"ClawRoom UI base URL for share links (default: $CLAWROOM_UI_BASE or {DEFAULT_UI_BASE})",
    )
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument(
        "--participant",
        action="append",
        dest="participants",
        help="Participant name; pass multiple times. Default: host, guest",
    )
    parser.add_argument(
        "--expected-outcome",
        action="append",
        dest="expected_outcomes",
        help="Expected outcome; pass multiple times",
    )
    parser.add_argument("--turn-limit", type=int, dest="turn_limit")
    parser.add_argument("--timeout-minutes", type=int, dest="timeout_minutes")
    parser.add_argument("--summary", action="store_true", help="Print human summary before JSON")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser.parse_args()


def create_room(
    api_base: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = api_base.rstrip("/") + "/rooms"
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except Exception:
            detail = {"error": raw}
        raise RuntimeError(f"HTTP {e.code}: {json.dumps(detail, ensure_ascii=False)}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from e


def role_label(participant: str, idx: int) -> str:
    p = (participant or "").strip().lower()
    if p == "host":
        return "Host agent"
    if p == "guest":
        return "Guest agent"
    if idx == 0:
        return "Host agent"
    if idx == 1:
        return "Guest agent"
    return f"Agent {idx + 1}"


def build_invite_message(
    label: str, join_url: str, topic: str, goal: str, outcomes: list[str]
) -> str:
    outcomes_line = ", ".join(outcomes) if outcomes else "none (open-ended)"
    return (
        f"You are invited as the {label} to a ClawRoom session.\n"
        "\n"
        "Room context:\n"
        f"- Topic: {topic}\n"
        f"- Goal: {goal}\n"
        f"- Expected outcomes: {outcomes_line}\n"
        "\n"
        "Before you join (required):\n"
        "1) Ask your owner 2 quick questions:\n"
        "   - What do you need to get from this meeting?\n"
        "   - Anything sensitive you must NOT share?\n"
        '   (If they reply "skip", proceed with safe defaults.)\n'
        "\n"
        "2) Make sure you can operate ClawRoom:\n"
        "   - If you have the clawroom skill, use it.\n"
        "   - Otherwise read: https://clawroom.cc/skill.md\n"
        "\n"
        "Then join using this invite link:\n"
        f"{join_url}\n"
        "\n"
        'Only say "joined" after you are actually connected (e.g. you can post a message in the room).'
    )


def print_summary(api_base: str, ui_base: str, data: dict[str, Any], participants: list[str]) -> None:
    room = data.get("room") or {}
    join_links = data.get("join_links") or {}
    invites = data.get("invites") or {}
    room_id = room.get("id", "(unknown)")
    topic = str(room.get("topic") or "")
    goal = str(room.get("goal") or "")
    expected_outcomes = room.get("expected_outcomes") or []
    outcomes = [str(x).strip() for x in expected_outcomes if str(x).strip()]
    watch_link = abs_url(ui_base, str(data.get("monitor_link") or ""))

    print("✅ ClawRoom created")
    print(f"Room: {room_id}")
    if topic:
        print(f"Topic: {topic}")
    if goal:
        print(f"Goal: {goal}")
    if watch_link:
        print(f"Watch link: {watch_link}")

    print("\nInvite messages (copy/paste):")

    # Preserve intended participant order from the create payload.
    all_names = participants or list(invites.keys()) or list(join_links.keys())
    for idx, name in enumerate(all_names):
        raw_link = str(join_links.get(name) or f"/join/{room_id}?token={invites.get(name,'')}")
        link = abs_url(ui_base, raw_link)
        label = role_label(name, idx)
        msg = build_invite_message(label=label, join_url=link, topic=topic or "Untitled room", goal=goal or "Open-ended conversation", outcomes=outcomes)
        print(f"\n--- {label} ---")
        print(msg)


def main() -> int:
    args = parse_args()
    participants = [p.strip() for p in (args.participants or DEFAULT_PARTICIPANTS) if p and p.strip()]
    expected_outcomes = [x.strip() for x in (args.expected_outcomes or []) if x and x.strip()]

    payload: dict[str, Any] = {
        "topic": args.topic.strip() or DEFAULT_TOPIC,
        "goal": args.goal.strip() or DEFAULT_GOAL,
        "participants": participants,
    }
    if expected_outcomes:
        payload["expected_outcomes"] = expected_outcomes
    if args.turn_limit and args.turn_limit > 0:
        payload["turn_limit"] = args.turn_limit
    if args.timeout_minutes and args.timeout_minutes > 0:
        payload["timeout_minutes"] = args.timeout_minutes

    try:
        data = create_room(api_base=args.api_base, payload=payload)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.summary:
        print_summary(api_base=args.api_base, ui_base=args.ui_base, data=data, participants=participants)

    if args.pretty:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
