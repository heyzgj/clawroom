#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
VALIDATE_DIR = ROOT / "skills" / "openclaw-telegram-e2e" / "scripts"
if str(VALIDATE_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATE_DIR))

from validate_room_result import detect_echo_loop, detect_meta_language, fetch_result  # noqa: E402


DEFAULT_BASE_URL = "https://api.clawroom.cc"
DEFAULT_UI_BASE = "https://clawroom.cc"
DEFAULT_HISTORY_PATH = ROOT / "docs" / "progress" / "TELEGRAM_E2E_HISTORY.jsonl"
DEFAULT_LOG_PATH = ROOT / "docs" / "progress" / "CERTIFIED_E2E_LOG.md"
FOUNDATION_CONTRACT_VERSION = "foundation-certified-v1"
PATH_FAMILY = "bridge_pair_direct_v1"


def create_room(*, base_url: str, run_label: str, turn_limit: int, timeout_minutes: int) -> dict[str, Any]:
    payload = {
        "topic": f"Choose a launch checklist owner ({run_label})",
        "goal": "Agree who will own the final launch checklist and what the next step is",
        "expected_outcomes": ["owner", "next_step"],
        "participants": ["host", "guest"],
        "turn_limit": turn_limit,
        "stall_limit": 6,
        "timeout_minutes": timeout_minutes,
    }
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        resp = client.post(f"{base_url}/rooms", json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"create room failed status={resp.status_code} body={resp.text[:500]}")
    return resp.json()


def poll_until_closed(
    *,
    base_url: str,
    room_id: str,
    host_token: str,
    timeout_seconds: int,
    poll_seconds: float,
) -> dict[str, Any]:
    deadline = time.time() + max(15, timeout_seconds)
    last_payload: dict[str, Any] | None = None
    while time.time() < deadline:
        payload = fetch_result(base_url=base_url, room_id=room_id, host_token=host_token)
        last_payload = payload
        result = dict(payload.get("result") or {})
        if str(result.get("status") or "") == "closed":
            return payload
        time.sleep(max(1.0, poll_seconds))
    if last_payload is not None:
        return last_payload
    raise RuntimeError("room polling ended without any result payload")


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
    if bool(summary.get("pass")) or bool(summary.get("infra_blocked")):
        return False
    status = str(summary.get("status") or "")
    attention_state = str(summary.get("execution_attention_state") or "")
    reasons = list(summary.get("execution_attention_reasons") or [])
    root_cause_hints = list(summary.get("root_cause_hints") or [])
    return status != "closed" and attention_state in {"", "healthy"} and not reasons and not root_cause_hints


def evaluate_result(
    *,
    payload: dict[str, Any],
    room_id: str,
    watch_link: str,
    host_runtime: str,
    guest_runtime: str,
) -> dict[str, Any]:
    result = dict(payload.get("result") or {})
    room = dict(payload.get("room") or {})
    transcript = list(result.get("transcript") or [])
    errors: list[str] = []
    warnings: list[str] = []

    status = str(result.get("status") or "")
    stop_reason = str(result.get("stop_reason") or "")
    turn_count = int(result.get("turn_count") or 0)

    if status != "closed":
        errors.append(f"room status is {status!r}, expected 'closed'")
    if stop_reason not in {"goal_done", "mutual_done", "turn_limit", "timeout"}:
        errors.append(f"stop_reason={stop_reason!r} not in allowed set ['goal_done', 'mutual_done', 'turn_limit', 'timeout']")
    if turn_count < 2:
        errors.append(f"turn_count={turn_count} < min_turns=2")
    if detect_echo_loop(transcript):
        errors.append("transcript matches self-echo/template-loop pattern")
    if detect_meta_language(transcript):
        warnings.append("transcript contains platform-meta/test language")
    if not bool(result.get("product_owned")):
        errors.append("result.product_owned is false")
    if str(result.get("runner_certification") or "") != "certified":
        errors.append(f"runner_certification={result.get('runner_certification')!r} is not 'certified'")
    if not bool(result.get("automatic_recovery_eligible")):
        errors.append("automatic_recovery_eligible is false")

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenario": "certified_local",
        "room_id": room_id,
        "watch_link": watch_link,
        "host_bot": host_runtime,
        "guest_bot": guest_runtime,
        "wait_after_new_seconds": 0.0,
        "pass": not errors,
        "outcome_class": "",
        "infra_blocked": False,
        "silent_failure": False,
        "status": status,
        "stop_reason": stop_reason or None,
        "turn_count": turn_count,
        "execution_mode": result.get("execution_mode"),
        "runner_certification": result.get("runner_certification"),
        "managed_coverage": result.get("managed_coverage"),
        "product_owned": result.get("product_owned"),
        "automatic_recovery_eligible": bool(result.get("automatic_recovery_eligible")),
        "foundation_contract_version": FOUNDATION_CONTRACT_VERSION,
        "path_family": PATH_FAMILY,
        "start_slo": dict(result.get("start_slo") or room.get("start_slo") or {}),
        "last_live_execution_mode": room.get("execution_mode"),
        "last_live_managed_coverage": room.get("managed_coverage"),
        "last_live_product_owned": room.get("product_owned"),
        "attempt_status": result.get("attempt_status") or room.get("attempt_status"),
        "execution_attention_state": ((result.get("execution_attention") or {}).get("state")),
        "execution_attention_reasons": list((result.get("execution_attention") or {}).get("reasons") or []),
        "primary_root_cause_code": ((result.get("root_cause_hints") or [{}])[0].get("code") if result.get("root_cause_hints") else None),
        "primary_root_cause_confidence": ((result.get("root_cause_hints") or [{}])[0].get("confidence") if result.get("root_cause_hints") else None),
        "errors": errors,
        "warnings": warnings,
    }
    summary["outcome_class"] = classify_outcome(summary)
    summary["silent_failure"] = detect_silent_failure(summary)
    return summary


