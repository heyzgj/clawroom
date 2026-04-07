from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "codex-bridge" / "src"))
sys.path.insert(0, str(ROOT / "apps" / "openclaw-bridge" / "src"))

from codex_bridge import cli as codex_cli
from openclaw_bridge import cli as openclaw_cli


@pytest.fixture()
def runner_plane_recorder() -> dict[str, list[dict[str, Any]]]:
    return {"claim": [], "renew": [], "release": []}


@pytest.fixture(autouse=True)
def _patch_runner_plane(
    monkeypatch: pytest.MonkeyPatch,
    runner_plane_recorder: dict[str, list[dict[str, Any]]],
) -> None:
    attempt_ids: dict[tuple[str, str], str] = {}

    def fake_claim(
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
        del base_url, token
        effective_attempt_id = attempt_id or f"attempt_{room_id}_{runner_id.replace(':', '_')}"
        attempt_ids[(room_id, runner_id)] = effective_attempt_id
        runner_plane_recorder["claim"].append(
            {
                "room_id": room_id,
                "runner_id": runner_id,
                "execution_mode": execution_mode,
                "status": status,
                "capabilities": capabilities or {},
                "lease_seconds": lease_seconds,
                "log_ref": log_ref,
                "last_error": last_error,
                "recovery_reason": recovery_reason,
                "phase": phase,
                "phase_detail": phase_detail,
                "attempt_id": effective_attempt_id,
                "managed_certified": managed_certified,
                "recovery_policy": recovery_policy,
            }
        )
        return {"attempt_id": effective_attempt_id}

    def fake_renew(
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
        del base_url, token
        effective_attempt_id = attempt_id or attempt_ids.get((room_id, runner_id)) or f"attempt_{room_id}_{runner_id.replace(':', '_')}"
        attempt_ids[(room_id, runner_id)] = effective_attempt_id
        runner_plane_recorder["renew"].append(
            {
                "room_id": room_id,
                "runner_id": runner_id,
                "execution_mode": execution_mode,
                "status": status,
                "capabilities": capabilities or {},
                "lease_seconds": lease_seconds,
                "log_ref": log_ref,
                "last_error": last_error,
                "recovery_reason": recovery_reason,
                "phase": phase,
                "phase_detail": phase_detail,
                "attempt_id": effective_attempt_id,
                "managed_certified": managed_certified,
                "recovery_policy": recovery_policy,
            }
        )
        return {"attempt_id": effective_attempt_id}

    def fake_release(
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
        del base_url, token
        effective_attempt_id = attempt_id or attempt_ids.get((room_id, runner_id)) or f"attempt_{room_id}_{runner_id.replace(':', '_')}"
        runner_plane_recorder["release"].append(
            {
                "room_id": room_id,
                "runner_id": runner_id,
                "status": status,
                "reason": reason,
                "last_error": last_error,
                "attempt_id": effective_attempt_id,
            }
        )
        return {"attempt_id": effective_attempt_id, "status": status}

    monkeypatch.setattr(codex_cli, "runner_claim", fake_claim)
    monkeypatch.setattr(codex_cli, "runner_renew", fake_renew)
    monkeypatch.setattr(codex_cli, "runner_release", fake_release)
    monkeypatch.setattr(openclaw_cli, "runner_claim", fake_claim)
    monkeypatch.setattr(openclaw_cli, "runner_renew", fake_renew)
    monkeypatch.setattr(openclaw_cli, "runner_release", fake_release)


def _active_room(room_id: str = "room_test", *, turn_count: int = 1) -> dict[str, Any]:
    return {
        "id": room_id,
        "status": "active",
        "stop_reason": None,
        "turn_count": turn_count,
        "participants": [
            {"name": "host", "joined": True},
            {"name": "guest", "joined": True},
        ],
    }


def _closed_room(room_id: str = "room_test", *, reason: str = "goal_done") -> dict[str, Any]:
    room = _active_room(room_id, turn_count=2)
    room["status"] = "closed"
    room["stop_reason"] = reason
    return room


class _FakeCodexAPI:
    def __init__(self, relay_message: dict[str, Any]) -> None:
        self.relay_message = relay_message
        self.events_calls = 0
        self.sent_messages: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            return {"participant": "guest", "room": _active_room()}
        if method == "POST" and url.endswith("/heartbeat"):
            return {"room": _active_room()}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            if self.events_calls == 1:
                return {
                    "room": _active_room(),
                    "events": [
                        {
                            "id": 11,
                            "type": "relay",
                            "payload": {
                                "from": "host",
                                "message": self.relay_message,
                            },
                        }
                    ],
                    "next_cursor": 12,
                }
            return {"room": _closed_room(), "events": [], "next_cursor": 12}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room(turn_count=2)}
        if method == "POST" and url.endswith("/leave"):
            return {"ok": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_codex_bridge_harness_sets_in_reply_to_event_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_plane_recorder: dict[str, list[dict[str, Any]]],
) -> None:
    fake_api = _FakeCodexAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--offline-mock",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_state.json"),
        ],
    )
    codex_cli.main()
    assert fake_api.sent_messages, "bridge should reply to ASK relay"
    meta = fake_api.sent_messages[0].get("meta") or {}
    assert meta.get("in_reply_to_event_id") == 11
    assert runner_plane_recorder["claim"], "bridge should claim runner attempt"
    assert runner_plane_recorder["claim"][0]["managed_certified"] is True
    assert runner_plane_recorder["claim"][0]["recovery_policy"] == "automatic"


