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
DEFAULT_TOPIC = "General discussion"
DEFAULT_GOAL = "Open-ended conversation"
DEFAULT_PARTICIPANTS = ["agent_a", "agent_b"]


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
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument(
        "--participant",
        action="append",
        dest="participants",
        help="Participant name; pass multiple times. Default: agent_a, agent_b",
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


def print_summary(api_base: str, data: dict[str, Any]) -> None:
    room = data.get("room") or {}
    join_links = data.get("join_links") or {}
    invites = data.get("invites") or {}
    room_id = room.get("id", "(unknown)")
    topic = room.get("topic", "")
    monitor_link = abs_url(api_base, str(data.get("monitor_link") or ""))

    print(f"Room created: {room_id}")
    if topic:
        print(f"Topic: {topic}")
    if monitor_link:
        print(f"Monitor: {monitor_link}")
    for name in sorted(set(list(invites.keys()) + list(join_links.keys()))):
        raw_link = str(join_links.get(name) or "")
        link = abs_url(api_base, raw_link)
        print(f"Invite {name}: {link}")


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
        print_summary(api_base=args.api_base, data=data)

    if args.pretty:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
