#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "runnerd" / "src"))

from runnerd.models import WakePackage, render_wake_package  # noqa: E402


def _summarize_error_body(text: str) -> str:
    body = (text or "").strip()
    try:
        parsed = json.loads(body)
    except Exception:
        return body[:500]
    error = str(parsed.get("error") or "").strip()
    message = str(parsed.get("message") or "").strip()
    subsystem = str(parsed.get("subsystem") or "").strip()
    detail = str(parsed.get("detail") or "").strip()
    if error == "capacity_exhausted":
        parts = ["capacity_exhausted"]
        if subsystem:
            parts.append(subsystem)
        if detail:
            parts.append(detail)
        elif message:
            parts.append(message)
        return " | ".join(parts)
    return body[:500]


def create_room(
    *,
    base_url: str,
    topic: str,
    goal: str,
    required_fields: list[str],
    turn_limit: int,
    stall_limit: int,
    timeout_minutes: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "topic": topic,
        "goal": goal,
        "participants": ["host", "guest"],
        "turn_limit": turn_limit,
        "stall_limit": stall_limit,
        "timeout_minutes": timeout_minutes,
    }
    if required_fields:
        payload["required_fields"] = required_fields

    with httpx.Client(timeout=20.0, trust_env=False) as client:
        resp = client.post(f"{base_url}/rooms", json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"create room failed status={resp.status_code} body={_summarize_error_body(resp.text)}")
    return resp.json()


def scenario_defaults(scenario: str) -> tuple[str, str]:
    if scenario == "natural":
        return (
            "What to eat tonight",
            "Decide what to eat for dinner",
        )
    if scenario == "owner_escalation":
        return (
            "Choose tonight's dinner with one hidden owner preference",
            "Reach a dinner decision after exactly one owner-only clarification",
        )
    return (
        "ClawRoom Telegram Regression",
        "Complete a stable multi-turn discussion and auto-close",
    )


def build_wake_package_text(
    *,
    join_link: str,
    room_id: str,
    role: str,
    scenario: str,
    preferred_runner_kind: str,
    sender_owner_label: str,
    sender_gateway_label: str,
) -> str:
    if scenario == "natural":
        task_summary = "Join the room, keep the dialogue natural, and help reach a concrete dinner decision."
        owner_context = "Defaults already provided in the message; do not ask for extra preferences unless they are missing."
        expected_output = "Join successfully, continue the bounded work thread, and close the room once the decision is clear."
    elif scenario == "owner_escalation":
        task_summary = (
            "Join the room, keep the conversation natural, complete at least one normal in-room exchange first, "
            "then trigger exactly one owner-only clarification before the final dinner decision."
        )
        if role == "initiator":
            owner_context = (
                "Start with a normal in-room suggestion or question, wait for at least one real counterpart reply, and only then ask your owner one question "
                "about the hidden decision rule: should the final choice prioritize safer/classic food or the most exciting/special option? "
                "Do not ask the owner in the opening turn, do not guess, do not ask more than once, and once the owner answers, make the decision and close the room."
            )
        else:
            owner_context = (
                "Treat the initiator's owner as holding one hidden preference that may matter to the final choice. "
                "Do not ask your own owner unless the room becomes blocked for owner-only information."
            )
        expected_output = (
            "Join successfully, complete at least one normal in-room exchange, trigger exactly one owner-only clarification, "
            "then continue the work thread and close the room."
        )
    else:
        task_summary = "Join the room, keep the relay stable, and finish the regression conversation without self-echo."
        owner_context = "Defaults already provided in the message; do not ask for extra preferences unless they are missing."
        expected_output = "Join successfully, continue the bounded work thread, and close the room once the decision is clear."
    package = WakePackage(
        coordination_id=f"coord_{room_id}",
        wake_request_id=f"wake_{role}_{uuid.uuid4().hex[:10]}",
        room_id=room_id,
        join_link=join_link,
        role=role,  # type: ignore[arg-type]
        task_summary=task_summary,
        owner_context=owner_context,
        expected_output=expected_output,
        preferred_runner_kind=preferred_runner_kind,
        sender_owner_label=sender_owner_label,
        sender_gateway_label=sender_gateway_label,
    )
    return render_wake_package(package)


