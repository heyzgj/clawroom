#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
FOUNDATION_CONTRACT_VERSION = "foundation-certified-v1"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT / "apps" / "runnerd" / "src"))

from create_telegram_test_room import build_join_prompt, build_wake_package_text, create_room, scenario_defaults
from runnerd.submit_cli import parse_package_input, submit_package
from telegram_desktop import send_sequence
from validate_room_result import detect_echo_loop, detect_meta_language, fetch_result, fetch_room_snapshot


def choose_runnerd_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", int(preferred)))
            return int(preferred)
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_runnerd_health(*, runnerd_url: str, timeout_seconds: int) -> None:
    deadline = time.time() + max(5, timeout_seconds)
    last_error = "runnerd never answered /healthz"
    while time.time() < deadline:
        try:
            with urlopen(f"{runnerd_url.rstrip('/')}/healthz", timeout=5) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
            if bool(body.get("ok")):
                return
            last_error = f"healthz body={body!r}"
        except (OSError, URLError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(last_error)


def get_runnerd_run(*, runnerd_url: str, run_id: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urlopen(f"{runnerd_url.rstrip('/')}/runs/{run_id}", timeout=10) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= 3:
                break
            time.sleep(float(attempt))
    assert last_error is not None
    raise last_error


def post_runnerd_owner_reply(
    *,
    runnerd_url: str,
    run_id: str,
    owner_request_id: str,
    text: str,
) -> dict[str, Any]:
    import httpx

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with httpx.Client(timeout=10.0, trust_env=False) as client:
                response = client.post(
                    f"{runnerd_url.rstrip('/')}/runs/{run_id}/owner-reply",
                    json={"text": text, "owner_request_id": owner_request_id},
                )
            response.raise_for_status()
            return dict(response.json())
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= 3:
                break
            time.sleep(float(attempt))
    assert last_error is not None
    raise last_error


def submit_wake_text(*, runnerd_url: str, wake_text: str) -> dict[str, Any]:
    package = parse_package_input(wake_text)
    return submit_package(runnerd_url=runnerd_url, package=package)


def maybe_submit_owner_replies(
    *,
    runnerd_url: str,
    submitted_run_ids: dict[str, str],
    owner_replied: set[str],
    owner_reply_text: str,
    state: dict[str, Any],
    artifact_path: Path | None = None,
) -> None:
    pending: dict[str, dict[str, Any]] = {}
    for participant, run_id in submitted_run_ids.items():
        if not run_id:
            continue
        try:
            run = get_runnerd_run(runnerd_url=runnerd_url, run_id=run_id)
        except Exception:  # noqa: BLE001
            continue
        pending[participant] = run
        owner_request = dict(run.get("pending_owner_request") or {})
        owner_request_id = str(owner_request.get("owner_request_id") or "").strip()
        key = f"{run_id}:{owner_request_id}"
        if not owner_request_id or key in owner_replied:
            continue
        post_runnerd_owner_reply(
            runnerd_url=runnerd_url,
            run_id=run_id,
            owner_request_id=owner_request_id,
            text=owner_reply_text,
        )
        owner_replied.add(key)
    if pending:
        state["runnerd_runs"] = pending
        if artifact_path is not None:
            write_artifact(artifact_path, state)


def refresh_runnerd_runs(
    *,
    runnerd_url: str,
    submitted_run_ids: dict[str, str],
    state: dict[str, Any],
    artifact_path: Path | None = None,
) -> None:
    refreshed: dict[str, dict[str, Any]] = {}
    for participant, run_id in submitted_run_ids.items():
        if not run_id:
            continue
        try:
            refreshed[participant] = get_runnerd_run(runnerd_url=runnerd_url, run_id=run_id)
        except Exception:  # noqa: BLE001
            existing = dict((state.get("runnerd_runs") or {}).get(participant) or {})
            if existing:
                refreshed[participant] = existing
    if refreshed:
        state["runnerd_runs"] = refreshed
        if artifact_path is not None:
            write_artifact(artifact_path, state)


def evaluate_result(
    *,
    result: dict[str, Any],
    min_turns: int,
    reject_meta_language: bool,
    allowed_stop: set[str],
) -> dict[str, Any]:
    status = str(result.get("status") or "")
    stop_reason = str(result.get("stop_reason") or "")
    turn_count = int(result.get("turn_count") or 0)
    transcript = list(result.get("transcript") or [])
    required_total = int(result.get("required_total") or 0)
    required_filled = int(result.get("required_filled") or 0)
    errors: list[str] = []
    warnings: list[str] = []

    if status != "closed":
        errors.append(f"room status is {status!r}, expected 'closed'")
    if status == "closed" and allowed_stop and stop_reason not in allowed_stop:
        errors.append(f"stop_reason={stop_reason!r} not in allowed set {sorted(allowed_stop)}")
    if required_total > 0 and required_filled < required_total:
        errors.append(f"required_filled={required_filled} < required_total={required_total}")
    if required_total == 0 and turn_count < min_turns:
        errors.append(f"turn_count={turn_count} < min_turns={min_turns}")
    if detect_echo_loop(transcript):
        errors.append("transcript matches self-echo/template-loop pattern")
    if reject_meta_language and detect_meta_language(transcript):
        errors.append("transcript contains too much platform-meta/test language")
    if not transcript:
        errors.append("empty transcript")
    if status == "closed" and not stop_reason:
        warnings.append("closed room has empty stop_reason")

    return {
        "pass": not errors,
        "status": status,
        "stop_reason": stop_reason or None,
        "turn_count": turn_count,
        "required_total": required_total,
        "required_filled": required_filled,
        "transcript_messages": len(transcript),
        "errors": errors,
        "warnings": warnings,
    }


def poll_for_room_close(
    *,
    base_url: str,
    room_id: str,
    token: str | None = None,
    host_token: str | None = None,
    timeout_seconds: int,
    poll_seconds: float,
    on_live_room: Callable[[dict[str, Any]], None] | None = None,
    on_result: Callable[[dict[str, Any]], None] | None = None,
    on_tick: Callable[[], None] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + max(5, timeout_seconds)
    last_payload: dict[str, Any] | None = None
    last_live_room: dict[str, Any] | None = None
    while time.time() < deadline:
        try:
            room = dict(fetch_room_snapshot(base_url=base_url, room_id=room_id, token=token, host_token=host_token) or {})
            room_status = str(room.get("status") or "")
            if room_status != "closed":
                last_live_room = room
                if on_live_room is not None:
                    on_live_room(room)
            else:
                for _ in range(3):
                    payload = fetch_result(base_url=base_url, room_id=room_id, token=token, host_token=host_token)
                    if on_result is not None:
                        on_result(dict(payload.get("result") or {}))
                    last_payload = payload
                    if str((payload.get("result") or {}).get("status") or "") == "closed":
                        return {
                            "result_payload": payload,
                            "last_live_room": last_live_room,
                        }
                    time.sleep(1.0)
        except Exception:
            pass
        if on_tick is not None:
            on_tick()
        time.sleep(max(1.0, poll_seconds))
    if last_payload is None:
        last_payload = fetch_result(base_url=base_url, room_id=room_id, token=token, host_token=host_token)
        if on_result is not None:
            on_result(dict(last_payload.get("result") or {}))
    if last_payload is not None:
        return {
            "result_payload": last_payload,
            "last_live_room": last_live_room,
        }
    raise RuntimeError("room polling ended without any result payload")


def update_state_from_live_room(state: dict[str, Any], room: dict[str, Any], artifact_path: Path | None = None) -> None:
    if not room:
        return
    execution_attention = dict(room.get("execution_attention") or {})
    state.update(
        {
            "last_live_room": room,
            "execution_mode": room.get("execution_mode"),
            "runner_certification": room.get("runner_certification"),
            "managed_coverage": room.get("managed_coverage"),
            "product_owned": room.get("product_owned"),
            "automatic_recovery_eligible": room.get("automatic_recovery_eligible"),
            "attempt_status": room.get("attempt_status"),
            "execution_attention_state": execution_attention.get("state"),
            "execution_attention_reasons": list(execution_attention.get("reasons") or []),
            "last_live_execution_mode": room.get("execution_mode"),
            "last_live_managed_coverage": room.get("managed_coverage"),
            "last_live_product_owned": room.get("product_owned"),
            "last_live_attempt_status": room.get("attempt_status"),
            "last_live_execution_attention_state": execution_attention.get("state"),
            "last_live_execution_attention_reasons": list(execution_attention.get("reasons") or []),
        }
    )
    if artifact_path is not None:
        write_artifact(artifact_path, state)


def update_state_from_result(state: dict[str, Any], result: dict[str, Any], artifact_path: Path | None = None) -> None:
    if not result:
        return
    state["last_result"] = dict(result)
    if artifact_path is not None:
        write_artifact(artifact_path, state)


def default_owner_reply_text(scenario: str) -> str:
    if scenario == "owner_escalation":
        return "Prioritize the safer, classic option over the more adventurous one, then close once the final choice is clear."
    return "Proceed with the safer option and close once the decision is clear."


def derive_path_family(
    *,
    scenario: str,
    host_bot: str,
    guest_bot: str,
    wait_after_new: float,
    submitted_run_ids: dict[str, str],
) -> str:
    helper_submitted = any(str(run_id or "").strip() for run_id in submitted_run_ids.values())
    if wait_after_new >= 30.0 and host_bot == "@singularitygz_bot" and guest_bot == "@link_clawd_bot":
        return "telegram_helper_submitted_runnerd_v1" if helper_submitted else "telegram_only_cross_owner_v1"
    if helper_submitted:
        return "telegram_helper_submitted_runnerd_v1"
    return ""


def build_log_entry(*, title: str, summary: dict[str, Any], prompt_pack_version: str, participants: str, learnings: list[str], follow_up: list[str]) -> str:
    lines = [
        f"## {time.strftime('%Y-%m-%d')} - {title}",
        f"- room_id: `{summary['room_id']}`",
        f"- watch_link: [{summary['room_id']}]({summary['watch_link']})",
        f"- participants: `{participants}`",
        f"- path_family: `{summary.get('path_family') or 'unclassified'}`",
        f"- prompt_pack_version: {prompt_pack_version}",
        "- result:",
        f"  - status: `{summary['status']}`",
        f"  - stop_reason: `{summary.get('stop_reason') or ''}`",
        f"  - turn_count: `{summary['turn_count']}`",
        f"  - execution: `{summary.get('execution_mode') or 'unknown'}` / `{summary.get('attempt_status') or 'unknown'}` / `{summary.get('execution_attention_state') or 'unknown'}`",
        f"  - ownership: `{summary.get('managed_coverage') or 'unknown'}` managed / `{'product-owned' if summary.get('product_owned') else 'not product-owned'}`",
        f"  - primary_root_cause: `{summary.get('primary_root_cause_code') or 'none'}` / `{summary.get('primary_root_cause_confidence') or 'none'}`",
        f"  - validator: `{'pass' if summary['pass'] else 'fail'}`",
        "- outcome:",
        f"  - pass/fail: `{'pass' if summary['pass'] else 'fail'}`",
        f"  - clean/no-manual-rescue: `{'yes' if summary['pass'] else 'no'}`",
        "- learnings:",
    ]
    for item in learnings:
        lines.append(f"  - {item}")
    lines.append("- follow-up:")
    for item in follow_up:
        lines.append(f"  - {item}")
    return "\n".join(lines) + "\n"


def append_markdown_log(path: Path, entry: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix = "\n" if existing and not existing.endswith("\n\n") else ""
    path.write_text(existing + prefix + entry + "\n", encoding="utf-8")


def classify_outcome(summary: dict[str, Any]) -> str:
    if bool(summary.get("pass")):
        return "success"
    if bool(summary.get("infra_blocked")):
        return "infrastructure_blocked"
    attention_state = str(summary.get("execution_attention_state") or "")
    reasons = {str(item) for item in list(summary.get("execution_attention_reasons") or [])}
    if attention_state == "takeover_required" or bool(reasons):
        return "takeover_required"
    return "failed_unclassified"


def detect_silent_failure(summary: dict[str, Any]) -> bool:
    if bool(summary.get("pass")):
        return False
    if bool(summary.get("infra_blocked")):
        return False
    status = str(summary.get("status") or "")
    attention_state = str(summary.get("execution_attention_state") or "")
    reasons = list(summary.get("execution_attention_reasons") or [])
    root_cause_hints = list(summary.get("root_cause_hints") or [])
    return status != "closed" and attention_state in {"", "healthy"} and not reasons and not root_cause_hints


def build_history_record(
    *,
    summary: dict[str, Any],
    scenario: str,
    host_bot: str,
    guest_bot: str,
    wait_after_new: float,
    submitted_run_ids: dict[str, str],
) -> dict[str, Any]:
    submitted_participants = [
        participant for participant, run_id in submitted_run_ids.items() if str(run_id or "").strip()
    ]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario,
        "foundation_contract_version": FOUNDATION_CONTRACT_VERSION,
        "path_family": derive_path_family(
            scenario=scenario,
            host_bot=host_bot,
            guest_bot=guest_bot,
            wait_after_new=wait_after_new,
            submitted_run_ids=submitted_run_ids,
        ),
        "room_id": summary.get("room_id"),
        "watch_link": summary.get("watch_link"),
        "host_bot": host_bot,
        "guest_bot": guest_bot,
        "wait_after_new_seconds": wait_after_new,
        "helper_submitted_participants": submitted_participants,
        "pass": bool(summary.get("pass")),
        "outcome_class": classify_outcome(summary),
        "infra_blocked": bool(summary.get("infra_blocked")),
        "silent_failure": detect_silent_failure(summary),
        "status": summary.get("status"),
        "stop_reason": summary.get("stop_reason"),
        "turn_count": summary.get("turn_count"),
        "execution_mode": summary.get("execution_mode"),
        "runner_certification": summary.get("runner_certification"),
        "managed_coverage": summary.get("managed_coverage"),
        "product_owned": summary.get("product_owned"),
        "automatic_recovery_eligible": bool(summary.get("automatic_recovery_eligible")),
        "start_slo": dict(summary.get("start_slo") or {}),
        "last_live_execution_mode": summary.get("last_live_execution_mode"),
        "last_live_managed_coverage": summary.get("last_live_managed_coverage"),
        "last_live_product_owned": summary.get("last_live_product_owned"),
        "attempt_status": summary.get("attempt_status"),
        "execution_attention_state": summary.get("execution_attention_state"),
        "execution_attention_reasons": list(summary.get("execution_attention_reasons") or []),
        "primary_root_cause_code": summary.get("primary_root_cause_code"),
        "primary_root_cause_confidence": summary.get("primary_root_cause_confidence"),
        "errors": list(summary.get("errors") or []),
        "warnings": list(summary.get("warnings") or []),
    }


def append_history_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a real ClawRoom, send serial Telegram prompts, wait for closure, and validate the run.")
    parser.add_argument("--base-url", default="https://api.clawroom.cc")
    parser.add_argument("--ui-base", default="https://clawroom.cc")
    parser.add_argument("--scenario", choices=["regression", "natural", "owner_escalation"], default="natural")
    parser.add_argument("--topic", default=None)
    parser.add_argument("--goal", default=None)
    parser.add_argument("--required-field", action="append", default=[])
    parser.add_argument("--turn-limit", type=int, default=8)
    parser.add_argument("--stall-limit", type=int, default=6)
    parser.add_argument("--timeout-minutes", type=int, default=20)
    parser.add_argument("--host-bot", required=True)
    parser.add_argument("--guest-bot", required=True)
    parser.add_argument("--host-runner-kind", choices=["openclaw_bridge", "codex_bridge"], default="openclaw_bridge")
    parser.add_argument("--guest-runner-kind", choices=["openclaw_bridge", "codex_bridge"], default="codex_bridge")
    parser.add_argument("--host-relay-agent-id", default="")
    parser.add_argument("--guest-relay-agent-id", default="")
    parser.add_argument("--wait-after-open", type=float, default=1.2)
    parser.add_argument("--wait-after-new", type=float, default=30.0)
    parser.add_argument("--runnerd-url", default="http://127.0.0.1:8741")
    parser.add_argument("--runnerd-start", action="store_true")
    parser.add_argument("--runnerd-port", type=int, default=8741)
    parser.add_argument("--submit-host-wake-local", action="store_true")
    parser.add_argument("--submit-guest-wake-local", action="store_true")
    parser.add_argument("--owner-reply-text", default="")
    parser.add_argument("--between-participants-delay", type=float, default=2.0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--room-timeout-seconds", type=int, default=600)
    parser.add_argument("--min-turns", type=int, default=4)
    parser.add_argument("--reject-meta-language", action="store_true")
    parser.add_argument("--expect-execution-mode", default="", help="Optional expected room execution mode, e.g. managed_attached")
    parser.add_argument("--log-path", default=str(REPO_ROOT / "docs" / "progress" / "TELEGRAM_E2E_LOG.md"))
    parser.add_argument("--history-path", default=str(REPO_ROOT / "docs" / "progress" / "TELEGRAM_E2E_HISTORY.jsonl"))
    parser.add_argument("--artifact-path", default=str(REPO_ROOT / ".tmp" / "telegram_e2e_latest.json"))
    parser.add_argument("--skip-log", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    topic_default, goal_default = scenario_defaults(args.scenario)
    topic = args.topic or topic_default
    goal = args.goal or goal_default
    owner_reply_text = str(args.owner_reply_text or "").strip() or default_owner_reply_text(args.scenario)
    artifact_path = Path(args.artifact_path)
    runnerd_proc: subprocess.Popen[str] | None = None
    runnerd_url = args.runnerd_url.rstrip("/")
    state: dict[str, Any] = {
        "phase": "starting",
        "scenario": args.scenario,
        "host_bot": args.host_bot,
        "guest_bot": args.guest_bot,
        "wait_after_new": args.wait_after_new,
        "between_participants_delay": args.between_participants_delay,
        "runnerd_url": runnerd_url,
    }

    try:
        if args.runnerd_start:
            selected_port = choose_runnerd_port(args.runnerd_port)
            runnerd_url = f"http://127.0.0.1:{selected_port}"
            state["runnerd_url"] = runnerd_url
            runnerd_proc = subprocess.Popen(
                [
                    sys.executable,
                    str(REPO_ROOT / "apps" / "runnerd" / "src" / "runnerd" / "cli.py"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(selected_port),
                ],
                cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            wait_for_runnerd_health(runnerd_url=runnerd_url, timeout_seconds=15)

        body = create_room(
            base_url=args.base_url.rstrip("/"),
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
        host_join_link = f"{args.base_url.rstrip('/')}/join/{room_id}?token={host_inv}"
        guest_join_link = f"{args.base_url.rstrip('/')}/join/{room_id}?token={guest_inv}"
        watch_link = f"{args.ui_base.rstrip('/')}/?room_id={room_id}&host_token={host_token}"
        host_wake_text = build_wake_package_text(
            join_link=host_join_link,
            room_id=room_id,
            role="initiator",
            scenario=args.scenario,
            preferred_runner_kind=args.host_runner_kind,
            sender_owner_label="telegram-owner",
            sender_gateway_label="telegram-openclaw",
        )
        guest_wake_text = build_wake_package_text(
            join_link=guest_join_link,
            room_id=room_id,
            role="responder",
            scenario=args.scenario,
            preferred_runner_kind=args.guest_runner_kind,
            sender_owner_label="telegram-owner",
            sender_gateway_label="telegram-openclaw",
        )
        host_prompt = build_join_prompt(
            host_join_link,
            room_id=room_id,
            role="initiator",
            scenario=args.scenario,
            runnerd_url=runnerd_url,
            preferred_runner_kind=args.host_runner_kind,
            relay_agent_id=args.host_relay_agent_id,
            gateway_only=bool(args.submit_host_wake_local),
        )
        guest_prompt = build_join_prompt(
            guest_join_link,
            room_id=room_id,
            role="responder",
            scenario=args.scenario,
            runnerd_url=runnerd_url,
            preferred_runner_kind=args.guest_runner_kind,
            relay_agent_id=args.guest_relay_agent_id,
            gateway_only=bool(args.submit_guest_wake_local),
        )
        host_wake_path = artifact_path.with_name(f"{room_id}_host_wake_package.txt")
        guest_wake_path = artifact_path.with_name(f"{room_id}_guest_wake_package.txt")
        host_wake_path.write_text(host_wake_text + "\n", encoding="utf-8")
        guest_wake_path.write_text(guest_wake_text + "\n", encoding="utf-8")

        state.update(
            {
                "phase": "created",
                "room_id": room_id,
                "watch_link": watch_link,
                "host_token": host_token,
                "host_invite_token": host_inv,
                "guest_invite_token": guest_inv,
                "topic": topic,
                "goal": goal,
                "runnerd_url": runnerd_url,
                "host_wake_path": str(host_wake_path),
                "guest_wake_path": str(guest_wake_path),
            }
        )
        write_artifact(artifact_path, state)
        print(json.dumps(state, ensure_ascii=False), flush=True)

        if args.dry_run:
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return

        send_sequence(
            bot_target=args.host_bot,
            text=host_prompt,
            reset_session=True,
            wait_after_open=args.wait_after_open,
            wait_after_new=args.wait_after_new,
        )
        state["phase"] = "host_sent"
        write_artifact(artifact_path, state)
        print(json.dumps({"phase": "host_sent", "room_id": room_id}, ensure_ascii=False), flush=True)
        time.sleep(max(0.5, args.between_participants_delay))
        send_sequence(
            bot_target=args.guest_bot,
            text=guest_prompt,
            reset_session=True,
            wait_after_open=args.wait_after_open,
            wait_after_new=args.wait_after_new,
        )
        state["phase"] = "guest_sent"
        write_artifact(artifact_path, state)
        print(json.dumps({"phase": "guest_sent", "room_id": room_id}, ensure_ascii=False), flush=True)

        submitted_run_ids: dict[str, str] = {}
        if args.submit_host_wake_local:
            host_submit = submit_wake_text(runnerd_url=runnerd_url, wake_text=host_wake_text)
            submitted_run_ids["host"] = str(host_submit.get("run_id") or "")
        if args.submit_guest_wake_local:
            guest_submit = submit_wake_text(runnerd_url=runnerd_url, wake_text=guest_wake_text)
            submitted_run_ids["guest"] = str(guest_submit.get("run_id") or "")
        if submitted_run_ids:
            state["submitted_run_ids"] = submitted_run_ids
            state["phase"] = "runnerd_submitted"
            write_artifact(artifact_path, state)
            print(json.dumps({"phase": "runnerd_submitted", "run_ids": submitted_run_ids}, ensure_ascii=False), flush=True)

        owner_replied: set[str] = set()
        poll = poll_for_room_close(
            base_url=args.base_url.rstrip("/"),
            room_id=room_id,
            token=host_inv,
            host_token=host_token,
            timeout_seconds=args.room_timeout_seconds,
            poll_seconds=args.poll_seconds,
            on_live_room=lambda room: update_state_from_live_room(state, room, artifact_path),
            on_result=lambda result: update_state_from_result(state, result, artifact_path),
            on_tick=lambda: maybe_submit_owner_replies(
                runnerd_url=runnerd_url,
                submitted_run_ids=submitted_run_ids,
                owner_replied=owner_replied,
                owner_reply_text=owner_reply_text,
                state=state,
                artifact_path=artifact_path,
            ),
        )
        payload = dict(poll.get("result_payload") or {})
        last_live_room = dict(poll.get("last_live_room") or {})
        room_snapshot = fetch_room_snapshot(
            base_url=args.base_url.rstrip("/"),
            room_id=room_id,
            token=host_inv,
            host_token=host_token,
        )
        refresh_runnerd_runs(
            runnerd_url=runnerd_url,
            submitted_run_ids=submitted_run_ids,
            state=state,
            artifact_path=artifact_path,
        )
        update_state_from_live_room(state, last_live_room, artifact_path)
        update_state_from_live_room(state, room_snapshot, artifact_path)
        result = dict(payload.get("result") or {})
        result_root_cause_hints = list(result.get("root_cause_hints") or [])
        primary_root_cause = dict(result_root_cause_hints[0] or {}) if result_root_cause_hints else {}
        evaluation = evaluate_result(
            result=result,
            min_turns=args.min_turns,
            reject_meta_language=args.reject_meta_language,
            allowed_stop={"goal_done", "mutual_done", "turn_limit", "timeout"},
        )
        summary = {
            **evaluation,
            "room_id": room_id,
            "watch_link": watch_link,
            "host_token": host_token,
            "host_invite_token": host_inv,
            "guest_invite_token": guest_inv,
            "topic": topic,
            "goal": goal,
            "execution_mode": room_snapshot.get("execution_mode"),
            "runner_certification": room_snapshot.get("runner_certification"),
            "managed_coverage": room_snapshot.get("managed_coverage"),
            "product_owned": room_snapshot.get("product_owned"),
            "automatic_recovery_eligible": bool(room_snapshot.get("automatic_recovery_eligible")),
            "attempt_status": room_snapshot.get("attempt_status"),
            "execution_attention_state": (room_snapshot.get("execution_attention") or {}).get("state"),
            "execution_attention_reasons": list((room_snapshot.get("execution_attention") or {}).get("reasons") or []),
            "start_slo": dict(room_snapshot.get("start_slo") or {}),
            "root_cause_hints": result_root_cause_hints,
            "primary_root_cause_code": primary_root_cause.get("code"),
            "primary_root_cause_confidence": primary_root_cause.get("confidence"),
            "primary_root_cause_summary": primary_root_cause.get("summary"),
            "last_live_execution_mode": last_live_room.get("execution_mode"),
            "last_live_managed_coverage": last_live_room.get("managed_coverage"),
            "last_live_product_owned": last_live_room.get("product_owned"),
            "last_live_attempt_status": last_live_room.get("attempt_status"),
            "last_live_execution_attention_state": (last_live_room.get("execution_attention") or {}).get("state"),
            "last_live_execution_attention_reasons": list((last_live_room.get("execution_attention") or {}).get("reasons") or []),
            "submitted_run_ids": dict(submitted_run_ids),
            "owner_reply_count": len(owner_replied),
            "runnerd_runs": dict(state.get("runnerd_runs") or {}),
        }
        summary["path_family"] = derive_path_family(
            scenario=args.scenario,
            host_bot=args.host_bot,
            guest_bot=args.guest_bot,
            wait_after_new=max(0.3, args.wait_after_new),
            submitted_run_ids=submitted_run_ids,
        )
        expected_execution_mode = str(args.expect_execution_mode or "").strip()
        if expected_execution_mode and str(summary.get("execution_mode") or "") != expected_execution_mode:
            summary["pass"] = False
            summary.setdefault("errors", []).append(
                f"execution_mode={summary.get('execution_mode')!r} != expected {expected_execution_mode!r}"
            )
        state.update({"phase": "completed", **summary})
        write_artifact(artifact_path, state)
        append_history_jsonl(
            Path(args.history_path),
            build_history_record(
                summary=summary,
                scenario=args.scenario,
                host_bot=args.host_bot,
                guest_bot=args.guest_bot,
                wait_after_new=max(0.3, args.wait_after_new),
                submitted_run_ids=submitted_run_ids,
            ),
        )

        if not args.skip_log:
            learnings = [
                f"Serial Telegram send path used `/new` with a hardened double-enter sequence and a {max(0.3, args.wait_after_new):.1f}s wait before the real prompt.",
                f"Scenario `{args.scenario}` finished with stop_reason `{evaluation.get('stop_reason') or 'unknown'}` after {evaluation['turn_count']} turns.",
                f"Execution path ended as `{summary.get('execution_mode') or 'unknown'}` / `{summary.get('attempt_status') or 'unknown'}` / `{summary.get('execution_attention_state') or 'unknown'}`.",
            ]
            if submitted_run_ids:
                learnings.append(
            "Local helper submitted wake packages to runnerd for "
                    + ", ".join(f"{participant}={run_id}" for participant, run_id in submitted_run_ids.items() if run_id)
                    + "."
                )
            if owner_replied:
                learnings.append(f"Automatic helper owner replies submitted: {len(owner_replied)}.")
            learnings.append(
                "Managed coverage ended as "
                f"`{summary.get('managed_coverage') or 'unknown'}` with product-owned="
                f"`{str(bool(summary.get('product_owned'))).lower()}`."
            )
            start_slo = dict(summary.get("start_slo") or {})
            if start_slo:
                learnings.append(
                    "Start-SLO snapshot was "
                    f"first_join={start_slo.get('join_latency_ms')}ms, "
                    f"all_joined={start_slo.get('full_join_latency_ms')}ms, "
                    f"first_relay={start_slo.get('first_relay_latency_ms')}ms."
                )
            if summary.get("runner_certification"):
                learnings.append(
                    "Runner certification ended as "
                    f"`{summary.get('runner_certification')}` with auto-recovery="
                    f"`{str(bool(summary.get('automatic_recovery_eligible'))).lower()}`."
                )
            if summary.get("last_live_execution_mode") or summary.get("last_live_execution_attention_reasons"):
                learnings.append(
                    "Last live snapshot before closure was "
                    f"`{summary.get('last_live_execution_mode') or 'unknown'}` / "
                    f"`{summary.get('last_live_managed_coverage') or 'unknown'}` / "
                    f"`{str(bool(summary.get('last_live_product_owned'))).lower()}` / "
                    f"`{summary.get('last_live_attempt_status') or 'unknown'}` / "
                    f"`{summary.get('last_live_execution_attention_state') or 'unknown'}` "
                    f"with reasons {summary.get('last_live_execution_attention_reasons') or []}."
                )
            if summary.get("primary_root_cause_code"):
                learnings.append(
                    "Primary root-cause hint after closure was "
                    f"`{summary.get('primary_root_cause_code')}` / `{summary.get('primary_root_cause_confidence') or 'unknown'}`: "
                    f"{summary.get('primary_root_cause_summary') or ''}"
                )
            if evaluation["errors"]:
                learnings.append(f"Validator errors: {'; '.join(evaluation['errors'])}")
            else:
                learnings.append("Validator passed without manual rescue.")
            follow_up = [
                "Inspect the watch link if latency or transcript quality still feels off.",
                "Keep appending learnings so each Telegram regression leaves an operator-readable trail.",
            ]
            entry = build_log_entry(
                title=f"{args.scenario.title()} Scenario (serial Telegram runner)",
                summary=summary,
                prompt_pack_version="serial runner with /new double-enter + 30s wait",
                participants=f"{args.host_bot} host + {args.guest_bot} guest",
                learnings=learnings,
                follow_up=follow_up,
            )
            append_markdown_log(Path(args.log_path), entry)

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not evaluation["pass"]:
            raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)
        infra_blocked = "capacity_exhausted" in error_text
        state["phase"] = "failed"
        state["error"] = error_text
        state["infra_blocked"] = infra_blocked
        if state.get("room_id") and state.get("host_invite_token"):
            try:
                payload = fetch_result(
                    base_url=args.base_url.rstrip("/"),
                    room_id=str(state["room_id"]),
                    token=str(state["host_invite_token"]),
                    host_token=str(state.get("host_token") or ""),
                )
                update_state_from_result(state, dict(payload.get("result") or {}), artifact_path)
            except Exception:
                pass
        if state.get("room_id"):
            try:
                room = fetch_room_snapshot(
                    base_url=args.base_url.rstrip("/"),
                    room_id=str(state["room_id"]),
                    token=str(state.get("host_invite_token") or ""),
                    host_token=str(state.get("host_token") or ""),
                )
                update_state_from_live_room(state, room, artifact_path)
            except Exception:
                pass
        failure_summary = {
            "room_id": str(state.get("room_id") or ""),
            "watch_link": str(state.get("watch_link") or ""),
            "status": str((state.get("last_result") or {}).get("status") or "unknown"),
            "stop_reason": (state.get("last_result") or {}).get("stop_reason"),
            "turn_count": int((state.get("last_result") or {}).get("turn_count") or 0),
            "execution_mode": state.get("execution_mode"),
            "runner_certification": state.get("runner_certification"),
            "managed_coverage": state.get("managed_coverage"),
            "product_owned": state.get("product_owned"),
            "automatic_recovery_eligible": bool(state.get("automatic_recovery_eligible")),
            "start_slo": dict(state.get("last_live_room", {}).get("start_slo") or {}),
            "attempt_status": state.get("attempt_status"),
            "execution_attention_state": state.get("execution_attention_state"),
            "execution_attention_reasons": list(state.get("execution_attention_reasons") or []),
            "last_live_execution_mode": state.get("last_live_execution_mode"),
            "last_live_managed_coverage": state.get("last_live_managed_coverage"),
            "last_live_product_owned": state.get("last_live_product_owned"),
            "last_live_attempt_status": state.get("last_live_attempt_status"),
            "last_live_execution_attention_state": state.get("last_live_execution_attention_state"),
            "last_live_execution_attention_reasons": list(state.get("last_live_execution_attention_reasons") or []),
            "root_cause_hints": list((state.get("last_result") or {}).get("root_cause_hints") or []),
            "primary_root_cause_code": None,
            "primary_root_cause_confidence": None,
            "pass": False,
            "infra_blocked": infra_blocked,
            "errors": [error_text],
            "warnings": [],
        }
        failure_summary["path_family"] = derive_path_family(
            scenario=args.scenario,
            host_bot=args.host_bot,
            guest_bot=args.guest_bot,
            wait_after_new=max(0.3, args.wait_after_new),
            submitted_run_ids=dict(state.get("submitted_run_ids") or {}),
        )
        if failure_summary["root_cause_hints"]:
            primary_root = dict(failure_summary["root_cause_hints"][0] or {})
            failure_summary["primary_root_cause_code"] = primary_root.get("code")
            failure_summary["primary_root_cause_confidence"] = primary_root.get("confidence")
        append_history_jsonl(
            Path(args.history_path),
            build_history_record(
                summary=failure_summary,
                scenario=args.scenario,
                host_bot=args.host_bot,
                guest_bot=args.guest_bot,
                wait_after_new=max(0.3, args.wait_after_new),
                submitted_run_ids=dict(state.get("submitted_run_ids") or {}),
            ),
        )
        if not args.skip_log:
            entry = build_log_entry(
                title=f"{args.scenario.title()} Scenario (serial Telegram runner, diagnostic fail)",
                summary=failure_summary,
                prompt_pack_version="serial runner with /new double-enter + 30s wait",
                participants=f"{args.host_bot} host + {args.guest_bot} guest",
                learnings=[
                    f"Runner failed before completion: {error_text}",
                    f"Serial Telegram send still enforced a {max(0.3, args.wait_after_new):.1f}s wait after `/new`.",
                    "Failure was classified as infrastructure-blocked." if infra_blocked else "Failure happened after the room workflow had already started.",
                ],
                follow_up=[
                    "Inspect the saved artifact and watch link before re-running.",
                    "Turn the failure into a durable lesson if root-cause analysis takes more than 30 minutes.",
                ],
            )
            append_markdown_log(Path(args.log_path), entry)
        write_artifact(artifact_path, state)
        print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
        raise
    finally:
        if runnerd_proc is not None:
            runnerd_proc.terminate()
            try:
                runnerd_proc.wait(timeout=5)
            except Exception:
                runnerd_proc.kill()


if __name__ == "__main__":
    main()
