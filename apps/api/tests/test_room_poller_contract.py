from __future__ import annotations

import importlib.util
import json
import ssl
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
PRE_FLIGHT = ROOT / "skills" / "clawroom" / "scripts" / "clawroom_preflight.py"
ROOM_POLLER = ROOT / "skills" / "clawroom" / "scripts" / "room_poller.py"
OWNER_REPLY = ROOT / "skills" / "clawroom" / "scripts" / "clawroom_owner_reply.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_report_is_binary(monkeypatch) -> None:
    module = load_module(PRE_FLIGHT, "clawroom_preflight_test")
    monkeypatch.setattr(module, "check_exec_enabled", lambda: (True, "ok"))
    monkeypatch.setattr(module, "check_python3", lambda: (True, "Python 3.x"))
    monkeypatch.setattr(module, "check_writable_workspace", lambda: (True, "/tmp"))
    monkeypatch.setattr(module, "check_openclaw_agent_help", lambda: (True, "--session-id\n--deliver\n"))
    ready = module.build_report()
    assert ready["status"] == "ready"
    assert ready["missing"] == []

    monkeypatch.setattr(module, "check_python3", lambda: (False, "missing"))
    blocked = module.build_report()
    assert blocked["status"] == "not_ready"
    assert "python3" in blocked["missing"]
    assert "ready_candidate" not in json.dumps(blocked)


def test_room_poller_owner_context_validation_and_paths(tmp_path, monkeypatch) -> None:
    module = load_module(ROOM_POLLER, "clawroom_room_poller_test")
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)

    context_path = tmp_path / "owner_context.json"
    context_path.write_text(
        json.dumps(
            {
                "owner_name": "George",
                "owner_role": "Founder",
                "confirmed_facts": ["Based in Shenzhen"],
                "do_not_share": [],
                "task_context": "Sync next week's work",
                "language": "zh",
            }
        ),
        encoding="utf-8",
    )
    context = module.load_owner_context(context_path)
    assert context["owner_name"] == "George"
    assert module.owner_context_path("room_abc", "host_openclaw") == tmp_path / ".clawroom" / "rooms" / "room_abc" / "host_openclaw" / "owner_context.json"
    assert module.pending_question_path("room_abc", "host_openclaw") == tmp_path / ".clawroom" / "rooms" / "room_abc" / "host_openclaw" / "pending_question.json"
    assert module.owner_reply_path("room_abc", "host_openclaw") == tmp_path / ".clawroom" / "rooms" / "room_abc" / "host_openclaw" / "owner_reply.json"
    assert module.poller_pid_path("room_abc", "host_openclaw") == tmp_path / ".clawroom" / "rooms" / "room_abc" / "host_openclaw" / "poller.pid"


def test_room_poller_prompt_blocks_fact_invention() -> None:
    module = load_module(ROOM_POLLER, "clawroom_room_poller_prompt_test")
    prompt = module.build_reply_prompt(
        role="guest",
        room={
            "topic": "Next week work sync",
            "goal": "Align next week's work",
            "required_fields": ["weekly_tasks", "handoff_items"],
            "fields": {},
        },
        latest_event=None,
        owner_context={
            "owner_name": "George",
            "owner_role": "Founder",
            "confirmed_facts": ["Based in Shenzhen"],
            "do_not_share": ["Revenue"],
            "task_context": "Wants to sync next week's work",
            "language": "zh",
        },
        has_started=False,
    )
    assert "Use only the confirmed facts above as factual owner information." in prompt
    assert "If a required fact is missing, use ASK_OWNER instead of inventing it." in prompt
    assert "Never mention room mechanics, tokens, pollers, protocol, fields, relay, or statuses." in prompt


def test_room_poller_sanitizes_fills_to_required_fields() -> None:
    module = load_module(ROOM_POLLER, "clawroom_room_poller_sanitize_test")
    message = module.sanitize_message_for_room(
        {
            "intent": "ANSWER",
            "text": "reply",
            "fills": {
                "next_week_tasks": "Investor meetings",
                "owner_name": "George",
                "time_nodes": "ASK_OWNER",
            },
            "expect_reply": True,
        },
        {"required_fields": ["next_week_tasks", "time_nodes"]},
    )
    assert message["fills"] == {"next_week_tasks": "Investor meetings"}


def test_owner_reply_script_requires_pending_question(tmp_path, monkeypatch) -> None:
    module = load_module(OWNER_REPLY, "clawroom_owner_reply_test")
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    room_dir = tmp_path / ".clawroom" / "rooms" / "room_abc" / "host_openclaw"
    room_dir.mkdir(parents=True, exist_ok=True)
    (room_dir / "pending_question.json").write_text(
        json.dumps({"request_id": "req_123", "question": "Need one answer"}),
        encoding="utf-8",
    )
    assert module.find_pending_rooms() == [("room_abc", "host_openclaw")]


def test_room_poller_wait_for_owner_reply_stops_when_room_closes(tmp_path, monkeypatch) -> None:
    module = load_module(ROOM_POLLER, "clawroom_room_poller_wait_test")
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)

    context_path = tmp_path / "owner_context.json"
    context_path.write_text(
        json.dumps(
            {
                "owner_name": "George",
                "owner_role": "Founder",
                "confirmed_facts": ["Based in Shenzhen"],
                "do_not_share": [],
                "task_context": "Sync next week's work",
                "language": "zh",
            }
        ),
        encoding="utf-8",
    )

    args = SimpleNamespace(
        room_id="room_abc",
        participant_token="ptok_abc",
        join_url="",
        api_base="https://api.clawroom.cc",
        owner_context_file=str(context_path),
        role="guest",
        participant_name=None,
        agent_id="main",
        owner_session_id="main",
        session_id="clawroom-room_abc",
        client_name="ClawRoomPoller",
        poll_seconds=0.01,
        openclaw_timeout=30,
        owner_wait_timeout=1,
        heartbeat_seconds=0.01,
        thinking="minimal",
        reply_channel=None,
        reply_to=None,
        reply_account=None,
        after=0,
    )
    poller = module.Poller(args)

    calls = {"heartbeat": 0, "fetch_room": 0}
    monkeypatch.setattr(poller, "read_owner_reply", lambda request_id: None)
    monkeypatch.setattr(poller, "maybe_heartbeat", lambda force=False: calls.__setitem__("heartbeat", calls["heartbeat"] + 1))

    def fake_fetch_room():
        calls["fetch_room"] += 1
        return {"status": "closed"}

    monkeypatch.setattr(poller, "fetch_room", fake_fetch_room)
    reply = poller.wait_for_owner_reply("req_123", "Need one answer")
    assert reply is None
    assert calls["heartbeat"] >= 1
    assert calls["fetch_room"] >= 1


def test_room_poller_uses_certifi_backed_ssl_context(monkeypatch) -> None:
    module = load_module(ROOM_POLLER, "clawroom_room_poller_tls_test")
    monkeypatch.setattr(module, "_SSL_CONTEXT", None)

    sentinel = ssl.create_default_context()

    class _FakeCertifi:
        @staticmethod
        def where() -> str:
            return "/tmp/fake-ca.pem"

    def fake_create_default_context(*, cafile=None):
        assert cafile == "/tmp/fake-ca.pem"
        return sentinel

    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "certifi":
            return _FakeCertifi()
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(ssl, "create_default_context", fake_create_default_context)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert module.ssl_context() is sentinel
