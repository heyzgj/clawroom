from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx


def parse_join_url(url: str) -> dict[str, str]:
    """Parse ClawRoom join URL into {base_url, room_id, token}.

    Supported forms:
    - https://api.clawroom.cc/join/<room_id>?token=<invite_token>
    - https://api.clawroom.cc/rooms/<room_id>/join_info?token=<invite_token>
    """

    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        base_url = f"{parsed.scheme}://{parsed.netloc}"
    else:
        # Backward-compatible fallback for malformed but common inputs.
        base_url = url.split("/join/")[0].rstrip("/")

    host = (parsed.hostname or "").lower()
    if host in {"clawroom.cc", "www.clawroom.cc"}:
        base_url = os.getenv("CLAWROOM_API_BASE", "https://api.clawroom.cc")

    room_id = ""
    path_parts = [part for part in parsed.path.split("/") if part]
    if "join" in path_parts:
        idx = path_parts.index("join")
        if idx + 1 < len(path_parts):
            room_id = path_parts[idx + 1]
    elif "rooms" in path_parts:
        idx = path_parts.index("rooms")
        if idx + 1 < len(path_parts):
            room_id = path_parts[idx + 1]

    token = parse_qs(parsed.query).get("token", [""])[0]

    if not room_id or not token:
        raise ValueError(f"Cannot parse join URL: {url} (need /join/<room_id>?token=<token>)")

    return {"base_url": base_url, "room_id": room_id, "token": token}


def http_json(
    method: str,
    url: str,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
    retries: int = 4,
) -> dict[str, Any]:
    """HTTP JSON helper with retry/backoff for bridge clients."""

    headers: dict[str, str] = {}
    if token:
        headers["X-Invite-Token"] = token

    retryable_exceptions = (httpx.TransportError, httpx.TimeoutException)
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                resp = client.request(method, url, headers=headers, json=payload)
        except retryable_exceptions:
            if attempt >= retries - 1:
                raise
            time.sleep(min(2.0, 0.25 * (2**attempt)))
            continue

        if resp.status_code >= 500 and attempt < retries - 1:
            time.sleep(min(2.0, 0.25 * (2**attempt)))
            continue
        if resp.status_code >= 400:
            body = (resp.text or "").strip()
            short_body = body if len(body) <= 500 else body[:499] + "..."
            raise RuntimeError(
                f"http {method} {url} failed status={resp.status_code} body={short_body}"
            )
        return resp.json()

    raise RuntimeError(f"http {method} {url} failed after {retries} retries")


def runner_claim(
    *,
    base_url: str,
    room_id: str,
    token: str,
    runner_id: str,
    execution_mode: str,
    status: str,
    capabilities: dict[str, Any] | None = None,
    lease_seconds: int | None = None,
    log_ref: str | None = None,
    last_error: str | None = None,
    recovery_reason: str | None = None,
    phase: str | None = None,
    phase_detail: str | None = None,
    attempt_id: str | None = None,
    managed_certified: bool | None = None,
    recovery_policy: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "runner_id": runner_id,
        "execution_mode": execution_mode,
        "status": status,
    }
    if capabilities is not None:
        payload["capabilities"] = capabilities
    if lease_seconds is not None:
        payload["lease_seconds"] = lease_seconds
    if log_ref:
        payload["log_ref"] = log_ref
    if last_error:
        payload["last_error"] = last_error
    if recovery_reason:
        payload["recovery_reason"] = recovery_reason
    if phase:
        payload["phase"] = str(phase).strip()
    if phase_detail:
        payload["phase_detail"] = str(phase_detail).strip()
    if attempt_id:
        payload["attempt_id"] = attempt_id
    if managed_certified is not None:
        payload["managed_certified"] = bool(managed_certified)
    if recovery_policy:
        payload["recovery_policy"] = str(recovery_policy).strip()
    return http_json("POST", f"{base_url.rstrip('/')}/rooms/{room_id}/runner/claim", token=token, payload=payload)


def runner_renew(
    *,
    base_url: str,
    room_id: str,
    token: str,
    runner_id: str,
    status: str,
    execution_mode: str | None = None,
    capabilities: dict[str, Any] | None = None,
    lease_seconds: int | None = None,
    log_ref: str | None = None,
    last_error: str | None = None,
    recovery_reason: str | None = None,
    phase: str | None = None,
    phase_detail: str | None = None,
    attempt_id: str | None = None,
    managed_certified: bool | None = None,
    recovery_policy: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "runner_id": runner_id,
        "status": status,
    }
    if execution_mode:
        payload["execution_mode"] = execution_mode
    if capabilities is not None:
        payload["capabilities"] = capabilities
    if lease_seconds is not None:
        payload["lease_seconds"] = lease_seconds
    if log_ref:
        payload["log_ref"] = log_ref
    if last_error:
        payload["last_error"] = last_error
    if recovery_reason:
        payload["recovery_reason"] = recovery_reason
    if phase:
        payload["phase"] = str(phase).strip()
    if phase_detail:
        payload["phase_detail"] = str(phase_detail).strip()
    if attempt_id:
        payload["attempt_id"] = attempt_id
    if managed_certified is not None:
        payload["managed_certified"] = bool(managed_certified)
    if recovery_policy:
        payload["recovery_policy"] = str(recovery_policy).strip()
    return http_json("POST", f"{base_url.rstrip('/')}/rooms/{room_id}/runner/renew", token=token, payload=payload)


def runner_release(
    *,
    base_url: str,
    room_id: str,
    token: str,
    runner_id: str,
    status: str = "exited",
    reason: str | None = None,
    last_error: str | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "runner_id": runner_id,
        "status": status,
    }
    if reason:
        payload["reason"] = reason
    if last_error:
        payload["last_error"] = last_error
    if attempt_id:
        payload["attempt_id"] = attempt_id
    return http_json("POST", f"{base_url.rstrip('/')}/rooms/{room_id}/runner/release", token=token, payload=payload)


def runner_status(
    *,
    base_url: str,
    room_id: str,
    token: str,
) -> dict[str, Any]:
    return http_json("GET", f"{base_url.rstrip('/')}/rooms/{room_id}/runner/status", token=token)
