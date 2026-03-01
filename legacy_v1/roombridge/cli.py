from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def http_json(method: str, url: str, *, token: str | None = None, payload: dict[str, Any] | None = None) -> Any:
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    else:
        data = None
    if token:
        headers["X-Invite-Token"] = token
    req = urllib.request.Request(url=url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        print(json.dumps({"status": exc.code, "error": parsed}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)


def dump(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def parse_fills(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--fill must be key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def base_arg(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--base-url", default="http://127.0.0.1:8080")


def cmd_create(args: argparse.Namespace) -> None:
    dump(
        http_json(
            "POST",
            f"{args.base_url.rstrip('/')}/rooms",
            payload={
                "topic": args.topic,
                "goal": args.goal,
                "participants": args.participants,
                "required_fields": args.required_field,
                "turn_limit": args.turn_limit,
                "timeout_minutes": args.timeout_minutes,
                "stall_limit": args.stall_limit,
            },
        )
    )


def cmd_invite(args: argparse.Namespace) -> None:
    dump(http_json("GET", f"{args.base_url.rstrip('/')}/invites/{urllib.parse.quote(args.token)}"))


def cmd_join(args: argparse.Namespace) -> None:
    dump(
        http_json(
            "POST",
            f"{args.base_url.rstrip('/')}/rooms/{args.room_id}/join",
            token=args.token,
            payload={"client_name": args.client_name},
        )
    )


def cmd_leave(args: argparse.Namespace) -> None:
    dump(
        http_json(
            "POST",
            f"{args.base_url.rstrip('/')}/rooms/{args.room_id}/leave",
            token=args.token,
            payload={"reason": args.reason},
        )
    )


def cmd_send(args: argparse.Namespace) -> None:
    dump(
        http_json(
            "POST",
            f"{args.base_url.rstrip('/')}/rooms/{args.room_id}/messages",
            token=args.token,
            payload={
                "intent": args.intent,
                "text": args.text,
                "fills": parse_fills(args.fill),
                "facts": args.fact,
                "questions": args.question,
                "wants_reply": not args.no_reply,
            },
        )
    )


def cmd_events(args: argparse.Namespace) -> None:
    qs = urllib.parse.urlencode({"after": args.after, "limit": args.limit})
    dump(http_json("GET", f"{args.base_url.rstrip('/')}/rooms/{args.room_id}/events?{qs}", token=args.token))


def cmd_result(args: argparse.Namespace) -> None:
    dump(http_json("GET", f"{args.base_url.rstrip('/')}/rooms/{args.room_id}/result", token=args.token))


def cmd_close(args: argparse.Namespace) -> None:
    dump(
        http_json(
            "POST",
            f"{args.base_url.rstrip('/')}/rooms/{args.room_id}/close",
            token=args.token,
            payload={"reason": args.reason},
        )
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RoomBridge CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="Create room")
    base_arg(create)
    create.add_argument("--topic", required=True)
    create.add_argument("--goal", required=True)
    create.add_argument("--participants", nargs="+", required=True)
    create.add_argument("--required-field", action="append", default=[])
    create.add_argument("--turn-limit", type=int, default=12)
    create.add_argument("--timeout-minutes", type=int, default=20)
    create.add_argument("--stall-limit", type=int, default=2)
    create.set_defaults(func=cmd_create)

    invite = sub.add_parser("invite", help="Inspect invite token")
    base_arg(invite)
    invite.add_argument("--token", required=True)
    invite.set_defaults(func=cmd_invite)

    join = sub.add_parser("join", help="Join room")
    base_arg(join)
    join.add_argument("--room-id", required=True)
    join.add_argument("--token", required=True)
    join.add_argument("--client-name", default=None)
    join.set_defaults(func=cmd_join)

    leave = sub.add_parser("leave", help="Leave room")
    base_arg(leave)
    leave.add_argument("--room-id", required=True)
    leave.add_argument("--token", required=True)
    leave.add_argument("--reason", default="left room")
    leave.set_defaults(func=cmd_leave)

    send = sub.add_parser("send", help="Send message")
    base_arg(send)
    send.add_argument("--room-id", required=True)
    send.add_argument("--token", required=True)
    send.add_argument("--intent", choices=["ASK", "ANSWER", "DONE", "NEED_HUMAN", "NOTE"], default="ANSWER")
    send.add_argument("--text", required=True)
    send.add_argument("--fill", action="append", default=[])
    send.add_argument("--fact", action="append", default=[])
    send.add_argument("--question", action="append", default=[])
    send.add_argument("--no-reply", action="store_true")
    send.set_defaults(func=cmd_send)

    events = sub.add_parser("events", help="Fetch room events")
    base_arg(events)
    events.add_argument("--room-id", required=True)
    events.add_argument("--token", required=True)
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--limit", type=int, default=200)
    events.set_defaults(func=cmd_events)

    result = sub.add_parser("result", help="Fetch room result")
    base_arg(result)
    result.add_argument("--room-id", required=True)
    result.add_argument("--token", required=True)
    result.set_defaults(func=cmd_result)

    close = sub.add_parser("close", help="Close room")
    base_arg(close)
    close.add_argument("--room-id", required=True)
    close.add_argument("--token", required=True)
    close.add_argument("--reason", default="manual close")
    close.set_defaults(func=cmd_close)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