def test_codex_bridge_harness_skips_note_expect_reply_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeCodexAPI({"intent": "NOTE", "text": "info", "expect_reply": False, "sender": "host"})
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--offline-mock",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_state_skip.json"),
        ],
    )
    codex_cli.main()
    assert fake_api.sent_messages == []


def test_codex_bridge_auto_prefers_local_codex_cli_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeCodexAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    commands: list[list[str]] = []

    def fake_run(cmd: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        assert isinstance(cmd, list)
        commands.append([str(part) for part in cmd])
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text(
            json.dumps(
                {
                    "intent": "ANSWER",
                    "text": "Local Codex CLI reply",
                    "fills": {},
                    "facts": [],
                    "questions": [],
                    "expect_reply": False,
                    "meta": {"backend": "codex-cli"},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(codex_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_cli_auto_state.json"),
        ],
    )
    codex_cli.main()
    assert commands, "local codex CLI should be invoked when available"
    command = commands[0]
    assert "exec" in command
    assert "-o" in command
    assert "--ephemeral" in command
    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert fake_api.sent_messages[0]["text"] == "Local Codex CLI reply"


def test_codex_bridge_openai_backend_uses_default_model_when_unspecified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeCodexAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    seen: dict[str, str] = {}

    def fake_call_openai(model: str, api_key: str, prompt: str) -> dict[str, Any]:
        seen["model"] = model
        seen["api_key"] = api_key
        seen["prompt"] = prompt
        return {
            "intent": "ANSWER",
            "text": "OpenAI fallback reply",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {"backend": "openai"},
        }

    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(codex_cli, "call_openai", fake_call_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_openai_default_model.json"),
        ],
    )
    codex_cli.main()
    assert seen["model"] == "gpt-5-mini"
    assert seen["api_key"] == "test-key"
    assert fake_api.sent_messages[0]["text"] == "OpenAI fallback reply"


class _FakeCodexKickoffWaitAPI:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.events_calls = 0

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            room = _active_room(turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": False}]
            return {"participant": "host", "room": room}
        if method == "POST" and url.endswith("/heartbeat"):
            room = _active_room(turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": False}]
            return {"room": room}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            room = _active_room(turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": False}]
            if self.events_calls > 1:
                room = _closed_room(reason="timeout")
            return {"room": room, "events": [], "next_cursor": 1}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room(turn_count=1)}
        if method == "POST" and url.endswith("/leave"):
            return {"ok": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_codex_initiator_waits_before_peer_join(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_api = _FakeCodexKickoffWaitAPI()
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "initiator",
            "--start",
            "--offline-mock",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_wait_state.json"),
        ],
    )
    codex_cli.main()
    assert fake_api.sent_messages == []


class _FakeCodexKickoffRaceAPI:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.events_calls = 0

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            room = _active_room(turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            return {"participant": "host", "room": room}
        if method == "POST" and url.endswith("/heartbeat"):
            room = _active_room(turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            return {"room": room}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            if self.events_calls == 1:
                room = _active_room(turn_count=0)
                room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
                return {
                    "room": room,
                    "events": [
                        {
                            "id": 21,
                            "type": "relay",
                            "payload": {
                                "from": "guest",
                                "message": {
                                    "intent": "ASK",
                                    "text": "Pizza tonight?",
                                    "expect_reply": True,
                                    "sender": "guest",
                                },
                            },
                        }
                    ],
                    "next_cursor": 22,
                }
            return {"room": _closed_room(reason="goal_done"), "events": [], "next_cursor": 22}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room(turn_count=1)}
        if method == "POST" and url.endswith("/leave"):
            return {"ok": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_codex_initiator_skips_kickoff_when_peer_message_already_arrived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeCodexKickoffRaceAPI()
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "initiator",
            "--start",
            "--offline-mock",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_kickoff_race.json"),
        ],
    )
    codex_cli.main()
    assert len(fake_api.sent_messages) == 1
    meta = fake_api.sent_messages[0].get("meta") or {}
    assert meta.get("in_reply_to_event_id") == 21


class _FakeCodexKickoffLateRaceAPI(_FakeCodexKickoffRaceAPI):
    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            room = _active_room(turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            return {"participant": "host", "room": room}
        if method == "POST" and url.endswith("/heartbeat"):
            room = _active_room(turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            return {"room": room}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            room = _active_room(turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            if self.events_calls == 1:
                return {"room": room, "events": [], "next_cursor": 1}
            if self.events_calls == 2:
                return {
                    "room": room,
                    "events": [
                        {
                            "id": 31,
                            "type": "relay",
                            "payload": {
                                "from": "guest",
                                "message": {
                                    "intent": "ASK",
                                    "text": "Sushi tonight?",
                                    "expect_reply": True,
                                    "sender": "guest",
                                },
                            },
                        }
                    ],
                    "next_cursor": 32,
                }
            return {"room": _closed_room(reason="goal_done"), "events": [], "next_cursor": 32}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room(turn_count=1)}
        if method == "POST" and url.endswith("/leave"):
            return {"ok": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_codex_initiator_rechecks_before_room_start_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeCodexKickoffLateRaceAPI()
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "initiator",
            "--start",
            "--offline-mock",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_kickoff_late_race.json"),
        ],
    )
    codex_cli.main()
    assert len(fake_api.sent_messages) == 1
    meta = fake_api.sent_messages[0].get("meta") or {}
    assert meta.get("in_reply_to_event_id") == 31


class _FakeCodexRelayLateRaceAPI:
    def __init__(self) -> None:
        self.events_calls = 0
        self.sent_messages: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            return {"participant": "guest", "room": _active_room()}
        if method == "POST" and url.endswith("/heartbeat"):
            return {"room": _active_room(turn_count=max(1, len(self.sent_messages)))}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            if self.events_calls == 1:
                return {
                    "room": _active_room(),
                    "events": [
                        {
                            "id": 41,
                            "type": "relay",
                            "payload": {
                                "from": "host",
                                "message": {
                                    "intent": "ASK",
                                    "text": "Pizza or tacos?",
                                    "expect_reply": True,
                                    "sender": "host",
                                },
                            },
                        }
                    ],
                    "next_cursor": 42,
                }
            if self.events_calls == 2:
                return {
                    "room": _active_room(turn_count=1),
                    "events": [
                        {
                            "id": 43,
                            "type": "relay",
                            "payload": {
                                "from": "host",
                                "message": {
                                    "intent": "DONE",
                                    "text": "Let's lock tacos.",
                                    "expect_reply": False,
                                    "sender": "host",
                                },
                            },
                        }
                    ],
                    "next_cursor": 44,
                }
            room = _closed_room(reason="mutual_done") if self.sent_messages else _active_room(turn_count=1)
            return {"room": room, "events": [], "next_cursor": 44}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room(turn_count=max(1, len(self.sent_messages)))}
        if method == "POST" and url.endswith("/leave"):
            return {"ok": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_codex_rechecks_before_relay_send_when_newer_peer_activity_arrives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeCodexRelayLateRaceAPI()
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    responses = iter(
        [
            {
                "intent": "ASK",
                "text": "How about tacos?",
                "fills": {},
                "facts": [],
                "questions": ["How about tacos?"],
                "expect_reply": True,
                "meta": {},
            },
            {
                "intent": "DONE",
                "text": "Tacos it is.",
                "fills": {},
                "facts": [],
                "questions": [],
                "expect_reply": False,
                "meta": {},
            },
        ]
    )
    monkeypatch.setattr(codex_cli, "call_openai", lambda _model, _api_key, _prompt: next(responses))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_relay_late_race.json"),
        ],
    )
    codex_cli.main()
    assert len(fake_api.sent_messages) == 1
    payload = fake_api.sent_messages[0]
    assert payload["intent"] == "DONE"
    assert (payload.get("meta") or {}).get("in_reply_to_event_id") == 43


def test_codex_state_resume_prevents_duplicate_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "codex_resume_state.json"
    fake_api_first = _FakeCodexAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    monkeypatch.setattr(codex_cli, "http_json", fake_api_first)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--offline-mock",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(state_path),
        ],
    )
    codex_cli.main()
    assert len(fake_api_first.sent_messages) == 1

    fake_api_second = _FakeCodexAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    monkeypatch.setattr(codex_cli, "http_json", fake_api_second)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--offline-mock",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(state_path),
        ],
    )
    codex_cli.main()
    assert fake_api_second.sent_messages == []


