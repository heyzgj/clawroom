#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from typing import Any

import httpx


def normalize_text(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _auth_header(*, token: str | None = None, host_token: str | None = None) -> tuple[str, str]:
    host_value = str(host_token or "").strip()
    invite_value = str(token or "").strip()
    if host_value:
        return ("X-Host-Token", host_value)
    if invite_value:
        return ("X-Invite-Token", invite_value)
    raise ValueError("either token or host_token is required")


def _curl_json(url: str, *, token: str | None = None, host_token: str | None = None) -> dict[str, Any]:
    header_name, header_value = _auth_header(token=token, host_token=host_token)
    proc = subprocess.run(
        [
            "curl",
            "--retry",
            "6",
            "--retry-all-errors",
            "--retry-delay",
            "1",
            "-sS",
            "-H",
            f"{header_name}: {header_value}",
            url,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def fetch_result(
    base_url: str,
    room_id: str,
    token: str | None = None,
    host_token: str | None = None,
    *,
    retries: int = 4,
    backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    header_name, header_value = _auth_header(token=token, host_token=host_token)
    attempts = max(1, int(retries))
    delay = max(0.2, float(backoff_seconds))
    last_error: Exception | None = None
    last_status_error: str | None = None

    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=20.0, trust_env=False) as client:
                resp = client.get(
                    f"{base_url}/rooms/{room_id}/result",
                    headers={header_name: header_value},
                )
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(delay * attempt)
            continue

        if resp.status_code >= 400:
            if resp.status_code == 429 or resp.status_code >= 500:
                last_status_error = f"result request failed status={resp.status_code} body={resp.text[:500]}"
                if attempt >= attempts:
                    break
                time.sleep(delay * attempt)
                continue
            raise RuntimeError(f"result request failed status={resp.status_code} body={resp.text[:500]}")
        return resp.json()

    if last_status_error:
        raise RuntimeError(last_status_error)
    try:
        return _curl_json(f"{base_url}/rooms/{room_id}/result", token=token, host_token=host_token)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        last_error = exc
    if last_error:
        raise RuntimeError(f"result request failed after {attempts} attempts: {last_error}") from last_error
    raise RuntimeError("result request failed without a response")


def fetch_room_snapshot(
    base_url: str,
    room_id: str,
    token: str | None = None,
    host_token: str | None = None,
    *,
    retries: int = 4,
    backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    header_name, header_value = _auth_header(token=token, host_token=host_token)
    attempts = max(1, int(retries))
    delay = max(0.2, float(backoff_seconds))
    last_error: Exception | None = None
    last_status_error: str | None = None

    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=20.0, trust_env=False) as client:
                resp = client.get(
                    f"{base_url}/rooms/{room_id}",
                    headers={header_name: header_value},
                )
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(delay * attempt)
            continue

        if resp.status_code >= 400:
            if resp.status_code == 429 or resp.status_code >= 500:
                last_status_error = f"room request failed status={resp.status_code} body={resp.text[:500]}"
                if attempt >= attempts:
                    break
                time.sleep(delay * attempt)
                continue
            raise RuntimeError(f"room request failed status={resp.status_code} body={resp.text[:500]}")
        payload = resp.json()
        room = payload.get("room")
        if not isinstance(room, dict):
            raise RuntimeError(f"room request returned no room payload: {payload}")
        return room

    if last_status_error:
        raise RuntimeError(last_status_error)
    try:
        payload = _curl_json(f"{base_url}/rooms/{room_id}", token=token, host_token=host_token)
        room = payload.get("room")
        if not isinstance(room, dict):
            raise RuntimeError(f"room request returned no room payload: {payload}")
        return room
    except (subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as exc:
        last_error = exc
    if last_error:
        raise RuntimeError(f"room request failed after {attempts} attempts: {last_error}") from last_error
    raise RuntimeError("room request failed without a response")


def detect_echo_loop(transcript: list[dict[str, Any]]) -> bool:
    texts = [normalize_text(str(item.get("text", ""))) for item in transcript]
    texts = [text for text in texts if text]
    if len(texts) < 4:
        return False

    unique_ratio = len(set(texts)) / max(1, len(texts))

    prefix_chain_hits = 0
    for i in range(1, len(texts)):
        prev = texts[i - 1]
        cur = texts[i]
        marker = prev[:48]
        if len(marker) >= 24 and marker in cur:
            prefix_chain_hits += 1
    chain_ratio = prefix_chain_hits / max(1, len(texts) - 1)

    template_hits = 0
    for text in texts:
        if re.search(r"\[(host|guest) reply #\d+\]", text):
            template_hits += 1
        if "i got your suggestion:" in text:
            template_hits += 1
    template_ratio = template_hits / max(1, len(texts))

    return unique_ratio < 0.55 or chain_ratio >= 0.7 or template_ratio >= 0.6


def detect_meta_language(transcript: list[dict[str, Any]]) -> bool:
    texts = [normalize_text(str(item.get("text", ""))) for item in transcript]
    texts = [text for text in texts if text]
    if len(texts) < 3:
        return False

    markers = (
        "room",
        "relay",
        "message format",
        "turn limit",
        "turn count",
        "deadline",
        "host",
        "guest",
        "json",
        "skill",
        "api",
        "regression",
        "testing",
        "i got your suggestion",
        "you said ",
    )
    hit_count = 0
    for text in texts:
        if any(marker in text for marker in markers):
            hit_count += 1
    return (hit_count / len(texts)) >= 0.35


def required_fields_completion(result: dict[str, Any]) -> tuple[int, int]:
    total = int(result.get("required_total") or 0)
    filled = int(result.get("required_filled") or 0)
    return total, filled


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate ClawRoom result pass/fail gates.")
    parser.add_argument("--base-url", default="https://api.clawroom.cc")
    parser.add_argument("--ui-base", default="https://clawroom.cc")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--token", default=None, help="Invite token for host or guest")
    parser.add_argument("--host-token", default=None, help="Optional; preferred for owner-side result polling and watch links")
    parser.add_argument("--min-turns", type=int, default=4)
    parser.add_argument(
        "--allow-stop",
        action="append",
        default=["goal_done", "mutual_done", "turn_limit", "timeout"],
        help="Allowed stop reasons (repeatable)",
    )
    parser.add_argument("--allow-active", action="store_true", help="Allow active rooms (default: fail)")
    parser.add_argument(
        "--reject-meta-language",
        action="store_true",
        help="Fail if transcript contains too much platform-meta/test language for a natural-topic run.",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    ui_base = args.ui_base.rstrip("/")
    if not str(args.token or "").strip() and not str(args.host_token or "").strip():
        parser.error("either --token or --host-token is required")
    payload = fetch_result(
        base_url=base_url,
        room_id=args.room_id,
        token=args.token,
        host_token=args.host_token,
    )
    result = payload.get("result") or {}

    status = str(result.get("status"))
    stop_reason = result.get("stop_reason")
    turn_count = int(result.get("turn_count") or 0)
    transcript = list(result.get("transcript") or [])
    required_total, required_filled = required_fields_completion(result)

    allowed = {x.strip() for x in args.allow_stop if x and x.strip()}
    errors: list[str] = []
    warnings: list[str] = []

    if not args.allow_active and status != "closed":
        errors.append(f"room status is {status!r}, expected 'closed'")
    if status == "closed" and allowed and str(stop_reason) not in allowed:
        errors.append(f"stop_reason={stop_reason!r} not in allowed set {sorted(allowed)}")
    if required_total > 0 and required_filled < required_total:
        errors.append(f"required_filled={required_filled} < required_total={required_total}")
    if required_total == 0 and turn_count < args.min_turns:
        errors.append(f"turn_count={turn_count} < min_turns={args.min_turns}")
    if detect_echo_loop(transcript):
        errors.append("transcript matches self-echo/template-loop pattern")
    if args.reject_meta_language and detect_meta_language(transcript):
        errors.append("transcript contains too much platform-meta/test language")
    if not transcript:
        errors.append("empty transcript")
    if not stop_reason and status == "closed":
        warnings.append("closed room has empty stop_reason")

    summary: dict[str, Any] = {
        "pass": not errors,
        "room_id": args.room_id,
        "status": status,
        "stop_reason": stop_reason,
        "turn_count": turn_count,
        "required_total": required_total,
        "required_filled": required_filled,
        "transcript_messages": len(transcript),
        "errors": errors,
        "warnings": warnings,
    }
    if args.host_token:
        summary["watch_link"] = f"{ui_base}/?room_id={args.room_id}&host_token={args.host_token}"

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
