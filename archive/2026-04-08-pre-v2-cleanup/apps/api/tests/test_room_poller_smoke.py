from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[3]
ROOM_POLLER = ROOT / "skills" / "clawroom" / "scripts" / "room_poller.py"


@dataclass
class _PollerSmokeState:
    room_id: str = "room_poller_smoke"
    participant_token: str = "ptok_poller_smoke"
    heartbeat_calls: int = 0
    message_calls: int = 0
    deliver_calls: int = 0
    status: str = "active"
    stop_reason: str | None = None
    turn_count: int = 0
    messages: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def room_snapshot(self) -> dict:
        return {
            "id": self.room_id,
            "topic": "Next week work sync",
            "goal": "Align next week's work",
            "status": self.status,
            "stop_reason": self.stop_reason,
            "turn_count": self.turn_count,
            "required_fields": ["weekly_tasks", "handoff_items"],
            "fields": {
                "weekly_tasks": {"value": "Meet investors in Beijing"},
                "handoff_items": {"value": "Share investor notes with the other owner"},
            },
            "participants": [
                {"name": "host_openclaw", "joined": True, "online": True, "done": False, "waiting_owner": False},
                {"name": "counterpart_openclaw", "joined": True, "online": True, "done": False, "waiting_owner": False},
            ],
        }


@dataclass
class _OwnerLoopSmokeState:
    room_id: str = "room_owner_loop_smoke"
    participant_token: str = "ptok_owner_loop_smoke"
    heartbeat_calls: int = 0
    messages: list[dict] = field(default_factory=list)
    status: str = "active"
    stop_reason: str | None = None
    turn_count: int = 1
    owner_wait_emitted: bool = False
    owner_reply_seen: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def room_snapshot(self) -> dict:
        return {
            "id": self.room_id,
            "topic": "Next week work sync",
            "goal": "Align next week's work",
            "status": self.status,
            "stop_reason": self.stop_reason,
            "turn_count": self.turn_count,
            "required_fields": ["weekly_tasks"],
            "fields": {
                "weekly_tasks": {"value": "Meet investors in Beijing" if self.owner_reply_seen else ""},
            },
            "participants": [
                {"name": "host_openclaw", "joined": True, "online": True, "done": False, "waiting_owner": False},
                {"name": "counterpart_openclaw", "joined": True, "online": True, "done": False, "waiting_owner": self.owner_wait_emitted and not self.owner_reply_seen},
            ],
        }


def _handler_factory(state: _PollerSmokeState):
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
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _authorized(self) -> bool:
            return self.headers.get("X-Participant-Token", "") == state.participant_token

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) < 3 or parts[0] != "rooms" or parts[1] != state.room_id or not self._authorized():
                self._json(404, {"error": "not_found"})
                return
            op = parts[2]

            if op == "heartbeat":
                with state.lock:
                    state.heartbeat_calls += 1
                self._json(200, {"room": state.room_snapshot()})
                return

            if op == "messages":
                payload = self._read_json()
                with state.lock:
                    state.message_calls += 1
                    state.messages.append(payload)
                    state.turn_count += 1
                    state.status = "closed"
                    state.stop_reason = "goal_done"
                self._json(200, {"room": state.room_snapshot(), "host_decision": {}})
                return

            self._json(404, {"error": "unsupported_post"})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) < 2 or parts[0] != "rooms" or parts[1] != state.room_id or not self._authorized():
                self._json(404, {"error": "not_found"})
                return

            if len(parts) == 2:
                self._json(200, {"room": state.room_snapshot()})
                return

            op = parts[2]
            if op == "events":
                after = int(parse_qs(parsed.query).get("after", ["0"])[0] or "0")
                next_cursor = max(after, 1)
                self._json(200, {"room": state.room_snapshot(), "events": [], "next_cursor": next_cursor})
                return
            if op == "result":
                self._json(
                    200,
                    {
                        "room": state.room_snapshot(),
                        "result": {
                            "status": state.status,
                            "stop_reason": state.stop_reason,
                            "fields": state.room_snapshot()["fields"],
                        },
                    },
                )
                return

            self._json(404, {"error": "unsupported_get"})

    return Handler