class _FakeCodexOwnerLoopAPI:
    def __init__(self) -> None:
        self.events_calls = 0
        self.sent_messages: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            return {"participant": "guest", "room": _active_room()}
        if method == "POST" and url.endswith("/heartbeat"):
            return {"room": _active_room(turn_count=max(1, len(self.sent_messages)))}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            if self.events_calls == 1:
                return {
                    "room": _active_room(),
                    "events": [
                        {
                            "id": 31,
                            "type": "relay",
                            "payload": {
                                "from": "host",
                                "message": {
                                    "intent": "ASK",
                                    "text": "Need your recommendation",
                                    "expect_reply": True,
                                    "sender": "host",
                                },
                            },
                        }
                    ],
                    "next_cursor": 32,
                }
            room = _active_room(turn_count=max(1, len(self.sent_messages)))
            if len(self.sent_messages) >= 2:
                room = _closed_room(reason="goal_done")
            return {"room": room, "events": [], "next_cursor": 32}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room(turn_count=max(1, len(self.sent_messages)))}
        if method == "POST" and url.endswith("/leave"):
            return {"ok": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_codex_owner_loop_sends_owner_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_api = _FakeCodexOwnerLoopAPI()
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    responses = iter(
        [
            {
                "intent": "ASK_OWNER",
                "text": "Need owner preference",
                "fills": {},
                "facts": [],
                "questions": [],
                "expect_reply": False,
                "meta": {},
            },
            {
                "intent": "OWNER_REPLY",
                "text": "Owner prefers sushi",
                "fills": {"decision": "sushi"},
                "facts": [],
                "questions": [],
                "expect_reply": True,
                "meta": {},
            },
        ]
    )
    monkeypatch.setattr(codex_cli, "call_openai", lambda _model, _api_key, _prompt: next(responses))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    state_path = tmp_path / "codex_owner_state.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(state_path),
            "--owner-reply-cmd",
            "printf '%s\\tPrefer sushi tonight\\n' '{owner_req_id}'",
        ],
    )
    codex_cli.main()
    assert len(fake_api.sent_messages) == 2
    first, second = fake_api.sent_messages
    assert first["intent"] == "ASK_OWNER"
    owner_req_id = (first.get("meta") or {}).get("owner_req_id")
    assert owner_req_id
    assert second["intent"] == "OWNER_REPLY"
    assert (second.get("meta") or {}).get("owner_req_id") == owner_req_id
    assert "in_reply_to_event_id" not in (second.get("meta") or {})

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["capabilities"]["strategy"] == "daemon-safe"
    assert saved["capabilities"]["owner_reply_supported"] is True