def build_join_prompt(
    join_link: str,
    *,
    room_id: str,
    role: str,
    scenario: str,
    runnerd_url: str = "http://127.0.0.1:8741",
    preferred_runner_kind: str = "openclaw_bridge",
    relay_agent_id: str = "",
    gateway_only: bool = False,
    prefer_shell_fallback: bool = False,
    strict_shell_probe: bool = False,
) -> str:
    if scenario == "natural":
        defaults_line = (
            "Defaults for this run: no extra preferences or deal-breakers. Make a concrete recommendation or ask one useful follow-up if needed, avoid quoting each other, and close the room once a clear decision is reached."
        )
    elif scenario == "owner_escalation":
        defaults_line = (
            "Defaults for this run: use the hidden owner-preference flow. One owner-only clarification is required before the final decision. "
            "Keep the conversation natural, do at least one normal in-room exchange before asking the owner, ask the owner only once, "
            "then close the room after the decision is clear."
        )
    else:
        defaults_line = (
            "Defaults for this regression test: no extra preferences or deal-breakers. Focus on continuous listening, natural dialogue, no self-echo, and automatic room closure."
        )
    wake_text = build_wake_package_text(
        join_link=join_link,
        room_id=room_id,
        role=role,
        scenario=scenario,
        preferred_runner_kind=preferred_runner_kind,
        sender_owner_label="telegram-owner",
        sender_gateway_label="telegram-openclaw",
    )
    if gateway_only:
        intro = (
            "Read https://clawroom.cc/skill.md first.\n"
            "Act as the gateway for this ClawRoom task.\n"
            f"Join link (context only; do not join directly): {join_link}\n\n"
        )
        guidance = (
            "A local helper or daemon will submit the wake package for you. Do not call /join yourself, do not start a shell keepalive, and do not invent a second worker path while the helper path is available.\n"
            f"Keep the wake package below unchanged so the owner or a local helper can hand it to runnerd with: python3 apps/runnerd/src/runnerd/submit_cli.py --runnerd-url {runnerd_url} --text-file /path/to/wake-package.txt --json.\n\n"
        )
        closing = (
            f"The preferred runner kind for this wake is `{preferred_runner_kind}`.\n"
            "If local runnerd is unavailable or rejects the wake, say so briefly instead of trying to join the room yourself.\n"
            "Reply with one concise gateway status update for the owner, then wait for the helper or runner to continue the work thread until the room is closed."
        )
    else:
        intro = (
            "Read https://clawroom.cc/skill.md first.\n"
            "Join this clawroom for me.\n"
            f"Join link: {join_link}\n\n"
        )
        guidance = (
            f"Preferred path: treat this chat as a gateway, not the long-running worker. Do not invent a background shell path if a local helper can submit the wake package. Keep the wake package below unchanged so the owner or a local helper can hand it to runnerd with: python3 apps/runnerd/src/runnerd/submit_cli.py --runnerd-url {runnerd_url} --text-file /path/to/wake-package.txt --json.\n\n"
        )
        shell_block = ""
        if prefer_shell_fallback:
            shell_cmd = build_probe_shell_command(join_link, role=role, relay_agent_id=relay_agent_id)
            if strict_shell_probe:
                shell_block = (
                    "Diagnostic mode: do not direct-join this room.\n"
                    "If no local helper or reliable runnerd submit surface is available in this runtime, either:\n"
                    "1. run the shell bridge candidate path below, or\n"
                    "2. reply briefly that shell execution is unavailable here.\n"
                    "Do not use API-first fallback in this diagnostic mode.\n\n"
                    f"Shell candidate path:\n`{shell_cmd}`\n\n"
                )
            else:
                shell_block = (
                    "If no local helper or reliable runnerd submit surface is available in this runtime, prefer the shell bridge candidate path below before direct API join.\n"
                    "Use it only if this runtime can execute shell commands locally.\n\n"
                    f"Shell candidate path:\n`{shell_cmd}`\n\n"
                )
        closing = (
            f"The preferred runner kind for this wake is `{preferred_runner_kind}`.\n"
            "If local runnerd is unavailable or rejects the wake, say so briefly instead of inventing a local worker path.\n"
            f"{shell_block}"
            + (
                ""
                if strict_shell_probe
                else "If reading the skill page is blocked, continue API-first: GET the join link for topic/goal, POST /rooms/{room_id}/join with X-Invite-Token, then keep polling /rooms/{room_id}/events until the room closes.\n"
            )
            + "Reply with one concise gateway status update for the owner, then wait for the helper or runner to continue the work thread until the room is closed."
        )

    return intro + f"{defaults_line}\n\n" + guidance + f"{wake_text}\n\n" + closing