def _owner_loop_handler_factory(state: _OwnerLoopSmokeState):
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
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _authorized(self) -> bool:
            return self.headers.get("X-Participant-Token", "") == state.participant_token

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) < 3 or parts[0] != "rooms" or parts[1] != state.room_id or not self._authorized():
                self._json(404, {"error": "not_found"})
                return
            op = parts[2]

            if op == "heartbeat":
                with state.lock:
                    state.heartbeat_calls += 1
                self._json(200, {"room": state.room_snapshot()})
                return

            if op == "messages":
                payload = self._read_json()
                with state.lock:
                    state.messages.append(payload)
                    state.turn_count += 1
                    intent = str(payload.get("intent") or "")
                    if intent == "ASK_OWNER":
                        state.owner_wait_emitted = True
                    if intent == "OWNER_REPLY":
                        state.owner_reply_seen = True
                        state.status = "closed"
                        state.stop_reason = "goal_done"
                self._json(200, {"room": state.room_snapshot(), "host_decision": {}})
                return

            self._json(404, {"error": "unsupported_post"})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) < 2 or parts[0] != "rooms" or parts[1] != state.room_id or not self._authorized():
                self._json(404, {"error": "not_found"})
                return

            if len(parts) == 2:
                self._json(200, {"room": state.room_snapshot()})
                return

            op = parts[2]
            if op == "events":
                after = int(parse_qs(parsed.query).get("after", ["0"])[0] or "0")
                events: list[dict] = []
                next_cursor = after
                with state.lock:
                    if after < 1:
                        events = [
                            {
                                "id": 1,
                                "type": "relay",
                                "payload": {
                                    "participant": "host_openclaw",
                                    "message": {
                                        "sender": "host_openclaw",
                                        "intent": "ANSWER",
                                        "text": "Share your side of next week's work plan.",
                                        "expect_reply": True,
                                    },
                                },
                            }
                        ]
                        next_cursor = 1
                    elif state.owner_wait_emitted and not state.owner_reply_seen and after < 2:
                        events = [
                            {
                                "id": 2,
                                "type": "owner_wait",
                                "payload": {
                                    "participant": "counterpart_openclaw",
                                    "owner_req_id": "req_owner_smoke",
                                    "text": "What exactly is next week's main task?",
                                },
                            }
                        ]
                        next_cursor = 2
                self._json(200, {"room": state.room_snapshot(), "events": events, "next_cursor": next_cursor})
                return
            if op == "result":
                self._json(
                    200,
                    {
                        "room": state.room_snapshot(),
                        "result": {
                            "status": state.status,
                            "stop_reason": state.stop_reason,
                            "fields": state.room_snapshot()["fields"],
                        },
                    },
                )
                return

            self._json(404, {"error": "unsupported_get"})

    return Handler


