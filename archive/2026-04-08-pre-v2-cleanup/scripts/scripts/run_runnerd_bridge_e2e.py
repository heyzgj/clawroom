#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUNNERD_SRC = ROOT / "apps" / "runnerd" / "src"
TELEGRAM_E2E_SCRIPTS = ROOT / "skills" / "openclaw-telegram-e2e" / "scripts"
if str(RUNNERD_SRC) not in sys.path:
    sys.path.insert(0, str(RUNNERD_SRC))
if str(TELEGRAM_E2E_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(TELEGRAM_E2E_SCRIPTS))

from runnerd.models import WakePackage  # noqa: E402
from validate_room_result import fetch_result  # noqa: E402


DEFAULT_BASE_URL = "https://api.clawroom.cc"
DEFAULT_UI_BASE = "https://clawroom.cc"
DEFAULT_RUNNERD_URL = "http://127.0.0.1:8741"
DEFAULT_LOG_PATH = ROOT / "docs" / "progress" / "RUNNERD_E2E_LOG.md"
DEFAULT_HISTORY_PATH = ROOT / "docs" / "progress" / "TELEGRAM_E2E_HISTORY.jsonl"
RUNNERD_REQUEST_RETRIES = 3
RUNNERD_RETRY_SLEEP_SECONDS = 1.0
FOUNDATION_CONTRACT_VERSION = "foundation-certified-v1"
PATH_FAMILY = "runnerd_gateway_local_v1"


def create_room(*, base_url: str, turn_limit: int, timeout_minutes: int) -> dict[str, Any]:
    payload = {
        "topic": "Choose the owner of the release checklist",
        "goal": "Agree who owns the release checklist and the next concrete shipping step",
        "expected_outcomes": ["owner", "next_step"],
        "participants": ["host", "guest"],
        "turn_limit": turn_limit,
        "stall_limit": 6,
        "timeout_minutes": timeout_minutes,
    }
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        response = client.post(f"{base_url}/rooms", json=payload)
    response.raise_for_status()
    return response.json()


def wait_for_runnerd_health(*, runnerd_url: str, timeout_seconds: int) -> None:
    deadline = time.time() + max(5, timeout_seconds)
    last_error = "runnerd never answered /healthz"
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=5.0, trust_env=False) as client:
                response = client.get(f"{runnerd_url.rstrip('/')}/healthz")
            if response.status_code == 200 and bool(response.json().get("ok")):
                return
            last_error = f"healthz status={response.status_code} body={response.text[:200]}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(last_error)


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


def post_wake(*, runnerd_url: str, package: WakePackage) -> dict[str, Any]:
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        response = client.post(f"{runnerd_url.rstrip('/')}/wake", json=package.model_dump(mode="json"))
    response.raise_for_status()
    return response.json()


def get_run(*, runnerd_url: str, run_id: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, RUNNERD_REQUEST_RETRIES + 1):
        try:
            with httpx.Client(timeout=10.0, trust_env=False) as client:
                response = client.get(f"{runnerd_url.rstrip('/')}/runs/{run_id}")
            response.raise_for_status()
            return response.json()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if attempt >= RUNNERD_REQUEST_RETRIES:
                break
            time.sleep(RUNNERD_RETRY_SLEEP_SECONDS * attempt)
    assert last_error is not None
    raise last_error


def post_owner_reply(*, runnerd_url: str, run_id: str, owner_request_id: str, text: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, RUNNERD_REQUEST_RETRIES + 1):
        try:
            with httpx.Client(timeout=10.0, trust_env=False) as client:
                response = client.post(
                    f"{runnerd_url.rstrip('/')}/runs/{run_id}/owner-reply",
                    json={"text": text, "owner_request_id": owner_request_id},
                )
            response.raise_for_status()
            return response.json()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if attempt >= RUNNERD_REQUEST_RETRIES:
                break
            time.sleep(RUNNERD_RETRY_SLEEP_SECONDS * attempt)
    assert last_error is not None
    raise last_error


def get_run_or_latest(
    *,
    runnerd_url: str,
    run_id: str,
    latest_runs: dict[str, dict[str, Any]],
    room_closed: bool,
) -> dict[str, Any]:
    try:
        payload = get_run(runnerd_url=runnerd_url, run_id=run_id)
        latest_runs[run_id] = payload
        return payload
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError):
        if room_closed and run_id in latest_runs:
            return latest_runs[run_id]
        raise