def test_terminal_coercion_promotes_no_reply_answer_to_done() -> None:
    room = {
        "expected_outcomes": ["owner", "next_step"],
        "fields": {
            "owner": {"value": "host"},
            "next_step": {"value": "draft_release_checklist"},
        },
    }
    message = {
        "intent": "ANSWER",
        "text": "Release checklist owner is host and the next step is drafting it now.",
        "fills": {},
        "facts": [],
        "questions": [],
        "expect_reply": False,
        "meta": {},
    }

    codex_coerced = codex_cli.coerce_terminal_message(message, room)
    openclaw_coerced = openclaw_cli.coerce_terminal_message(message, room)

    assert codex_coerced["intent"] == "DONE"
    assert codex_coerced["expect_reply"] is False
    assert codex_coerced["meta"]["terminal_coercion"] == ["intent->DONE"]

    assert openclaw_coerced["intent"] == "DONE"
    assert openclaw_coerced["expect_reply"] is False
    assert openclaw_coerced["meta"]["terminal_coercion"] == ["intent->DONE"]


def test_terminal_coercion_keeps_answer_when_question_remains() -> None:
    room = {
        "expected_outcomes": ["owner", "next_step"],
        "fields": {
            "owner": {"value": "host"},
            "next_step": {"value": "draft_release_checklist"},
        },
    }
    message = {
        "intent": "ANSWER",
        "text": "I can draft it next. Want me to start?",
        "fills": {},
        "facts": [],
        "questions": ["Want me to start?"],
        "expect_reply": False,
        "meta": {},
    }

    codex_coerced = codex_cli.coerce_terminal_message(message, room)
    openclaw_coerced = openclaw_cli.coerce_terminal_message(message, room)

    assert codex_coerced["intent"] == "ANSWER"
    assert openclaw_coerced["intent"] == "ANSWER"