def _write_fake_openclaw_script(path: Path, log_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from __future__ import annotations",
                "import json, sys",
                f"LOG = {str(log_path)!r}",
                "args = sys.argv[1:]",
                "with open(LOG, 'a', encoding='utf-8') as fh:",
                "    fh.write(json.dumps(args) + '\\n')",
                "if '--deliver' in args:",
                "    raise SystemExit(0)",
                "payload = {",
                "  'result': {",
                "    'payloads': [",
                "      {",
                "        'text': json.dumps({",
                "          'intent': 'ANSWER',",
                "          'text': 'Here is my side of next week. I will share the plan and ask for handoff items.',",
                "          'fills': {'weekly_tasks': 'Meet investors in Beijing', 'handoff_items': 'Share investor notes with the other owner'},",
                "          'expect_reply': True",
                "        })",
                "      }",
                "    ]",
                "  }",
                "}",
                "print(json.dumps(payload))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_room_poller_host_smoke(tmp_path) -> None:
    state = _PollerSmokeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    room_dir = tmp_path / ".clawroom" / "rooms" / state.room_id / "host_openclaw"
    room_dir.mkdir(parents=True, exist_ok=True)
    context_path = room_dir / "owner_context.json"
    context_path.write_text(
        json.dumps(
            {
                "owner_name": "George",
                "owner_role": "Founder",
                "confirmed_facts": ["Based in Shenzhen", "Going to Beijing next week to meet investors"],
                "do_not_share": [],
                "task_context": "Wants to sync next week's work schedule with another owner",
                "language": "zh",
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "openclaw.log"
    _write_fake_openclaw_script(fake_bin / "openclaw", log_path)

    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["CLAWROOM_STATE_ROOT"] = str(tmp_path / ".clawroom")
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOM_POLLER),
            "--api-base",
            f"http://127.0.0.1:{server.server_address[1]}",
            "--room-id",
            state.room_id,
            "--participant-token",
            state.participant_token,
            "--owner-context-file",
            str(context_path),
            "--role",
            "host",
            "--poll-seconds",
            "0.05",
            "--heartbeat-seconds",
            "0.05",
            "--openclaw-timeout",
            "10",
        ],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    server.shutdown()
    thread.join(timeout=2)

    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert state.heartbeat_calls >= 1
    assert state.message_calls == 1
    assert state.messages[0]["fills"]["weekly_tasks"] == "Meet investors in Beijing"
    assert (room_dir / "final_result.json").exists()
    deliver_lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any("--deliver" in line for line in deliver_lines)


def test_room_poller_owner_loop_smoke(tmp_path) -> None:
    state = _OwnerLoopSmokeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _owner_loop_handler_factory(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    room_dir = tmp_path / ".clawroom" / "rooms" / state.room_id / "counterpart_openclaw"
    room_dir.mkdir(parents=True, exist_ok=True)
    context_path = room_dir / "owner_context.json"
    context_path.write_text(
        json.dumps(
            {
                "owner_name": "George",
                "owner_role": "Founder",
                "confirmed_facts": ["Based in Shenzhen"],
                "do_not_share": [],
                "task_context": "Wants to sync next week's work schedule with another owner",
                "language": "zh",
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "openclaw-owner-loop.log"
    script_path = fake_bin / "openclaw"
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from __future__ import annotations",
                "import json, sys",
                f"LOG = {str(log_path)!r}",
                "args = sys.argv[1:]",
                "message = args[args.index('--message') + 1] if '--message' in args else ''",
                "with open(LOG, 'a', encoding='utf-8') as fh:",
                "    fh.write(json.dumps({'args': args, 'message': message}) + '\\n')",
                "if '--deliver' in args:",
                "    raise SystemExit(0)",
                "if 'resuming a ClawRoom after asking your owner' in message:",
                "    payload = {'result': {'payloads': [{'text': json.dumps({'intent': 'OWNER_REPLY', 'text': 'My next week is Beijing investor meetings.', 'fills': {'weekly_tasks': 'Meet investors in Beijing'}, 'expect_reply': True})}]}}",
                "else:",
                "    payload = {'result': {'payloads': [{'text': json.dumps({'intent': 'ASK_OWNER', 'text': 'I need one owner detail before I answer.', 'fills': {}, 'expect_reply': False})}]}}",
                "print(json.dumps(payload))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)

    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["CLAWROOM_STATE_ROOT"] = str(tmp_path / ".clawroom")
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    def _write_owner_reply() -> None:
        pending_path = room_dir / "pending_question.json"
        reply_path = room_dir / "owner_reply.json"
        for _ in range(100):
            if pending_path.exists():
                question = json.loads(pending_path.read_text(encoding="utf-8"))
                reply_path.write_text(
                    json.dumps({"request_id": question["request_id"], "reply": "I will be in Beijing meeting investors."}),
                    encoding="utf-8",
                )
                return
            threading.Event().wait(0.05)

    reply_thread = threading.Thread(target=_write_owner_reply, daemon=True)
    reply_thread.start()

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOM_POLLER),
            "--api-base",
            f"http://127.0.0.1:{server.server_address[1]}",
            "--room-id",
            state.room_id,
            "--participant-token",
            state.participant_token,
            "--owner-context-file",
            str(context_path),
            "--role",
            "guest",
            "--poll-seconds",
            "0.05",
            "--heartbeat-seconds",
            "0.05",
            "--openclaw-timeout",
            "10",
            "--owner-wait-timeout",
            "5",
        ],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    reply_thread.join(timeout=2)
    server.shutdown()
    thread.join(timeout=2)

    assert proc.returncode == 0, proc.stderr or proc.stdout
    intents = [message["intent"] for message in state.messages]
    assert intents[:2] == ["ASK_OWNER", "OWNER_REPLY"]
    assert state.owner_reply_seen is True
    deliver_lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any("--deliver" in entry["args"] for entry in deliver_lines)
