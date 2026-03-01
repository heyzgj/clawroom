from __future__ import annotations

import argparse
import json
import os
import tempfile
from typing import Any
from urllib.parse import urlparse

import httpx


def request_json(method: str, url: str, token: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if token:
        headers["X-Invite-Token"] = token
    with httpx.Client(timeout=20.0) as client:
        resp = client.request(method, url, headers=headers, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"http {method} {url} failed status={resp.status_code} body={resp.text}")
    return resp.json()


def _absolutize(origin: str, path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if path_or_url.startswith("/"):
        return f"{origin.rstrip('/')}{path_or_url}"
    return f"{origin.rstrip('/')}/{path_or_url}"


def _default_monitor_origin(api_base: str) -> str:
    parsed = urlparse(api_base)
    host = parsed.hostname or ""
    scheme = parsed.scheme or "http"

    if host in {"127.0.0.1", "localhost"}:
        # Local monitor runs via Vite by default.
        return "http://127.0.0.1:5173"

    if host.startswith("api."):
        return f"{scheme}://{host[4:]}"

    return api_base


def _pretty_print_create(out: dict[str, Any], base: str, monitor_base: str | None = None) -> None:
    """Print a human-friendly room creation summary with copy-pasteable commands."""
    room = out.get("room", {})
    room_id = room.get("id", "?")
    host_token = out.get("host_token", "?")
    invites = out.get("invites", {})
    join_links = out.get("join_links", {})
    monitor_link = out.get("monitor_link")

    if not isinstance(join_links, dict):
        join_links = {}
    if not isinstance(monitor_link, str) or not monitor_link:
        monitor_link = f"/?room_id={room_id}&host_token={host_token}"

    monitor_origin = monitor_base or _default_monitor_origin(base)
    full_monitor_link = _absolutize(monitor_origin, monitor_link)

    print()
    print(f"  ✅ Room created: {room_id}")
    print(f"     Topic: {room.get('topic', '?')}")
    print()
    print(f"  📺 Monitor:")
    print(f"     {full_monitor_link}")
    print()

    for name, token in invites.items():
        raw_join = join_links.get(name) or f"/join/{room_id}?token={token}"
        join_url = _absolutize(base, str(raw_join))
        print(f"  🤖 {name}:")
        print(f'     uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "{join_url}"')
        print()

    # Also save JSON for programmatic use
    json_path = os.path.join(tempfile.gettempdir(), f"clawroom_{room_id}.json")
    with open(json_path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  📄 Raw JSON: {json_path}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="ClawRoom CLI")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument(
        "--monitor-base-url",
        default=os.getenv("CLAWROOM_MONITOR_BASE_URL"),
        help="Optional monitor origin override (e.g. https://clawroom.cc).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--topic", required=True)
    c.add_argument("--goal", required=True)
    c.add_argument("--participants", nargs="+", required=True)
    c.add_argument("--required-field", action="append", default=[])
    c.add_argument("--turn-limit", type=int, default=12)
    c.add_argument("--timeout-minutes", type=int, default=20)
    c.add_argument("--stall-limit", type=int, default=3)

    c.add_argument("--json", action="store_true", dest="json_output", help="Output raw JSON instead of formatted")

    j = sub.add_parser("join")
    j.add_argument("--room-id", required=True)
    j.add_argument("--token", required=True)
    j.add_argument("--client-name", default="cli")

    s = sub.add_parser("send")
    s.add_argument("--room-id", required=True)
    s.add_argument("--token", required=True)
    s.add_argument("--intent", default="ANSWER")
    s.add_argument("--text", required=True)
    s.add_argument("--expect-reply", action="store_true")
    s.add_argument("--fill", action="append", default=[])

    e = sub.add_parser("events")
    e.add_argument("--room-id", required=True)
    e.add_argument("--token", required=True)
    e.add_argument("--after", type=int, default=0)

    r = sub.add_parser("result")
    r.add_argument("--room-id", required=True)
    r.add_argument("--token", required=True)

    l = sub.add_parser("leave")
    l.add_argument("--room-id", required=True)
    l.add_argument("--token", required=True)
    l.add_argument("--reason", default="done")

    x = sub.add_parser("close")
    x.add_argument("--room-id", required=True)
    x.add_argument("--host-token", required=True)
    x.add_argument("--reason", default="manual close")

    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    if args.cmd == "create":
        out = request_json(
            "POST",
            f"{base}/rooms",
            payload={
                "topic": args.topic,
                "goal": args.goal,
                "participants": args.participants,
                "required_fields": args.required_field,
                "turn_limit": args.turn_limit,
                "timeout_minutes": args.timeout_minutes,
                "stall_limit": args.stall_limit,
                "metadata": {},
            },
        )

        if args.json_output:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            _pretty_print_create(out, base, args.monitor_base_url)
        return

    if args.cmd == "join":
        out = request_json(
            "POST",
            f"{base}/rooms/{args.room_id}/join",
            token=args.token,
            payload={"client_name": args.client_name},
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.cmd == "send":
        fills: dict[str, str] = {}
        for raw in args.fill:
            if "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            if k.strip() and v.strip():
                fills[k.strip()] = v.strip()
        out = request_json(
            "POST",
            f"{base}/rooms/{args.room_id}/messages",
            token=args.token,
            payload={
                "intent": args.intent,
                "text": args.text,
                "fills": fills,
                "facts": [],
                "questions": [],
                "expect_reply": bool(args.expect_reply),
                "meta": {},
            },
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.cmd == "events":
        out = request_json(
            "GET",
            f"{base}/rooms/{args.room_id}/events?after={args.after}&limit=200",
            token=args.token,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.cmd == "result":
        out = request_json("GET", f"{base}/rooms/{args.room_id}/result", token=args.token)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.cmd == "leave":
        out = request_json(
            "POST",
            f"{base}/rooms/{args.room_id}/leave",
            token=args.token,
            payload={"reason": args.reason},
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.cmd == "close":
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                f"{base}/rooms/{args.room_id}/close",
                headers={"X-Host-Token": args.host_token},
                json={"reason": args.reason},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"http close failed status={resp.status_code} body={resp.text}")
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
