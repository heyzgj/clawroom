from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


REPORT_DIR = Path("reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Bot:
    name: str
    token: str
    cursor: int = 0
    sent: int = 0


def scripted_reply(bot: Bot, incoming: str) -> tuple[str, str, dict[str, str], bool]:
    """Deterministic multi-turn script with progressive content and structured closure."""
    step = bot.sent + 1
    incoming_short = incoming[:60].strip()

    if bot.name == "host":
        if step == 1:
            return (
                "ANSWER",
                "Nice kickoff. Alternative option: riverside cycling with a coffee stop. "
                "Do you prefer a calm nature route or a lively city route?",
                {},
                True,
            )
        if step == 2:
            return (
                "ANSWER",
                "Let's cap total budget around CNY 300 including transport and snacks. "
                "If that works, I can refine the route and timing.",
                {"budget_cny": "300"},
                True,
            )
        if step == 3:
            return (
                "ANSWER",
                "Given your preference, primary plan can be West Lake sunset walk plus a tea house stop. "
                "Any hard constraints for timing?",
                {},
                True,
            )
        if step == 4:
            return (
                "ANSWER",
                "Final proposal: West Lake sunset walk (90 min) + tea house (45 min), under CNY 300 total. "
                "This balances scenery, pace, and budget.",
                {"decision_summary": "West Lake sunset walk + tea house under CNY 300"},
                True,
            )
    else:
        if step == 2:
            return (
                "ANSWER",
                "I prefer calm scenery and photo-friendly places. "
                "City noise should be minimal.",
                {"vibe": "calm_scenic"},
                True,
            )
        if step == 3:
            return (
                "ANSWER",
                "Budget CNY 300 is acceptable. "
                "I prefer late afternoon to sunset timing.",
                {},
                True,
            )
        if step == 4:
            return (
                "ANSWER",
                "Confirmed. Destination should be West Lake, and the tea house stop sounds good.",
                {"destination": "West Lake"},
                True,
            )

    # Fallback for unexpected extra turns.
    return (
        "ANSWER",
        f"Received your point: {incoming_short}. I agree with the direction and can continue.",
        {},
        True,
    )


def req(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    expect: int = 200,
) -> dict[str, Any]:
    resp = client.request(method, url, headers=headers, json=payload)
    if resp.status_code != expect:
        raise RuntimeError(f"{method} {url} -> {resp.status_code}, expect={expect}, body={resp.text[:500]}")
    if not resp.text:
        return {}
    return resp.json()


def normalize(text: str) -> str:
    return " ".join(text.split())


def check_skill_contract(skill_path: Path) -> dict[str, Any]:
    text = skill_path.read_text(encoding="utf-8")
    compact = normalize(text)
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))

    required = [
        "Keep this skill file in English.",
        "When replying to humans, match the user's language.",
        "Never print raw planning JSON to the user.",
        "Keep the host runtime alive in a relay loop.",
        "`bash` tool access",
        "prefer the shell relay runner first (no Python/uv required).",
        "https://clawroom.cc/openclaw-shell-bridge.sh",
        "If this is your first clawroom task, read https://clawroom.cc/skill.md first.",
        "After successful join, immediately send the first in-room message (must):",
        "Continue conversation loop (must):",
        "Do not send kickoff before the guest has joined",
    ]
    missing = [s for s in required if s not in text]

    forbidden = [
        '"mode": "create|join|watch|close"',
        "Before any action, output a compact plan with this shape:",
    ]
    present_forbidden = [s for s in forbidden if s in text or s in compact]

    return {
        "ok": not missing and not present_forbidden and not has_cjk,
        "missing": missing,
        "forbidden_present": present_forbidden,
        "has_cjk": has_cjk,
    }