def build_probe_shell_command(join_link: str, *, role: str, relay_agent_id: str = "") -> str:
    agent_arg = f" --agent-id {relay_agent_id.strip()}" if relay_agent_id.strip() else ""
    return (
        "curl -fsSL https://clawroom.cc/openclaw-shell-bridge.sh -o /tmp/openclaw-shell-bridge.sh"
        " && chmod +x /tmp/openclaw-shell-bridge.sh"
        f" && bash /tmp/openclaw-shell-bridge.sh \"{join_link}\" --role {role} --max-seconds 0 --heartbeat-seconds 5 --print-result --client-name TelegramShellBridge{agent_arg}"
    )


def build_fallback_keepalive_cmd(join_link: str, *, relay_agent_id: str = "") -> str:
    effective_agent_id = relay_agent_id.strip() or "clawroom-relay"
    agent_arg = f" --agent-id {effective_agent_id}"
    return (
        f'RELAY_AGENT_ID="{effective_agent_id}" && '
        'RELAY_WORKSPACE="${HOME}/.openclaw/workspaces/${RELAY_AGENT_ID}" && '
        'RELAY_RUNTIME_ROOT="${HOME}/.openclaw/clawroom-runtime/${RELAY_AGENT_ID}" && '
        'mkdir -p "$RELAY_WORKSPACE" && '
        'mkdir -p "$RELAY_RUNTIME_ROOT" && '
        'cat > "$RELAY_WORKSPACE/AGENTS.md" <<\'EOF\'\n'
        'You are a dedicated ClawRoom relay agent.\n'
        'Priorities:\n'
        '1. Follow the room-bridge prompt exactly.\n'
        '2. Keep replies concise and machine-readable.\n'
        '3. Avoid unrelated tool use or broad project exploration.\n'
        '4. Do not narrate platform internals unless the prompt explicitly asks.\n'
        'EOF\n'
        ': > "$RELAY_WORKSPACE/BOOTSTRAP.md" && '
        ': > "$RELAY_WORKSPACE/SOUL.md" && '
        ': > "$RELAY_WORKSPACE/TOOLS.md" && '
        ': > "$RELAY_WORKSPACE/USER.md" && '
        ': > "$RELAY_WORKSPACE/HEARTBEAT.md" && '
        'printf \'Relay\\n\' > "$RELAY_WORKSPACE/IDENTITY.md" && '
        '(openclaw agents list --json 2>/dev/null | grep -q "\\\"id\\\": \\\"${RELAY_AGENT_ID}\\\"" || '
        'openclaw agents add "$RELAY_AGENT_ID" --workspace "$RELAY_WORKSPACE" --non-interactive >/dev/null) && '
        '(openclaw gateway restart >/dev/null 2>&1 || true) && '
        'sleep 4 && '
        'READY=0 && '
        'for attempt in 1 2 3; do '
        'if openclaw agent --json --agent "$RELAY_AGENT_ID" --session-id "clawroom-relay-probe-${attempt}" --message "Reply with raw text OK." --timeout 20 --thinking minimal >/dev/null 2>&1; then '
        'READY=1; break; '
        'fi; '
        'sleep 5; '
        'done && '
        '[ "$READY" = "1" ] && '
        'LOG_FILE="${TMPDIR:-/tmp}/clawroom-runner-$(date +%s).log" && '
        "curl -fsSL https://clawroom.cc/openclaw-shell-bridge.sh -o /tmp/openclaw-shell-bridge.sh && "
        "chmod +x /tmp/openclaw-shell-bridge.sh && "
        'if command -v setsid >/dev/null 2>&1; then '
        f'CLAWROOM_RUNNER_LOG_REF="$LOG_FILE" CLAWROOM_RUNTIME_STATE_ROOT="$RELAY_RUNTIME_ROOT" CLAWROOM_MANAGED_CERTIFY=1 nohup setsid bash /tmp/openclaw-shell-bridge.sh "{join_link}" '
        f'--max-seconds 0 --poll-seconds 1 --heartbeat-seconds 5 --print-result{agent_arg} >"$LOG_FILE" 2>&1 < /dev/null & '
        'else '
        f'CLAWROOM_RUNNER_LOG_REF="$LOG_FILE" CLAWROOM_RUNTIME_STATE_ROOT="$RELAY_RUNTIME_ROOT" CLAWROOM_MANAGED_CERTIFY=1 nohup bash /tmp/openclaw-shell-bridge.sh "{join_link}" '
        f'--max-seconds 0 --poll-seconds 1 --heartbeat-seconds 5 --print-result{agent_arg} >"$LOG_FILE" 2>&1 < /dev/null & '
        'fi && '
        'PID=$! && sleep 6 && kill -0 "$PID" && echo "runner_log=$LOG_FILE pid=$PID"'
    )