def test_codex_owner_unavailable_converts_ask_owner_to_ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeCodexAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        codex_cli,
        "call_openai",
        lambda _model, _api_key, _prompt: {
            "intent": "ASK_OWNER",
            "text": "Need owner guidance",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {},
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    state_path = tmp_path / "codex_owner_fallback_state.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(state_path),
        ],
    )
    codex_cli.main()
    assert len(fake_api.sent_messages) == 1
    payload = fake_api.sent_messages[0]
    assert payload["intent"] == "ASK"
    meta = payload.get("meta") or {}
    assert meta.get("owner_unavailable") is True
    assert meta.get("converted_from") == "ASK_OWNER"


def test_codex_auto_uses_local_cli_when_api_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeCodexAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _cmd: "/usr/local/bin/codex")

    invocations: list[list[str]] = []

    def fake_run(cmd: list[str], capture_output: bool, text: bool, timeout: int) -> subprocess.CompletedProcess[str]:
        del capture_output, text, timeout
        invocations.append(cmd)
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text(
            json.dumps(
                {
                    "intent": "ANSWER",
                    "text": "Local Codex reply",
                    "fills": {},
                    "facts": [],
                    "questions": [],
                    "expect_reply": False,
                    "meta": {},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_local_cli.json"),
        ],
    )
    codex_cli.main()
    assert invocations, "local codex cli should be invoked when no API key is available"
    assert invocations[0][0] == "/usr/local/bin/codex"
    assert invocations[0][1] == "exec"
    assert fake_api.sent_messages[0]["text"] == "Local Codex reply"


def test_codex_auto_prefers_openai_when_api_key_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeCodexAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    monkeypatch.setattr(codex_cli, "http_json", fake_api)
    monkeypatch.setattr(codex_cli.time, "sleep", lambda _x: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _cmd: "/usr/local/bin/codex")
    monkeypatch.setattr(
        codex_cli.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local codex cli should not run")),
    )
    openai_prompts: list[str] = []
    monkeypatch.setattr(
        codex_cli,
        "call_openai",
        lambda _model, _api_key, prompt: (
            openai_prompts.append(prompt)
            or {
                "intent": "ANSWER",
                "text": "Responses path reply",
                "fills": {},
                "facts": [],
                "questions": [],
                "expect_reply": False,
                "meta": {},
            }
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-bridge",
            "--base-url",
            "https://api.clawroom.cc",
            "--room-id",
            "room_test",
            "--token",
            "inv_x",
            "--role",
            "responder",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "codex_openai_auto.json"),
        ],
    )
    codex_cli.main()
    assert openai_prompts, "auto backend should keep using OpenAI Responses when API key exists"
    assert fake_api.sent_messages[0]["text"] == "Responses path reply"


class _FakeOpenClawAPI:
    def __init__(self, relay_message: dict[str, Any]) -> None:
        self.relay_message = relay_message
        self.events_calls = 0
        self.sent_messages: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            return {"participant": "guest", "room": _active_room("room_open")}
        if method == "POST" and url.endswith("/heartbeat"):
            return {"room": _active_room("room_open")}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            if self.events_calls == 1:
                return {
                    "room": _active_room("room_open"),
                    "events": [
                        {
                            "id": 21,
                            "type": "relay",
                            "payload": {
                                "from": "host",
                                "message": self.relay_message,
                            },
                        }
                    ],
                    "next_cursor": 22,
                }
            return {"room": _closed_room("room_open"), "events": [], "next_cursor": 22}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room("room_open"), "host_decision": {}}
        if method == "GET" and "/rooms/room_open" in url:
            return {"room": _active_room("room_open")}
        if method == "POST" and url.endswith("/leave"):
            return {"was_online": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_openclaw_bridge_harness_sets_in_reply_to_event_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_plane_recorder: dict[str, list[dict[str, Any]]],
) -> None:
    fake_api = _FakeOpenClawAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    ask_calls: list[str] = []

    def fake_ask_json(
        self: Any,
        room_id: str,
        participant_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del self, room_id, participant_name, session_id
        ask_calls.append(prompt)
        return {
            "intent": "ANSWER",
            "text": "Mock OpenClaw reply",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {},
        }

    monkeypatch.setattr(openclaw_cli, "http_json", fake_api)
    monkeypatch.setattr(openclaw_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(openclaw_cli.OpenClawRunner, "ask_json", fake_ask_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openclaw-bridge",
            "https://api.clawroom.cc/join/room_open?token=inv_x",
            "--role",
            "responder",
            "--preflight-mode",
            "off",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "openclaw_state.json"),
        ],
    )
    openclaw_cli.main()
    assert ask_calls, "runner should be called for reply generation"
    assert fake_api.sent_messages, "bridge should send relay reply"
    meta = fake_api.sent_messages[0].get("meta") or {}
    assert meta.get("in_reply_to_event_id") == 21
    assert runner_plane_recorder["claim"], "bridge should claim runner attempt"
    assert runner_plane_recorder["claim"][0]["managed_certified"] is True
    assert runner_plane_recorder["claim"][0]["recovery_policy"] == "automatic"


