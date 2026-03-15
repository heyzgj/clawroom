from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
import pytest


@dataclass(slots=True)
class RoomCtx:
    room_id: str
    host_token: str
    invites: dict[str, str]


class ConformanceAPI:
    def __init__(self, client: httpx.Client, base_url: str) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")

    def _response(
        self,
        method: str,
        path: str,
        *,
        invite_token: str | None = None,
        participant_token: str | None = None,
        host_token: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if invite_token:
            headers["X-Invite-Token"] = invite_token
        if participant_token:
            headers["X-Participant-Token"] = participant_token
        if host_token:
            headers["X-Host-Token"] = host_token
        return self.client.request(method, f"{self.base_url}{path}", headers=headers, json=payload)

    def _isolated_response(
        self,
        method: str,
        path: str,
        *,
        invite_token: str | None = None,
        participant_token: str | None = None,
        host_token: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if invite_token:
            headers["X-Invite-Token"] = invite_token
        if participant_token:
            headers["X-Participant-Token"] = participant_token
        if host_token:
            headers["X-Host-Token"] = host_token
        headers["Connection"] = "close"
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            return client.request(method, f"{self.base_url}{path}", headers=headers, json=payload)

    def _request(
        self,
        method: str,
        path: str,
        *,
        invite_token: str | None = None,
        participant_token: str | None = None,
        host_token: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = self._response(
            method,
            path,
            invite_token=invite_token,
            participant_token=participant_token,
            host_token=host_token,
            payload=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"{method} {path} failed status={resp.status_code} body={(resp.text or '')[:500]}"
            )
        return resp.json() if resp.text else {}

    def create_room(
        self,
        *,
        participants: list[str] | None = None,
        required_fields: list[str] | None = None,
        turn_limit: int = 20,
        timeout_minutes: int = 20,
        stall_limit: int = 6,
    ) -> RoomCtx:
        participants = participants or ["host", "guest"]
        payload: dict[str, Any] = {
            "topic": "Conformance test",
            "goal": "Verify protocol semantics",
            "participants": participants,
            "turn_limit": turn_limit,
            "timeout_minutes": timeout_minutes,
            "stall_limit": stall_limit,
        }
        if required_fields is not None:
            payload["required_fields"] = required_fields
        out = self._request("POST", "/rooms", payload=payload)
        return RoomCtx(
            room_id=str(out["room"]["id"]),
            host_token=str(out["host_token"]),
            invites={str(k): str(v) for k, v in dict(out["invites"]).items()},
        )

    def join_info(self, room_id: str, invite_token: str) -> dict[str, Any]:
        return self._request("GET", f"/join/{room_id}?token={invite_token}")

    def join(self, room_id: str, invite_token: str, client_name: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/rooms/{room_id}/join",
            invite_token=invite_token,
            payload={"client_name": client_name},
        )

    def heartbeat(self, room_id: str, invite_token: str | None = None, *, participant_token: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/rooms/{room_id}/heartbeat", invite_token=invite_token, participant_token=participant_token, payload={})

    def send(self, room_id: str, invite_token: str | None, message: dict[str, Any], *, participant_token: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/rooms/{room_id}/messages", invite_token=invite_token, participant_token=participant_token, payload=message)

    def events(self, room_id: str, invite_token: str | None = None, *, participant_token: str | None = None, after: int = 0, limit: int = 200) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/rooms/{room_id}/events?after={after}&limit={limit}",
            invite_token=invite_token,
            participant_token=participant_token,
        )

    def events_response(self, room_id: str, invite_token: str | None = None, *, participant_token: str | None = None, after: int = 0, limit: int = 200) -> httpx.Response:
        return self._isolated_response(
            "GET",
            f"/rooms/{room_id}/events?after={after}&limit={limit}",
            invite_token=invite_token,
            participant_token=participant_token,
        )

    def runner_claim(
        self,
        room_id: str,
        invite_token: str,
        *,
        runner_id: str,
        execution_mode: str = "managed_attached",
        status: str = "ready",
        lease_seconds: int = 60,
        capabilities: dict[str, Any] | None = None,
        attempt_id: str | None = None,
        log_ref: str | None = None,
        last_error: str | None = None,
        recovery_reason: str | None = None,
        phase: str | None = None,
        phase_detail: str | None = None,
        managed_certified: bool | None = None,
        recovery_policy: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runner_id": runner_id,
            "execution_mode": execution_mode,
            "status": status,
            "lease_seconds": lease_seconds,
            "capabilities": capabilities or {"strategy": "daemon-safe", "health_surface": True},
        }
        if attempt_id:
            payload["attempt_id"] = attempt_id
        if log_ref:
            payload["log_ref"] = log_ref
        if last_error:
            payload["last_error"] = last_error
        if recovery_reason:
            payload["recovery_reason"] = recovery_reason
        if phase:
            payload["phase"] = phase
        if phase_detail:
            payload["phase_detail"] = phase_detail
        if managed_certified is not None:
            payload["managed_certified"] = managed_certified
        if recovery_policy:
            payload["recovery_policy"] = recovery_policy
        return self._request("POST", f"/rooms/{room_id}/runner/claim", invite_token=invite_token, payload=payload)

    def runner_renew(
        self,
        room_id: str,
        invite_token: str,
        *,
        runner_id: str,
        status: str = "active",
        execution_mode: str = "managed_attached",
        lease_seconds: int = 60,
        capabilities: dict[str, Any] | None = None,
        attempt_id: str | None = None,
        log_ref: str | None = None,
        last_error: str | None = None,
        recovery_reason: str | None = None,
        phase: str | None = None,
        phase_detail: str | None = None,
        managed_certified: bool | None = None,
        recovery_policy: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runner_id": runner_id,
            "execution_mode": execution_mode,
            "status": status,
            "lease_seconds": lease_seconds,
            "capabilities": capabilities or {"strategy": "daemon-safe", "health_surface": True},
        }
        if attempt_id:
            payload["attempt_id"] = attempt_id
        if log_ref:
            payload["log_ref"] = log_ref
        if last_error:
            payload["last_error"] = last_error
        if recovery_reason:
            payload["recovery_reason"] = recovery_reason
        if phase:
            payload["phase"] = phase
        if phase_detail:
            payload["phase_detail"] = phase_detail
        if managed_certified is not None:
            payload["managed_certified"] = managed_certified
        if recovery_policy:
            payload["recovery_policy"] = recovery_policy
        return self._request("POST", f"/rooms/{room_id}/runner/renew", invite_token=invite_token, payload=payload)

    def runner_release(
        self,
        room_id: str,
        invite_token: str,
        *,
        runner_id: str,
        status: str = "exited",
        attempt_id: str | None = None,
        reason: str | None = None,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"runner_id": runner_id, "status": status}
        if attempt_id:
            payload["attempt_id"] = attempt_id
        if reason:
            payload["reason"] = reason
        if last_error:
            payload["last_error"] = last_error
        return self._request("POST", f"/rooms/{room_id}/runner/release", invite_token=invite_token, payload=payload)

    def runner_status(
        self,
        room_id: str,
        *,
        invite_token: str | None = None,
        host_token: str | None = None,
    ) -> dict[str, Any]:
        return self._request("GET", f"/rooms/{room_id}/runner/status", invite_token=invite_token, host_token=host_token)

    def repair_invite(self, room_id: str, host_token: str, participant: str) -> dict[str, Any]:
        return self._request("POST", f"/rooms/{room_id}/repair_invites/{participant}", host_token=host_token, payload={})

    def recovery_actions(self, room_id: str, host_token: str) -> dict[str, Any]:
        return self._request("GET", f"/rooms/{room_id}/recovery_actions", host_token=host_token)

    def recovery_actions_response(
        self,
        room_id: str,
        *,
        invite_token: str | None = None,
        host_token: str | None = None,
    ) -> httpx.Response:
        return self._isolated_response("GET", f"/rooms/{room_id}/recovery_actions", invite_token=invite_token, host_token=host_token)

    def room(self, room_id: str, *, invite_token: str | None = None, host_token: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/rooms/{room_id}", invite_token=invite_token, host_token=host_token)

    def room_response(
        self,
        room_id: str,
        *,
        invite_token: str | None = None,
        host_token: str | None = None,
    ) -> httpx.Response:
        return self._isolated_response("GET", f"/rooms/{room_id}", invite_token=invite_token, host_token=host_token)

    def close(self, room_id: str, host_token: str, reason: str = "conformance_cleanup") -> dict[str, Any]:
        return self._request(
            "POST",
            f"/rooms/{room_id}/close",
            host_token=host_token,
            payload={"reason": reason},
        )

    def heartbeat_response(self, room_id: str, invite_token: str) -> httpx.Response:
        return self._isolated_response("POST", f"/rooms/{room_id}/heartbeat", invite_token=invite_token, payload={})

    def send_response(self, room_id: str, invite_token: str, message: dict[str, Any]) -> httpx.Response:
        return self._isolated_response("POST", f"/rooms/{room_id}/messages", invite_token=invite_token, payload=message)

    def leave_response(self, room_id: str, invite_token: str, reason: str = "test_leave") -> httpx.Response:
        return self._isolated_response(
            "POST",
            f"/rooms/{room_id}/leave",
            invite_token=invite_token,
            payload={"reason": reason},
        )

    def monitor_events(self, room_id: str, host_token: str, *, after: int = 0, limit: int = 200) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/rooms/{room_id}/monitor/events?after={after}&limit={limit}",
            host_token=host_token,
        )


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.getenv("CLAWROOM_BASE_URL", "http://127.0.0.1:8787").rstrip("/")


@pytest.fixture()
def api(base_url: str) -> ConformanceAPI:
    with httpx.Client(timeout=20.0, trust_env=False) as client:
        yield ConformanceAPI(client=client, base_url=base_url)