def wait_for_runs_to_settle(*, runnerd_url: str, run_ids: list[str], settle_seconds: int) -> dict[str, dict[str, Any]]:
    deadline = time.time() + max(0, settle_seconds)
    latest: dict[str, dict[str, Any]] = {}
    while True:
        all_terminal = True
        for run_id in run_ids:
            payload = get_run_or_latest(
                runnerd_url=runnerd_url,
                run_id=run_id,
                latest_runs=latest,
                room_closed=True,
            )
            latest[run_id] = payload
            if str(payload.get("status") or "") not in {"exited", "abandoned", "replaced"}:
                all_terminal = False
        if all_terminal or time.time() >= deadline:
            return latest
        time.sleep(1.0)


def close_room(*, base_url: str, room_id: str, host_token: str) -> dict[str, Any]:
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.post(f"{base_url.rstrip('/')}/rooms/{room_id}/close", headers={"X-Host-Token": host_token})
    response.raise_for_status()
    return response.json()


def poll_result(*, base_url: str, room_id: str, host_token: str, timeout_seconds: int, poll_seconds: float) -> dict[str, Any]:
    deadline = time.time() + max(15, timeout_seconds)
    last_payload: dict[str, Any] | None = None
    while time.time() < deadline:
        payload = fetch_result(base_url=base_url, room_id=room_id, host_token=host_token)
        last_payload = payload
        result = dict(payload.get("result") or {})
        if str(result.get("status") or "") == "closed":
            return payload
        time.sleep(max(1.0, poll_seconds))
    return last_payload or {}


def append_log(path: Path, *, room_id: str, watch_link: str, host_run: dict[str, Any], guest_run: dict[str, Any], result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - runnerd bridge E2E",
        f"- room_id: `{room_id}`",
        f"- watch_link: [{room_id}]({watch_link})",
        f"- host_run: `{host_run.get('run_id')}` / `{host_run.get('runner_kind')}` / `{host_run.get('status')}`",
        f"  - restart_count: `{host_run.get('restart_count')}` / root_cause: `{host_run.get('root_cause_code')}` / supersedes: `{host_run.get('supersedes_run_id')}` / superseded_by: `{host_run.get('superseded_by_run_id')}`",
        f"- guest_run: `{guest_run.get('run_id')}` / `{guest_run.get('runner_kind')}` / `{guest_run.get('status')}`",
        f"  - restart_count: `{guest_run.get('restart_count')}` / root_cause: `{guest_run.get('root_cause_code')}` / supersedes: `{guest_run.get('supersedes_run_id')}` / superseded_by: `{guest_run.get('superseded_by_run_id')}`",
        "- result:",
        f"  - status: `{result.get('status')}`",
        f"  - stop_reason: `{result.get('stop_reason')}`",
        f"  - execution_mode: `{result.get('execution_mode')}`",
        f"  - runner_certification: `{result.get('runner_certification')}`",
        f"  - product_owned: `{result.get('product_owned')}`",
        f"  - automatic_recovery_eligible: `{result.get('automatic_recovery_eligible')}`",
        "",
    ]
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


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


def classify_recovery(summary: dict[str, Any]) -> str:
    if bool(summary.get("replacement_plane_exhausted")):
        return "restart_exhausted"
    if bool(summary.get("replacement_run_observed")):
        return "success_after_replacement" if bool(summary.get("pass")) else "replacement_required"
    if bool(summary.get("restart_observed")):
        return "success_after_restart" if bool(summary.get("pass")) else "restart_attempted"
    return "clean"


def detect_silent_failure(summary: dict[str, Any]) -> bool:
    if bool(summary.get("pass")) or bool(summary.get("infra_blocked")):
        return False
    status = str(summary.get("status") or "")
    attention_state = str(summary.get("execution_attention_state") or "")
    reasons = list(summary.get("execution_attention_reasons") or [])
    root_cause = summary.get("primary_root_cause_code")
    return status != "closed" and attention_state in {"", "healthy"} and not reasons and not root_cause


def restart_exhausted(run_payload: dict[str, Any]) -> bool:
    code = str(run_payload.get("root_cause_code") or "")
    return code in {
        "runnerd_restart_exhausted_before_claim",
        "runnerd_restart_exhausted_after_claim",
    }


def replacement_run_observed(run_payload: dict[str, Any]) -> bool:
    return bool(run_payload.get("supersedes_run_id")) or bool(run_payload.get("superseded_by_run_id"))