class _FakeOpenClawRelayLateRaceAPI:
    def __init__(self) -> None:
        self.events_calls = 0
        self.sent_messages: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            return {"participant": "guest", "room": _active_room("room_open")}
        if method == "POST" and url.endswith("/heartbeat"):
            return {"room": _active_room("room_open")}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            if self.events_calls == 1:
                return {
                    "room": _active_room("room_open"),
                    "events": [
                        {
                            "id": 51,
                            "type": "relay",
                            "payload": {
                                "from": "host",
                                "message": {
                                    "intent": "ASK",
                                    "text": "Pizza or tacos?",
                                    "expect_reply": True,
                                    "sender": "host",
                                },
                            },
                        }
                    ],
                    "next_cursor": 52,
                }
            if self.events_calls == 2:
                return {
                    "room": _active_room("room_open"),
                    "events": [
                        {
                            "id": 53,
                            "type": "relay",
                            "payload": {
                                "from": "host",
                                "message": {
                                    "intent": "DONE",
                                    "text": "Let's lock tacos.",
                                    "expect_reply": False,
                                    "sender": "host",
                                },
                            },
                        }
                    ],
                    "next_cursor": 54,
                }
            return {"room": _closed_room("room_open", reason="mutual_done"), "events": [], "next_cursor": 54}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room("room_open"), "host_decision": {}}
        if method == "GET" and "/rooms/room_open" in url:
            return {"room": _active_room("room_open")}
        if method == "POST" and url.endswith("/leave"):
            return {"was_online": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_openclaw_rechecks_before_relay_send_when_newer_peer_activity_arrives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeOpenClawRelayLateRaceAPI()
    replies = iter(
        [
            {
                "intent": "ASK",
                "text": "How about tacos?",
                "fills": {},
                "facts": [],
                "questions": ["How about tacos?"],
                "expect_reply": True,
                "meta": {},
            },
            {
                "intent": "DONE",
                "text": "Tacos it is.",
                "fills": {},
                "facts": [],
                "questions": [],
                "expect_reply": False,
                "meta": {},
            },
        ]
    )

    def fake_ask_json(
        self: Any,
        room_id: str,
        participant_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del self, room_id, participant_name, prompt, session_id
        return next(replies)

    monkeypatch.setattr(openclaw_cli, "http_json", fake_api)
    monkeypatch.setattr(openclaw_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(openclaw_cli.OpenClawRunner, "ask_json", fake_ask_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openclaw-bridge",
            "https://api.clawroom.cc/join/room_open?token=inv_x",
            "--role",
            "responder",
            "--preflight-mode",
            "off",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "openclaw_relay_late_race.json"),
        ],
    )
    openclaw_cli.main()
    assert len(fake_api.sent_messages) == 1
    payload = fake_api.sent_messages[0]
    assert payload["intent"] == "DONE"
    assert (payload.get("meta") or {}).get("in_reply_to_event_id") == 53


def test_openclaw_bridge_harness_skips_note_expect_reply_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeOpenClawAPI({"intent": "NOTE", "text": "info", "expect_reply": False, "sender": "host"})
    ask_calls: list[str] = []

    def fake_ask_json(
        self: Any,
        room_id: str,
        participant_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del self, room_id, participant_name, prompt, session_id
        ask_calls.append("called")
        return {
            "intent": "ANSWER",
            "text": "should not be used",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {},
        }

    monkeypatch.setattr(openclaw_cli, "http_json", fake_api)
    monkeypatch.setattr(openclaw_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(openclaw_cli.OpenClawRunner, "ask_json", fake_ask_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openclaw-bridge",
            "https://api.clawroom.cc/join/room_open?token=inv_x",
            "--role",
            "responder",
            "--preflight-mode",
            "off",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "openclaw_state_skip.json"),
        ],
    )
    openclaw_cli.main()
    assert ask_calls == []
    assert fake_api.sent_messages == []


class _FakeOpenClawKickoffWaitAPI(_FakeOpenClawAPI):
    def __init__(self) -> None:
        super().__init__({"intent": "ASK", "text": "noop", "expect_reply": True, "sender": "host"})

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            room = _active_room("room_open", turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": False}]
            return {"participant": "host", "room": room}
        if method == "POST" and url.endswith("/heartbeat"):
            room = _active_room("room_open", turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": False}]
            return {"room": room}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            room = _active_room("room_open", turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": False}]
            if self.events_calls > 1:
                room = _closed_room("room_open", reason="timeout")
            return {"room": room, "events": [], "next_cursor": 1}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room("room_open", turn_count=1), "host_decision": {}}
        if method == "GET" and "/rooms/room_open" in url:
            return {"room": _active_room("room_open", turn_count=0)}
        if method == "POST" and url.endswith("/leave"):
            return {"was_online": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_openclaw_initiator_waits_before_peer_join(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeOpenClawKickoffWaitAPI()
    ask_calls: list[str] = []

    def fake_ask_json(
        self: Any,
        room_id: str,
        participant_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del self, room_id, participant_name, prompt, session_id
        ask_calls.append("called")
        return {
            "intent": "ASK",
            "text": "should not send",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": True,
            "meta": {},
        }

    monkeypatch.setattr(openclaw_cli, "http_json", fake_api)
    monkeypatch.setattr(openclaw_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(openclaw_cli.OpenClawRunner, "ask_json", fake_ask_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openclaw-bridge",
            "https://api.clawroom.cc/join/room_open?token=inv_x",
            "--role",
            "initiator",
            "--start",
            "--preflight-mode",
            "off",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "openclaw_wait_state.json"),
        ],
    )
    openclaw_cli.main()
    assert ask_calls == []
    assert fake_api.sent_messages == []


class _FakeOpenClawKickoffRaceAPI(_FakeOpenClawAPI):
    def __init__(self) -> None:
        super().__init__({"intent": "ASK", "text": "noop", "expect_reply": True, "sender": "guest"})

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            room = _active_room("room_open", turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            return {"participant": "host", "room": room}
        if method == "POST" and url.endswith("/heartbeat"):
            room = _active_room("room_open", turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            return {"room": room}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            if self.events_calls == 1:
                room = _active_room("room_open", turn_count=0)
                room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
                return {
                    "room": room,
                    "events": [
                        {
                            "id": 41,
                            "type": "relay",
                            "payload": {
                                "from": "guest",
                                "message": {
                                    "intent": "ASK",
                                    "text": "Pizza tonight?",
                                    "expect_reply": True,
                                    "sender": "guest",
                                },
                            },
                        }
                    ],
                    "next_cursor": 42,
                }
            return {"room": _closed_room("room_open", reason="goal_done"), "events": [], "next_cursor": 42}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room("room_open", turn_count=1), "host_decision": {}}
        if method == "GET" and "/rooms/room_open" in url:
            return {"room": _active_room("room_open", turn_count=0)}
        if method == "POST" and url.endswith("/leave"):
            return {"was_online": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_openclaw_initiator_skips_kickoff_when_peer_message_already_arrived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeOpenClawKickoffRaceAPI()

    def fake_ask_json(
        self: Any,
        room_id: str,
        participant_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del self, room_id, participant_name, prompt, session_id
        return {
            "intent": "ANSWER",
            "text": "Pizza works. Let's do margherita.",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": True,
            "meta": {},
        }

    monkeypatch.setattr(openclaw_cli, "http_json", fake_api)
    monkeypatch.setattr(openclaw_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(openclaw_cli.OpenClawRunner, "ask_json", fake_ask_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openclaw-bridge",
            "https://api.clawroom.cc/join/room_open?token=inv_x",
            "--role",
            "initiator",
            "--start",
            "--preflight-mode",
            "off",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "openclaw_kickoff_race.json"),
        ],
    )
    openclaw_cli.main()
    assert len(fake_api.sent_messages) == 1
    meta = fake_api.sent_messages[0].get("meta") or {}
    assert meta.get("in_reply_to_event_id") == 41


class _FakeOpenClawKickoffLateRaceAPI(_FakeOpenClawKickoffRaceAPI):
    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
    ) -> dict[str, Any]:
        del token, timeout, retries
        if method == "POST" and url.endswith("/join"):
            room = _active_room("room_open", turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            return {"participant": "host", "room": room}
        if method == "POST" and url.endswith("/heartbeat"):
            room = _active_room("room_open", turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            return {"room": room}
        if method == "GET" and "/events?" in url:
            self.events_calls += 1
            room = _active_room("room_open", turn_count=0)
            room["participants"] = [{"name": "host", "joined": True}, {"name": "guest", "joined": True}]
            if self.events_calls == 1:
                return {"room": room, "events": [], "next_cursor": 1}
            if self.events_calls == 2:
                return {
                    "room": room,
                    "events": [
                        {
                            "id": 51,
                            "type": "relay",
                            "payload": {
                                "from": "guest",
                                "message": {
                                    "intent": "ASK",
                                    "text": "Sushi tonight?",
                                    "expect_reply": True,
                                    "sender": "guest",
                                },
                            },
                        }
                    ],
                    "next_cursor": 52,
                }
            return {"room": _closed_room("room_open", reason="goal_done"), "events": [], "next_cursor": 52}
        if method == "POST" and url.endswith("/messages"):
            self.sent_messages.append(payload or {})
            return {"room": _active_room("room_open", turn_count=1), "host_decision": {}}
        if method == "GET" and "/rooms/room_open" in url:
            return {"room": _active_room("room_open", turn_count=0)}
        if method == "POST" and url.endswith("/leave"):
            return {"was_online": True}
        raise AssertionError(f"unexpected request: {method} {url}")


def test_openclaw_initiator_rechecks_before_room_start_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeOpenClawKickoffLateRaceAPI()

    def fake_ask_json(
        self: Any,
        room_id: str,
        participant_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del self, room_id, participant_name, prompt, session_id
        return {
            "intent": "ANSWER",
            "text": "Sushi sounds great. Let's do salmon rolls.",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": True,
            "meta": {},
        }

    monkeypatch.setattr(openclaw_cli, "http_json", fake_api)
    monkeypatch.setattr(openclaw_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(openclaw_cli.OpenClawRunner, "ask_json", fake_ask_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openclaw-bridge",
            "https://api.clawroom.cc/join/room_open?token=inv_x",
            "--role",
            "initiator",
            "--start",
            "--preflight-mode",
            "off",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "openclaw_kickoff_late_race.json"),
        ],
    )
    openclaw_cli.main()
    assert len(fake_api.sent_messages) == 1
    meta = fake_api.sent_messages[0].get("meta") or {}
    assert meta.get("in_reply_to_event_id") == 51


def test_openclaw_state_resume_prevents_duplicate_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "openclaw_resume_state.json"
    ask_calls: list[str] = []

    def fake_ask_json(
        self: Any,
        room_id: str,
        participant_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del self, room_id, participant_name, prompt, session_id
        ask_calls.append("called")
        return {
            "intent": "ANSWER",
            "text": "Mock OpenClaw reply",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {},
        }

    fake_api_first = _FakeOpenClawAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    monkeypatch.setattr(openclaw_cli, "http_json", fake_api_first)
    monkeypatch.setattr(openclaw_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(openclaw_cli.OpenClawRunner, "ask_json", fake_ask_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openclaw-bridge",
            "https://api.clawroom.cc/join/room_open?token=inv_x",
            "--role",
            "responder",
            "--preflight-mode",
            "off",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(state_path),
        ],
    )
    openclaw_cli.main()
    assert len(fake_api_first.sent_messages) == 1

    fake_api_second = _FakeOpenClawAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    monkeypatch.setattr(openclaw_cli, "http_json", fake_api_second)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openclaw-bridge",
            "https://api.clawroom.cc/join/room_open?token=inv_x",
            "--role",
            "responder",
            "--preflight-mode",
            "off",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(state_path),
        ],
    )
    openclaw_cli.main()
    assert fake_api_second.sent_messages == []


def test_openclaw_recovers_from_session_lock_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _FakeOpenClawAPI({"intent": "ASK", "text": "question", "expect_reply": True, "sender": "host"})
    calls = {"n": 0}

    def flaky_ask_json(
        self: Any,
        room_id: str,
        participant_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del self, room_id, participant_name, prompt, session_id
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("session file locked (timeout 10000ms)")
        return {
            "intent": "ANSWER",
            "text": "Recovered",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {},
        }

    monkeypatch.setattr(openclaw_cli, "http_json", fake_api)
    monkeypatch.setattr(openclaw_cli.time, "sleep", lambda _x: None)
    monkeypatch.setattr(openclaw_cli.OpenClawRunner, "ask_json", flaky_ask_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openclaw-bridge",
            "https://api.clawroom.cc/join/room_open?token=inv_x",
            "--role",
            "responder",
            "--preflight-mode",
            "off",
            "--poll-seconds",
            "0",
            "--max-seconds",
            "5",
            "--state-path",
            str(tmp_path / "openclaw_lock_recover_state.json"),
        ],
    )
    openclaw_cli.main()
    assert calls["n"] >= 2
    assert fake_api.sent_messages, "bridge should recover and still send reply"
