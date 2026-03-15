from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[3]
SHELL_BRIDGE = ROOT / "skills" / "clawroom" / "scripts" / "openclaw_shell_bridge.sh"


@dataclass
class _SmokeState:
    room_id: str = "room_shell_smoke"
    token: str = "inv_shell_smoke"
    participant_token: str = "pt_shell_smoke"
    joined: bool = False
    online: bool = False
    status: str = "active"
    stop_reason: str | None = None
    turn_count: int = 1
    messages: list[dict] = field(default_factory=list)
    events_calls: int = 0
    heartbeat_calls: int = 0
    leave_calls: int = 0
    runner_claim_calls: int = 0
    runner_renew_calls: int = 0
    runner_release_calls: int = 0
    execution_mode: str = "compatibility"
    runner_certification: str = "none"
    automatic_recovery_eligible: bool = False
    attempt_status: str = "pending"
    active_runner_id: str | None = None
    attempt_id: str | None = None
    events_delay_seconds: float = 0.0
    delayed_events_once: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def room_snapshot(self) -> dict:
        return {
            "id": self.room_id,
            "topic": "shell smoke",
            "goal": "validate shell bridge loop",
            "status": self.status,
            "stop_reason": self.stop_reason,
            "turn_count": self.turn_count,
            "execution_mode": self.execution_mode,
            "runner_certification": self.runner_certification,
            "automatic_recovery_eligible": self.automatic_recovery_eligible,
            "attempt_status": self.attempt_status,
            "active_runner_id": self.active_runner_id,
            "participants": [
                {
                    "name": "guest",
                    "joined": self.joined,
                    "online": self.online,
                    "done": False,
                    "waiting_owner": False,
                },
                {
                    "name": "host",
                    "joined": True,
                    "online": True,
                    "done": False,
                    "waiting_owner": False,
                },
            ],
        }


def _handler_factory(state: _SmokeState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            del fmt, args
            return

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _check_token(self) -> bool:
            token = self.headers.get("X-Invite-Token", "")
            return token in {state.token, state.participant_token}

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) < 3 or parts[0] != "rooms" or parts[1] != state.room_id:
                self._json(404, {"error": "not_found"})
                return
            if not self._check_token():
                self._json(403, {"error": "bad_token"})
                return
            op = parts[2]

            if op == "join":
                with state.lock:
                    state.joined = True
                    state.online = True
                self._json(200, {"participant": "guest", "participant_token": state.participant_token, "room": state.room_snapshot()})
                return
            if op == "heartbeat":
                with state.lock:
                    state.online = True
                    state.heartbeat_calls += 1
                self._json(200, {"room": state.room_snapshot()})
                return
            if len(parts) >= 4 and parts[2] == "runner":
                payload = self._read_json()
                op2 = parts[3]
                with state.lock:
                    state.execution_mode = str(payload.get("execution_mode") or state.execution_mode)
                    managed_certified = (
                        bool(payload.get("managed_certified"))
                        if "managed_certified" in payload
                        else state.runner_certification == "certified"
                    )
                    recovery_policy = (
                        str(payload.get("recovery_policy") or "")
                        if "recovery_policy" in payload
                        else ("automatic" if state.automatic_recovery_eligible else "takeover_only")
                    )
                    state.runner_certification = "certified" if managed_certified else ("candidate" if state.execution_mode != "compatibility" else "none")
                    state.automatic_recovery_eligible = managed_certified and recovery_policy == "automatic"
                    state.active_runner_id = str(payload.get("runner_id") or state.active_runner_id or "") or state.active_runner_id
                    state.attempt_id = str(payload.get("attempt_id") or state.attempt_id or "rattempt_shell_smoke")
                    state.attempt_status = str(payload.get("status") or state.attempt_status)
                    if op2 == "claim":
                        state.runner_claim_calls += 1
                    elif op2 == "renew":
                        state.runner_renew_calls += 1
                    elif op2 == "release":
                        state.runner_release_calls += 1
                        state.attempt_status = str(payload.get("status") or "exited")
                        state.active_runner_id = None
                    else:
                        self._json(404, {"error": "unsupported_runner"})
                        return
                self._json(200, {"participant": "guest", "attempt_id": state.attempt_id, "room": state.room_snapshot()})
                return
            if op == "messages":
                payload = self._read_json()
                with state.lock:
                    state.messages.append(payload)
                    state.turn_count += 1
                    state.status = "closed"
                    state.stop_reason = "goal_done"
                self._json(200, {"room": state.room_snapshot(), "host_decision": {}})
                return
            if op == "leave":
                with state.lock:
                    state.online = False
                    state.leave_calls += 1
                self._json(200, {"ok": True, "was_online": True})
                return
            self._json(404, {"error": "unsupported_post"})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]

            if parsed.path == f"/join/{state.room_id}":
                token = parse_qs(parsed.query).get("token", [""])[0]
                if token != state.token:
                    self._json(403, {"error": "bad_token"})
                    return
                self._json(200, {"participant": "guest", "room": state.room_snapshot()})
                return

            if len(parts) < 2 or parts[0] != "rooms" or parts[1] != state.room_id:
                self._json(404, {"error": "not_found"})
                return
            if not self._check_token():
                self._json(403, {"error": "bad_token"})
                return

            if len(parts) == 2:
                self._json(200, {"room": state.room_snapshot()})
                return

            op = parts[2]
            if op == "events":
                after = int(parse_qs(parsed.query).get("after", ["0"])[0] or "0")
                with state.lock:
                    state.events_calls += 1
                    delay = state.events_delay_seconds if not state.delayed_events_once else 0.0
                    if delay > 0:
                        state.delayed_events_once = True
                    if state.status != "active":
                        self._json(200, {"room": state.room_snapshot(), "events": [], "next_cursor": max(after, 2)})
                        return
                if delay > 0:
                    threading.Event().wait(delay)
                with state.lock:
                    if after < 1:
                        relay = {
                            "id": 1,
                            "type": "relay",
                            "payload": {
                                "from": "host",
                                "message": {
                                    "sender": "host",
                                    "intent": "ASK",
                                    "text": "Please respond briefly",
                                    "fills": {},
                                    "facts": [],
                                    "questions": [],
                                    "expect_reply": True,
                                    "meta": {},
                                },
                            },
                        }
                        self._json(200, {"room": state.room_snapshot(), "events": [relay], "next_cursor": 2})
                        return
                self._json(200, {"room": state.room_snapshot(), "events": [], "next_cursor": max(after, 2)})
                return

            if op == "result":
                self._json(
                    200,
                    {
                        "result": {
                            "summary": "shell smoke",
                            "status": state.status,
                            "stop_reason": state.stop_reason,
                        }
                    },
                )
                return
            self._json(404, {"error": "unsupported_get"})

    return Handler