def build_history_record(
    *,
    payload: dict[str, Any],
    room_id: str,
    watch_link: str,
    host_run: dict[str, Any],
    guest_run: dict[str, Any],
    owner_reply_count: int,
) -> dict[str, Any]:
    result = dict(payload.get("result") or {})
    room = dict(payload.get("room") or {})
    start_slo = dict(result.get("start_slo") or room.get("start_slo") or {})
    stop_reason = str(result.get("stop_reason") or "")
    status = str(result.get("status") or "")
    turn_count = int(result.get("turn_count") or 0)
    first_relay_at = start_slo.get("first_relay_at")
    host_restart_count = int(host_run.get("restart_count") or 0)
    guest_restart_count = int(guest_run.get("restart_count") or 0)
    host_restart_exhausted = restart_exhausted(host_run)
    guest_restart_exhausted = restart_exhausted(guest_run)
    host_replacement_observed = replacement_run_observed(host_run)
    guest_replacement_observed = replacement_run_observed(guest_run)
    allowed_stop_reasons = {"goal_done", "mutual_done", "turn_limit", "timeout"}
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenario": "runnerd_gateway_local",
        "room_id": room_id,
        "watch_link": watch_link,
        "host_bot": "runnerd-openclaw-gateway",
        "guest_bot": "runnerd-codex-gateway",
        "wait_after_new_seconds": 0.0,
        "pass": False,
        "infra_blocked": False,
        "status": result.get("status"),
        "stop_reason": result.get("stop_reason"),
        "turn_count": turn_count,
        "execution_mode": result.get("execution_mode"),
        "runner_certification": result.get("runner_certification"),
        "managed_coverage": result.get("managed_coverage"),
        "product_owned": result.get("product_owned"),
        "automatic_recovery_eligible": bool(result.get("automatic_recovery_eligible")),
        "foundation_contract_version": FOUNDATION_CONTRACT_VERSION,
        "path_family": PATH_FAMILY,
        "start_slo": start_slo,
        "first_relay_observed": bool(first_relay_at),
        "owner_reply_count": int(owner_reply_count),
        "last_live_execution_mode": room.get("execution_mode"),
        "last_live_managed_coverage": room.get("managed_coverage"),
        "last_live_product_owned": room.get("product_owned"),
        "attempt_status": result.get("attempt_status") or room.get("attempt_status"),
        "execution_attention_state": ((result.get("execution_attention") or {}).get("state")),
        "execution_attention_reasons": list((result.get("execution_attention") or {}).get("reasons") or []),
        "primary_root_cause_code": ((result.get("root_cause_hints") or [{}])[0].get("code") if result.get("root_cause_hints") else None),
        "primary_root_cause_confidence": ((result.get("root_cause_hints") or [{}])[0].get("confidence") if result.get("root_cause_hints") else None),
        "runnerd_host_run_id": host_run.get("run_id"),
        "runnerd_guest_run_id": guest_run.get("run_id"),
        "runnerd_host_status": host_run.get("status"),
        "runnerd_guest_status": guest_run.get("status"),
        "runnerd_host_root_cause": host_run.get("root_cause_code"),
        "runnerd_guest_root_cause": guest_run.get("root_cause_code"),
        "runnerd_host_restart_count": host_restart_count,
        "runnerd_guest_restart_count": guest_restart_count,
        "runnerd_host_restart_exhausted": host_restart_exhausted,
        "runnerd_guest_restart_exhausted": guest_restart_exhausted,
        "runnerd_host_supersedes_run_id": host_run.get("supersedes_run_id"),
        "runnerd_guest_supersedes_run_id": guest_run.get("supersedes_run_id"),
        "runnerd_host_superseded_by_run_id": host_run.get("superseded_by_run_id"),
        "runnerd_guest_superseded_by_run_id": guest_run.get("superseded_by_run_id"),
        "restart_observed": host_restart_count > 0 or guest_restart_count > 0,
        "replacement_run_observed": host_replacement_observed or guest_replacement_observed,
        "replacement_plane_exhausted": host_restart_exhausted or guest_restart_exhausted,
        "errors": [],
        "warnings": [],
    }
    if status != "closed":
        summary["errors"].append(f"room status is {summary['status']!r}, expected 'closed'")
    if stop_reason not in allowed_stop_reasons:
        summary["errors"].append(f"stop_reason={stop_reason!r} not in {sorted(allowed_stop_reasons)}")
    if not bool(summary["product_owned"]):
        summary["errors"].append("result.product_owned is false")
    if str(summary["runner_certification"] or "") != "certified":
        summary["errors"].append(f"runner_certification={summary['runner_certification']!r} is not 'certified'")
    if not bool(summary["automatic_recovery_eligible"]):
        summary["errors"].append("automatic_recovery_eligible is false")
    if not first_relay_at:
        summary["errors"].append("start_slo.first_relay_at is empty")
    if turn_count < 2:
        summary["errors"].append(f"turn_count={turn_count} < 2")
    if int(owner_reply_count) < 1:
        summary["errors"].append("owner escalation was never exercised")
    summary["pass"] = not summary["errors"]
    summary["outcome_class"] = classify_outcome(summary)
    summary["recovery_class"] = classify_recovery(summary)
    summary["silent_failure"] = detect_silent_failure(summary)
    if bool(summary["pass"]) and summary["recovery_class"] == "success_after_restart":
        summary["warnings"].append("runnerd restart path was exercised before the room finished")
    if bool(summary["pass"]) and summary["recovery_class"] == "success_after_replacement":
        summary["warnings"].append("runner replacement lineage was exercised before the room finished")
    return summary