def build_cron_fallback_note() -> str:
    return (
        "If shell runner cannot stay alive in this runtime, create a temporary OpenClaw cron keepalive job "
        "for this room and remove it automatically after room close."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an online ClawRoom and print Telegram-ready prompts.")
    parser.add_argument("--base-url", default="https://api.clawroom.cc")
    parser.add_argument("--ui-base", default="https://clawroom.cc")
    parser.add_argument("--scenario", choices=["regression", "natural", "owner_escalation"], default="regression")
    parser.add_argument("--topic", default=None)
    parser.add_argument(
        "--goal",
        default=None,
    )
    parser.add_argument("--required-field", action="append", default=[])
    parser.add_argument("--turn-limit", type=int, default=8)
    parser.add_argument("--stall-limit", type=int, default=6)
    parser.add_argument("--timeout-minutes", type=int, default=20)
    parser.add_argument("--host-relay-agent-id", default="")
    parser.add_argument("--guest-relay-agent-id", default="")
    parser.add_argument("--runnerd-url", default="http://127.0.0.1:8741")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    ui_base = args.ui_base.rstrip("/")
    default_topic, default_goal = scenario_defaults(args.scenario)
    topic = args.topic or default_topic
    goal = args.goal or default_goal

    body = create_room(
        base_url=base_url,
        topic=topic,
        goal=goal,
        required_fields=list(args.required_field or []),
        turn_limit=args.turn_limit,
        stall_limit=args.stall_limit,
        timeout_minutes=args.timeout_minutes,
    )

    room_id = body["room"]["id"]
    host_token = body["host_token"]
    host_inv = body["invites"]["host"]
    guest_inv = body["invites"]["guest"]

    host_join_link = f"{base_url}/join/{room_id}?token={host_inv}"
    guest_join_link = f"{base_url}/join/{room_id}?token={guest_inv}"
    watch_link = f"{ui_base}/?room_id={room_id}&host_token={host_token}"

    summary = {
        "room_id": room_id,
        "host_token": host_token,
        "host_invite_token": host_inv,
        "guest_invite_token": guest_inv,
        "host_join_link": host_join_link,
        "guest_join_link": guest_join_link,
        "watch_link": watch_link,
        "config": {
            "turn_limit": args.turn_limit,
            "stall_limit": args.stall_limit,
            "timeout_minutes": args.timeout_minutes,
            "required_fields": list(args.required_field or []),
            "scenario": args.scenario,
        },
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print()
    print("=== Telegram message for host ===")
    print(
        build_join_prompt(
            host_join_link,
            room_id=room_id,
            role="initiator",
            scenario=args.scenario,
            runnerd_url=args.runnerd_url,
            relay_agent_id=args.host_relay_agent_id,
        )
    )
    print("Optional fallback keepalive command (host; shell-first, run only if Telegram flow stalls):")
    print(build_fallback_keepalive_cmd(host_join_link, relay_agent_id=args.host_relay_agent_id))
    print("Secondary fallback (host):")
    print(build_cron_fallback_note())
    print()
    print("=== Telegram message for guest ===")
    print(
        build_join_prompt(
            guest_join_link,
            room_id=room_id,
            role="responder",
            scenario=args.scenario,
            runnerd_url=args.runnerd_url,
            relay_agent_id=args.guest_relay_agent_id,
        )
    )
    print("Optional fallback keepalive command (guest; shell-first, run only if Telegram flow stalls):")
    print(build_fallback_keepalive_cmd(guest_join_link, relay_agent_id=args.guest_relay_agent_id))
    print("Secondary fallback (guest):")
    print(build_cron_fallback_note())


if __name__ == "__main__":
    main()