def _write_fake_openclaw(fake_dir: Path) -> Path:
    fake = fake_dir / "openclaw"
    fake.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
cat <<'JSON'
{"payloads":[{"text":"{\\"intent\\":\\"NOTE\\",\\"text\\":\\"ack from mock\\",\\"fills\\":{},\\"facts\\":[],\\"questions\\":[],\\"expect_reply\\":true,\\"meta\\":{}}"}]}
JSON
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _write_fake_openclaw_lock_once(fake_dir: Path, marker_file: Path) -> Path:
    fake = fake_dir / "openclaw"
    fake.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [ ! -f "{marker_file}" ]; then
  echo 1 > "{marker_file}"
  echo "session file locked (timeout 10000ms)" >&2
  exit 1
fi
cat <<'JSON'
{{"payloads":[{{"text":"{{\\"intent\\":\\"ANSWER\\",\\"text\\":\\"recovered after lock\\",\\"fills\\":{{}},\\"facts\\":[],\\"questions\\":[],\\"expect_reply\\":false,\\"meta\\":{{}}}}"}}]}}
JSON
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def test_shell_bridge_smoke_join_send_note_normalized(tmp_path: Path) -> None:
    state = _SmokeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir(parents=True, exist_ok=True)
        _write_fake_openclaw(fake_bin)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

        join_url = f"http://127.0.0.1:{port}/join/{state.room_id}?token={state.token}"
        proc = subprocess.run(
            [
                "bash",
                str(SHELL_BRIDGE),
                join_url,
                "--role",
                "responder",
                "--max-seconds",
                "8",
                "--poll-seconds",
                "0.1",
                "--heartbeat-seconds",
                "1",
                "--auto-install",
                "off",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert proc.returncode == 0, f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        assert state.joined is True
        assert state.heartbeat_calls >= 1
        assert state.leave_calls == 1
        assert state.runner_claim_calls >= 1
        assert state.runner_renew_calls >= 1
        assert state.runner_release_calls >= 1
        assert state.execution_mode == "managed_attached"
        assert state.runner_certification == "candidate"
        assert state.automatic_recovery_eligible is False
        assert state.messages, "shell bridge should send one relay reply"
        msg = state.messages[0]
        assert msg.get("intent") == "NOTE"
        assert msg.get("expect_reply") is False, "NOTE should be normalized to expect_reply=false"
        meta = msg.get("meta") or {}
        assert meta.get("in_reply_to_event_id") == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shell_bridge_smoke_recovers_from_session_lock(tmp_path: Path) -> None:
    state = _SmokeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        fake_bin = tmp_path / "bin_lock"
        fake_bin.mkdir(parents=True, exist_ok=True)
        marker_file = tmp_path / "openclaw_lock_once.marker"
        _write_fake_openclaw_lock_once(fake_bin, marker_file)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

        join_url = f"http://127.0.0.1:{port}/join/{state.room_id}?token={state.token}"
        proc = subprocess.run(
            [
                "bash",
                str(SHELL_BRIDGE),
                join_url,
                "--role",
                "responder",
                "--max-seconds",
                "8",
                "--poll-seconds",
                "0.1",
                "--heartbeat-seconds",
                "1",
                "--auto-install",
                "off",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert proc.returncode == 0, f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        assert state.messages, "bridge should recover and send a message after lock error"
        assert state.messages[0].get("intent") == "ANSWER"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shell_bridge_signal_handler_is_bash32_safe(tmp_path: Path) -> None:
    state = _SmokeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        fake_bin = tmp_path / "bin_signal"
        fake_bin.mkdir(parents=True, exist_ok=True)
        _write_fake_openclaw(fake_bin)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

        join_url = f"http://127.0.0.1:{port}/join/{state.room_id}?token={state.token}"
        proc = subprocess.Popen(
            [
                "bash",
                str(SHELL_BRIDGE),
                join_url,
                "--role",
                "initiator",
                "--max-seconds",
                "30",
                "--poll-seconds",
                "0.1",
                "--heartbeat-seconds",
                "1",
                "--auto-install",
                "off",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for _ in range(50):
                if state.runner_claim_calls >= 1:
                    break
                threading.Event().wait(0.05)
            threading.Event().wait(0.1)
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        assert proc.returncode == 0, f"rc={proc.returncode}\nstdout={stdout}\nstderr={stderr}"
        assert "bad substitution" not in stderr
        assert "signal received TERM" in stderr
        assert state.runner_release_calls >= 1
        assert state.leave_calls == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shell_bridge_watchdog_keeps_heartbeats_during_blocked_event_poll(tmp_path: Path) -> None:
    state = _SmokeState(events_delay_seconds=3.0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        fake_bin = tmp_path / "bin_watchdog"
        fake_bin.mkdir(parents=True, exist_ok=True)
        _write_fake_openclaw(fake_bin)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
        env["CLAWROOM_CURL_CONNECT_TIMEOUT"] = "1"
        env["CLAWROOM_CURL_MAX_TIME"] = "5"

        join_url = f"http://127.0.0.1:{port}/join/{state.room_id}?token={state.token}"
        proc = subprocess.Popen(
            [
                "bash",
                str(SHELL_BRIDGE),
                join_url,
                "--role",
                "responder",
                "--max-seconds",
                "20",
                "--poll-seconds",
                "0.1",
                "--heartbeat-seconds",
                "1",
                "--auto-install",
                "off",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for _ in range(80):
                if state.heartbeat_calls >= 3 and state.runner_renew_calls >= 3:
                    break
                threading.Event().wait(0.1)
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        assert state.events_calls >= 1
        assert state.heartbeat_calls >= 3, f"stdout={stdout}\nstderr={stderr}"
        assert state.runner_renew_calls >= 3, f"stdout={stdout}\nstderr={stderr}"
        assert "heartbeat watchdog started" in stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shell_bridge_can_self_certify_dedicated_relay_agent(tmp_path: Path) -> None:
    state = _SmokeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        fake_bin = tmp_path / "bin_cert"
        fake_bin.mkdir(parents=True, exist_ok=True)
        _write_fake_openclaw(fake_bin)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
        env["CLAWROOM_MANAGED_CERTIFY"] = "1"
        env["CLAWROOM_RUNTIME_STATE_ROOT"] = str(tmp_path / "relay-state")

        join_url = f"http://127.0.0.1:{port}/join/{state.room_id}?token={state.token}"
        proc = subprocess.run(
            [
                "bash",
                str(SHELL_BRIDGE),
                join_url,
                "--agent-id",
                "clawroom-relay",
                "--role",
                "responder",
                "--max-seconds",
                "8",
                "--poll-seconds",
                "0.1",
                "--heartbeat-seconds",
                "1",
                "--auto-install",
                "off",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert proc.returncode == 0, f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        assert state.runner_certification == "certified"
        assert state.automatic_recovery_eligible is True
        assert "managed certification enabled for dedicated relay agent" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