def append_history(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local runnerd bridge E2E against a ClawRoom API.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--ui-base", default=DEFAULT_UI_BASE)
    parser.add_argument("--runnerd-url", default=DEFAULT_RUNNERD_URL)
    parser.add_argument("--turn-limit", type=int, default=6)
    parser.add_argument("--timeout-minutes", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--host-runner-kind", choices=["openclaw_bridge", "codex_bridge"], default="openclaw_bridge")
    parser.add_argument("--guest-runner-kind", choices=["openclaw_bridge", "codex_bridge"], default="codex_bridge")
    parser.add_argument("--runnerd-start", action="store_true", help="Start a local runnerd subprocess for this run.")
    parser.add_argument("--runnerd-port", type=int, default=8741)
    parser.add_argument("--settle-seconds", type=int, default=8)
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--history-path", default=str(DEFAULT_HISTORY_PATH))
    args = parser.parse_args()

    runnerd_proc: subprocess.Popen[str] | None = None
    runnerd_url = args.runnerd_url.rstrip("/")
    if args.runnerd_start:
        selected_port = choose_runnerd_port(args.runnerd_port)
        runnerd_url = f"http://127.0.0.1:{selected_port}"
        runnerd_proc = subprocess.Popen(
            [
                sys.executable,
                "apps/runnerd/src/runnerd/cli.py",
                "--host",
                "127.0.0.1",
                "--port",
                str(selected_port),
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    try:
        wait_for_runnerd_health(runnerd_url=runnerd_url, timeout_seconds=15)
        created = create_room(
            base_url=args.base_url,
            turn_limit=args.turn_limit,
            timeout_minutes=args.timeout_minutes,
        )
        room_id = str(created["room"]["id"])
        host_token = str(created["host_token"])
        host_invite = str(created["invites"]["host"])
        guest_invite = str(created["invites"]["guest"])
        watch_link = f"{args.ui_base.rstrip('/')}/?room_id={room_id}&host_token={host_token}"
        coordination_id = f"coord_{uuid.uuid4().hex[:12]}"

        host_package = WakePackage.model_validate(
            {
                "coordination_id": coordination_id,
                "wake_request_id": f"wake_{uuid.uuid4().hex[:10]}",
                "room_id": room_id,
                "join_link": f"{args.base_url.rstrip('/')}/join/{room_id}?token={host_invite}",
                "role": "initiator",
                "task_summary": "Start the release-owner discussion, but do not finalize it until you have asked your owner once which tradeoff to prefer.",
                "owner_context": (
                    "Before you lock a final recommendation, ask your owner exactly once whether to optimize for fastest shipping or safest rollout. "
                    "After the owner replies, translate that guidance back into the room, keep the checklist owner explicit, and close with DONE once owner and next_step are settled. "
                    "Do not ask for extra drafts or follow-up work after the decision is already explicit."
                ),
                "expected_output": "A final owner choice and next release step.",
                "deadline_at": None,
                "preferred_runner_kind": args.host_runner_kind,
                "sender_owner_label": "owner-a",
                "sender_gateway_label": "telegram-gateway-a",
            }
        )
        guest_package = WakePackage.model_validate(
            {
                "coordination_id": coordination_id,
                "wake_request_id": f"wake_{uuid.uuid4().hex[:10]}",
                "room_id": room_id,
                "join_link": f"{args.base_url.rstrip('/')}/join/{room_id}?token={guest_invite}",
                "role": "responder",
                "task_summary": "Review the proposal, challenge weak points, and ask your owner once before agreeing on the release checklist owner.",
                "owner_context": (
                    "Before you endorse the final plan, ask your owner exactly once whether they want speed or caution prioritized. "
                    "Wait for the answer, then turn it into a concise room reply that makes owner and next_step explicit. "
                    "Do not ask a follow-up question after your owner reply."
                ),
                "expected_output": "A concise recommendation and agreement on next action.",
                "deadline_at": None,
                "preferred_runner_kind": args.guest_runner_kind,
                "sender_owner_label": "owner-b",
                "sender_gateway_label": "telegram-gateway-b",
            }
        )

        host_run = post_wake(runnerd_url=runnerd_url, package=host_package)
        guest_run = post_wake(runnerd_url=runnerd_url, package=guest_package)

        run_ids = [str(host_run["run_id"]), str(guest_run["run_id"])]
        owner_replied: set[str] = set()
        deadline = time.time() + max(20, args.timeout_seconds)
        latest_runs: dict[str, dict[str, Any]] = {}
        while time.time() < deadline:
            all_terminal = True
            for run_id in run_ids:
                run_payload = get_run_or_latest(
                    runnerd_url=runnerd_url,
                    run_id=run_id,
                    latest_runs=latest_runs,
                    room_closed=False,
                )
                latest_runs[run_id] = run_payload
                status = str(run_payload.get("status") or "")
                if status not in {"exited", "abandoned"}:
                    all_terminal = False
                pending = run_payload.get("pending_owner_request") or {}
                owner_req_id = str(pending.get("owner_request_id") or "").strip()
                if owner_req_id and owner_req_id not in owner_replied:
                    post_owner_reply(
                        runnerd_url=runnerd_url,
                        run_id=run_id,
                        owner_request_id=owner_req_id,
                        text="Choose the fastest safe option and keep the checklist owner explicit.",
                    )
                    owner_replied.add(owner_req_id)
            payload = fetch_result(base_url=args.base_url, room_id=room_id, host_token=host_token)
            result = dict(payload.get("result") or {})
            if str(result.get("status") or "") == "closed":
                settled = wait_for_runs_to_settle(runnerd_url=runnerd_url, run_ids=run_ids, settle_seconds=args.settle_seconds)
                final_host_run = settled[run_ids[0]]
                final_guest_run = settled[run_ids[1]]
                append_log(
                    Path(args.log_path),
                    room_id=room_id,
                    watch_link=watch_link,
                    host_run=final_host_run,
                    guest_run=final_guest_run,
                    result=result,
                )
                record = build_history_record(
                    payload=payload,
                    room_id=room_id,
                    watch_link=watch_link,
                    host_run=final_host_run,
                    guest_run=final_guest_run,
                    owner_reply_count=len(owner_replied),
                )
                append_history(Path(args.history_path), record)
                print(
                    json.dumps(
                        {
                            "room_id": room_id,
                            "watch_link": watch_link,
                            "result": result,
                            "runnerd": {"host": final_host_run, "guest": final_guest_run},
                            "history_record": record,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            if all_terminal:
                break
            time.sleep(max(1.0, args.poll_seconds))

        close_room(base_url=args.base_url, room_id=room_id, host_token=host_token)
        payload = poll_result(
            base_url=args.base_url,
            room_id=room_id,
            host_token=host_token,
            timeout_seconds=20,
            poll_seconds=args.poll_seconds,
        )
        result = dict(payload.get("result") or {})
        settled = wait_for_runs_to_settle(runnerd_url=runnerd_url, run_ids=run_ids, settle_seconds=args.settle_seconds)
        final_host_run = settled[run_ids[0]]
        final_guest_run = settled[run_ids[1]]
        append_log(
            Path(args.log_path),
            room_id=room_id,
            watch_link=watch_link,
            host_run=final_host_run,
            guest_run=final_guest_run,
            result=result,
        )
        record = build_history_record(
            payload=payload,
            room_id=room_id,
            watch_link=watch_link,
            host_run=final_host_run,
            guest_run=final_guest_run,
            owner_reply_count=len(owner_replied),
        )
        append_history(Path(args.history_path), record)
        print(
            json.dumps(
                {
                    "room_id": room_id,
                    "watch_link": watch_link,
                    "result": result,
                    "runnerd": {"host": final_host_run, "guest": final_guest_run},
                    "history_record": record,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if runnerd_proc is not None:
            runnerd_proc.terminate()
            try:
                runnerd_proc.wait(timeout=5)
            except Exception:
                runnerd_proc.kill()


if __name__ == "__main__":
    main()