def post_message(
    client: httpx.Client,
    base: str,
    room_id: str,
    token: str,
    *,
    intent: str,
    text: str,
    expect_reply: bool,
    fills: dict[str, str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return req(
        client,
        "POST",
        f"{base}/rooms/{room_id}/messages",
        headers={"X-Invite-Token": token},
        payload={
            "intent": intent,
            "text": text,
            "fills": fills or {},
            "facts": [],
            "questions": [],
            "expect_reply": expect_reply,
            "meta": meta or {},
        },
    )


def fetch_participant_events(client: httpx.Client, base: str, room_id: str, bot: Bot) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    batch = req(
        client,
        "GET",
        f"{base}/rooms/{room_id}/events?after={bot.cursor}&limit=200",
        headers={"X-Invite-Token": bot.token},
    )
    bot.cursor = int(batch.get("next_cursor", bot.cursor))
    return list(batch.get("events", [])), batch.get("room", {})


def fetch_monitor_events(client: httpx.Client, base: str, room_id: str, host_token: str, after: int) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    batch = req(
        client,
        "GET",
        f"{base}/rooms/{room_id}/monitor/events?host_token={host_token}&after={after}&limit=500",
    )
    events = list(batch.get("events", []))
    next_cursor = int(batch.get("next_cursor", after))
    return events, next_cursor, batch.get("room", {})


def run_live_loop_check(base: str, min_round_messages: int, max_seconds: int) -> dict[str, Any]:
    log: list[dict[str, Any]] = []
    started = time.time()
    monitor_cursor = 0

    with httpx.Client(timeout=20.0, trust_env=False) as client:
        created = req(
            client,
            "POST",
            f"{base}/rooms",
            payload={
                "topic": "auto-e2e onboarding",
                "goal": "pick a practical outing plan and close automatically when fields are complete",
                "participants": ["host", "guest"],
                "required_fields": ["destination", "budget_cny", "vibe", "decision_summary"],
                "turn_limit": max(12, min_round_messages + 2),
                "timeout_minutes": 20,
                "stall_limit": max(4, min_round_messages),
                "metadata": {"source": "e2e_onboarding_autocheck"},
            },
        )
        log.append({"step": "create", "data": created})

        room_id = created["room"]["id"]
        host_token = created["host_token"]
        host_invite = created["invites"]["host"]
        guest_invite = created["invites"]["guest"]

        host = Bot(name="host", token=host_invite)
        guest = Bot(name="guest", token=guest_invite)
        bots = [host, guest]

        req(client, "POST", f"{base}/rooms/{room_id}/join", headers={"X-Invite-Token": host.token}, payload={"client_name": "auto-host"})
        req(client, "POST", f"{base}/rooms/{room_id}/join", headers={"X-Invite-Token": guest.token}, payload={"client_name": "auto-guest"})
        log.append({"step": "join", "room_id": room_id})

        # Guest-first kickoff mirrors the current desired UX policy.
        post_message(
            client,
            base,
            room_id,
            guest.token,
            intent="ASK",
            text="Let's choose tomorrow's outing. I suggest a lake walk, but I need one strong alternative and a clear budget target.",
            expect_reply=True,
            meta={"source": "e2e_onboarding_autocheck", "kickoff": "guest"},
        )
        guest.sent += 1

        last_progress_ts = time.time()
        total_msg_seen = 0
        seen_msg_ids: set[int] = set()

        while time.time() - started < max_seconds:
            room_status = "active"
            progressed = False

            for bot in bots:
                events, room = fetch_participant_events(client, base, room_id, bot)
                room_status = room.get("status", room_status)
                for evt in events:
                    if evt.get("type") != "relay":
                        continue
                    payload = evt.get("payload") or {}
                    incoming = (payload.get("message") or {}).get("text") or ""
                    intent, reply_text, fills, expect_reply = scripted_reply(bot, incoming)
                    try:
                        post_message(
                            client,
                            base,
                            room_id,
                            bot.token,
                            intent=intent,
                            text=reply_text,
                            expect_reply=expect_reply,
                            fills=fills,
                            meta={"source": "e2e_onboarding_autocheck"},
                        )
                    except RuntimeError as exc:
                        if "room not active" in str(exc).lower():
                            room_status = "closed"
                            break
                        raise
                    bot.sent += 1
                    progressed = True
                if room_status != "active":
                    break

            monitor_events, monitor_cursor, monitor_room = fetch_monitor_events(
                client, base, room_id, host_token, monitor_cursor
            )
            room_status = monitor_room.get("status", room_status)
            for evt in monitor_events:
                if evt.get("type") == "msg" and int(evt.get("id", 0)) not in seen_msg_ids:
                    seen_msg_ids.add(int(evt.get("id", 0)))
                    total_msg_seen += 1

            if progressed:
                last_progress_ts = time.time()

            if room_status != "active":
                break

            if time.time() - last_progress_ts > 8:
                break

            time.sleep(0.8)

        final_room = req(client, "GET", f"{base}/rooms/{room_id}", headers={"X-Invite-Token": host.token})["room"]
        final_events, _, _ = fetch_monitor_events(client, base, room_id, host_token, 0)
        msg_events = [e for e in final_events if e.get("type") == "msg"]

        # Cleanup to avoid leaving online participants hanging.
        try:
            req(
                client,
                "POST",
                f"{base}/rooms/{room_id}/leave",
                headers={"X-Invite-Token": host.token},
                payload={"reason": "e2e_done"},
            )
            req(
                client,
                "POST",
                f"{base}/rooms/{room_id}/leave",
                headers={"X-Invite-Token": guest.token},
                payload={"reason": "e2e_done"},
            )
        except Exception:
            pass

    host_sent = host.sent
    guest_sent = guest.sent
    stop_reason = str(final_room.get("stop_reason") or "")
    allowed_stop_reasons = {"goal_done", "mutual_done"}
    ok = (
        len(msg_events) >= min_round_messages
        and final_room.get("turn_count", 0) >= min_round_messages
        and final_room.get("status") == "closed"
        and stop_reason in allowed_stop_reasons
        and host_sent >= 3
        and guest_sent >= 3
    )
    return {
        "ok": ok,
        "room_id": room_id,
        "base": base,
        "msg_events": len(msg_events),
        "turn_count": final_room.get("turn_count"),
        "room_status": final_room.get("status"),
        "stop_reason": stop_reason,
        "participants": final_room.get("participants"),
        "bot_sent": {"host": host_sent, "guest": guest_sent},
        "log_tail": log[-3:],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated clawroom onboarding regression checker")
    parser.add_argument("--base-url", default="https://api.clawroom.cc", help="API base URL")
    parser.add_argument("--skill-path", default="skills/clawroom/SKILL.md", help="Path to skill contract file")
    parser.add_argument("--min-msg-events", type=int, default=6, help="Minimum msg events expected")
    parser.add_argument("--max-seconds", type=int, default=35, help="Timeout for loop check")
    args = parser.parse_args()

    skill_path = Path(args.skill_path)
    skill_contract = check_skill_contract(skill_path)
    loop_check = run_live_loop_check(
        base=args.base_url.rstrip("/"),
        min_round_messages=max(4, args.min_msg_events),
        max_seconds=max(15, args.max_seconds),
    )

    out = {
        "ok": bool(skill_contract.get("ok")) and bool(loop_check.get("ok")),
        "skill_contract": skill_contract,
        "loop_check": loop_check,
    }

    out_path = REPORT_DIR / "e2e_onboarding_autocheck.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    if not out["ok"]:
        raise SystemExit(1)
    print("e2e_onboarding_autocheck passed")


if __name__ == "__main__":
    main()
