from __future__ import annotations

import argparse
import json
import time
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.autoresearch_sync_demo.common import api_base_url, parse_fill_pairs, parse_refs_arg

from clawroom_client_core.client import http_json


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_join(args: argparse.Namespace) -> None:
    payload: dict[str, Any] = {"client_name": args.client_name}
    if args.summary or args.refs:
        payload["context_envelope"] = {
            "summary": (args.summary or "").strip(),
            "refs": parse_refs_arg(args.refs),
        }
    out = http_json(
        "POST",
        f"{api_base_url(args.base_url)}/rooms/{args.room_id}/join",
        token=args.token,
        payload=payload,
    )
    _print_json(out)


def cmd_read(args: argparse.Namespace) -> None:
    out = http_json(
        "GET",
        f"{api_base_url(args.base_url)}/rooms/{args.room_id}/events?after={args.after}&limit={args.limit}",
        token=args.token,
    )
    _print_json(out)


def cmd_wait(args: argparse.Namespace) -> None:
    deadline = time.time() + max(1, int(args.timeout))
    after = int(args.after)
    poll_seconds = max(0.2, float(args.poll_seconds))
    room_batch: dict[str, Any] | None = None
    while time.time() < deadline:
        room_batch = http_json(
            "GET",
            f"{api_base_url(args.base_url)}/rooms/{args.room_id}/events?after={after}&limit={args.limit}",
            token=args.token,
        )
        events = room_batch.get("events") or []
        room = room_batch.get("room") or {}
        if events or room.get("status") != "active":
            _print_json(room_batch)
            return
        time.sleep(poll_seconds)
    _print_json(room_batch or {"room": {"status": "active"}, "events": [], "next_cursor": after})


def cmd_send(args: argparse.Namespace) -> None:
    payload = {
        "intent": args.intent,
        "text": args.text,
        "fills": parse_fill_pairs(args.fill),
        "facts": [],
        "questions": [],
        "expect_reply": bool(args.expect_reply),
        "meta": {},
    }
    out = http_json(
        "POST",
        f"{api_base_url(args.base_url)}/rooms/{args.room_id}/messages",
        token=args.token,
        payload=payload,
    )
    _print_json(out)


def cmd_get_outcome(args: argparse.Namespace) -> None:
    out = http_json(
        "GET",
        f"{api_base_url(args.base_url)}/rooms/{args.room_id}/result",
        token=args.token,
    )
    _print_json(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin ClawRoom sync helper for the autoresearch demo")
    parser.add_argument("--base-url", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    join = sub.add_parser("join")
    join.add_argument("--room-id", required=True)
    join.add_argument("--token", required=True)
    join.add_argument("--client-name", default="autoresearch-sync")
    join.add_argument("--summary")
    join.add_argument("--refs")
    join.set_defaults(func=cmd_join)

    read = sub.add_parser("read")
    read.add_argument("--room-id", required=True)
    read.add_argument("--token", required=True)
    read.add_argument("--after", type=int, default=0)
    read.add_argument("--limit", type=int, default=200)
    read.set_defaults(func=cmd_read)

    wait = sub.add_parser("wait")
    wait.add_argument("--room-id", required=True)
    wait.add_argument("--token", required=True)
    wait.add_argument("--after", type=int, default=0)
    wait.add_argument("--limit", type=int, default=200)
    wait.add_argument("--timeout", type=int, default=120)
    wait.add_argument("--poll-seconds", type=float, default=2.0)
    wait.set_defaults(func=cmd_wait)

    send = sub.add_parser("send")
    send.add_argument("--room-id", required=True)
    send.add_argument("--token", required=True)
    send.add_argument("--intent", default="ANSWER")
    send.add_argument("--text", required=True)
    send.add_argument("--expect-reply", action="store_true")
    send.add_argument("--fill", action="append", default=[])
    send.set_defaults(func=cmd_send)

    result = sub.add_parser("get-outcome")
    result.add_argument("--room-id", required=True)
    result.add_argument("--token", required=True)
    result.set_defaults(func=cmd_get_outcome)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