def append_history(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_log(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = "\n".join(
        [
            f"## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Certified Local Bridge Run",
            f"- room_id: `{summary['room_id']}`",
            f"- watch_link: [{summary['room_id']}]({summary['watch_link']})",
            f"- participants: `{summary['host_bot']} + {summary['guest_bot']}`",
            "- result:",
            f"  - status: `{summary['status']}`",
            f"  - stop_reason: `{summary.get('stop_reason') or ''}`",
            f"  - turn_count: `{summary['turn_count']}`",
            f"  - execution: `{summary.get('execution_mode') or 'unknown'}` / `{summary.get('runner_certification') or 'unknown'}` / `{'product-owned' if summary.get('product_owned') else 'not product-owned'}`",
            f"  - validator: `{'pass' if summary['pass'] else 'fail'}`",
            "- learnings:",
            "  - This run used the local bridge path, not Telegram shell candidate runtime.",
            "  - A passing run here counts toward product-owned history only if it is certified + product_owned=true.",
        ]
    ) + "\n\n"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + entry, encoding="utf-8")


def run_single(
    *,
    base_url: str,
    ui_base: str,
    timeout_seconds: int,
    poll_seconds: float,
    turn_limit: int,
    timeout_minutes: int,
) -> dict[str, Any]:
    created = create_room(
        base_url=base_url,
        run_label=datetime.now().strftime("%H%M%S"),
        turn_limit=turn_limit,
        timeout_minutes=timeout_minutes,
    )
    room_id = created["room"]["id"]
    host_token = created["host_token"]
    host_invite = created["invites"]["host"]
    guest_invite = created["invites"]["guest"]
    watch_link = f"{ui_base.rstrip('/')}/?room_id={room_id}&host_token={host_token}"

    host_cmd = [
        "python3",
        "apps/openclaw-bridge/src/openclaw_bridge/cli.py",
        f"{base_url}/join/{room_id}?token={host_invite}",
        "--role",
        "auto",
        "--preflight-mode",
        "off",
        "--max-seconds",
        str(timeout_seconds),
        "--poll-seconds",
        "1",
        "--heartbeat-seconds",
        "5",
        "--thinking",
        "minimal",
        "--print-result",
    ]
    guest_cmd = [
        "python3",
        "apps/codex-bridge/src/codex_bridge/cli.py",
        "--base-url",
        base_url,
        "--room-id",
        room_id,
        "--token",
        guest_invite,
        "--role",
        "auto",
        "--max-seconds",
        str(timeout_seconds),
        "--poll-seconds",
        "1",
        "--heartbeat-seconds",
        "5",
    ]

    host_proc = subprocess.Popen(
        host_cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(2.0)
        guest_proc = subprocess.Popen(
            guest_cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:
        host_proc.terminate()
        host_proc.wait(timeout=10)
        raise

    try:
        payload = poll_until_closed(
            base_url=base_url,
            room_id=room_id,
            host_token=host_token,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
    finally:
        for proc in (host_proc, guest_proc):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    summary = evaluate_result(
        payload=payload,
        room_id=room_id,
        watch_link=watch_link,
        host_runtime="local-openclaw-bridge",
        guest_runtime="local-codex-bridge",
    )
    summary["host_log"] = host_proc.stdout.read() if host_proc.stdout else ""
    summary["guest_log"] = guest_proc.stdout.read() if guest_proc.stdout else ""
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live certified local bridge E2E runs and append DoD history.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--ui-base", default=DEFAULT_UI_BASE)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--history-path", default=str(DEFAULT_HISTORY_PATH))
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--turn-limit", type=int, default=8)
    parser.add_argument("--timeout-minutes", type=int, default=15)
    parser.add_argument("--stop-on-failure", action="store_true")
    args = parser.parse_args()

    history_path = Path(args.history_path)
    log_path = Path(args.log_path)
    overall_failures = 0

    for index in range(1, max(1, args.count) + 1):
        summary = run_single(
            base_url=args.base_url.rstrip("/"),
            ui_base=args.ui_base.rstrip("/"),
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            turn_limit=args.turn_limit,
            timeout_minutes=args.timeout_minutes,
        )
        append_history(history_path, {k: v for k, v in summary.items() if k not in {"host_log", "guest_log"}})
        append_log(log_path, summary)
        print(json.dumps({k: v for k, v in summary.items() if k not in {"host_log", "guest_log"}}, ensure_ascii=False), flush=True)
        if not summary["pass"]:
            overall_failures += 1
            log_dir = ROOT / ".tmp" / "certified_e2e_failures"
            log_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{summary['room_id'] or 'unknown'}"
            (log_dir / f"{stem}.host.log").write_text(summary.get("host_log", ""), encoding="utf-8")
            (log_dir / f"{stem}.guest.log").write_text(summary.get("guest_log", ""), encoding="utf-8")
            if args.stop_on_failure:
                raise SystemExit(1)

    if overall_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
