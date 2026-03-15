from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills" / "openclaw-telegram-e2e" / "scripts" / "validate_room_result.py"
SPEC = importlib.util.spec_from_file_location("validate_room_result", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def get(self, *_args, **_kwargs):
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _CapturingClient:
    def __init__(self, captured: dict[str, object], response: _FakeResponse) -> None:
        self._captured = captured
        self._response = response

    def __enter__(self) -> _CapturingClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def get(self, url, **kwargs):
        self._captured["url"] = url
        self._captured["headers"] = kwargs.get("headers") or {}
        return self._response


class _FakeCompletedProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_fetch_result_retries_transient_http_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: list[object] = [
        httpx.ConnectError("tls eof"),
        _FakeResponse(200, {"result": {"status": "closed"}}),
    ]

    monkeypatch.setattr(MODULE.httpx, "Client", lambda **_kwargs: _FakeClient(outcomes))
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)

    payload = MODULE.fetch_result("https://api.clawroom.cc", "room_abc", "inv_123")
    assert payload["result"]["status"] == "closed"
    assert outcomes == []


def test_fetch_result_fails_fast_on_non_retryable_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: list[object] = [
        _FakeResponse(403, {"error": "forbidden"}, text="forbidden"),
    ]

    monkeypatch.setattr(MODULE.httpx, "Client", lambda **_kwargs: _FakeClient(outcomes))

    with pytest.raises(RuntimeError, match="status=403"):
        MODULE.fetch_result("https://api.clawroom.cc", "room_abc", "inv_123")


def test_fetch_room_snapshot_retries_and_returns_room(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: list[object] = [
        httpx.ConnectError("tls eof"),
        _FakeResponse(200, {"room": {"id": "room_abc", "execution_mode": "managed_attached"}}),
    ]

    monkeypatch.setattr(MODULE.httpx, "Client", lambda **_kwargs: _FakeClient(outcomes))
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)

    room = MODULE.fetch_room_snapshot("https://api.clawroom.cc", "room_abc", "inv_123")
    assert room["id"] == "room_abc"
    assert room["execution_mode"] == "managed_attached"


def test_fetch_result_falls_back_to_curl_after_httpx_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: list[object] = [
        httpx.ConnectError("tls eof"),
        httpx.ConnectError("tls eof"),
        httpx.ConnectError("tls eof"),
        httpx.ConnectError("tls eof"),
    ]

    monkeypatch.setattr(MODULE.httpx, "Client", lambda **_kwargs: _FakeClient(outcomes))
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *_args, **_kwargs: _FakeCompletedProcess('{"result":{"status":"closed","turn_count":4,"transcript":[{"text":"ok"}]}}'),
    )

    payload = MODULE.fetch_result("https://api.clawroom.cc", "room_abc", "inv_123")
    assert payload["result"]["status"] == "closed"


def test_fetch_room_snapshot_falls_back_to_curl_after_httpx_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: list[object] = [
        httpx.ConnectError("tls eof"),
        httpx.ConnectError("tls eof"),
        httpx.ConnectError("tls eof"),
        httpx.ConnectError("tls eof"),
    ]

    monkeypatch.setattr(MODULE.httpx, "Client", lambda **_kwargs: _FakeClient(outcomes))
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *_args, **_kwargs: _FakeCompletedProcess('{"room":{"id":"room_abc","execution_mode":"compatibility"}}'),
    )

    room = MODULE.fetch_room_snapshot("https://api.clawroom.cc", "room_abc", "inv_123")
    assert room["id"] == "room_abc"


def test_fetch_result_prefers_host_token_for_owner_side_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        MODULE.httpx,
        "Client",
        lambda **_kwargs: _CapturingClient(captured, _FakeResponse(200, {"result": {"status": "closed"}})),
    )

    payload = MODULE.fetch_result(
        "https://api.clawroom.cc",
        "room_abc",
        token="inv_rotated_old",
        host_token="host_live_123",
    )

    assert payload["result"]["status"] == "closed"
    assert captured["headers"] == {"X-Host-Token": "host_live_123"}


def test_fetch_room_snapshot_prefers_host_token_when_invite_tokens_can_rotate(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        MODULE.httpx,
        "Client",
        lambda **_kwargs: _CapturingClient(
            captured,
            _FakeResponse(200, {"room": {"id": "room_abc", "execution_mode": "managed_attached"}}),
        ),
    )

    room = MODULE.fetch_room_snapshot(
        "https://api.clawroom.cc",
        "room_abc",
        token="inv_rotated_old",
        host_token="host_live_123",
    )

    assert room["id"] == "room_abc"
    assert captured["headers"] == {"X-Host-Token": "host_live_123"}
