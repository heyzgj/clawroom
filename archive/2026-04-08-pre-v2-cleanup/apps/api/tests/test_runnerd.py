from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from runnerd.app import create_app
import runnerd.owner_reply_cli as owner_reply_cli
from runnerd.models import WakePackage, parse_wake_package_text, render_wake_package
import runnerd.submit_cli as submit_cli
from runnerd.owner_reply_cli import submit_owner_reply as submit_owner_reply_http
from runnerd.service import OWNER_REPLY_OVERDUE_SECONDS, RUNNER_NOT_CLAIMED_SECONDS, RunnerdService
from runnerd.submit_cli import parse_package_input, submit_package


class FakeProc:
    def __init__(self, *, pid: int = 4242, poll_value: int | None = None) -> None:
        self.pid = pid
        self._poll_value = poll_value
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self._poll_value

    def terminate(self) -> None:
        self.terminated = True
        self._poll_value = 0

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        if self._poll_value is None:
            self._poll_value = 0
        return self._poll_value

    def kill(self) -> None:
        self.killed = True
        self._poll_value = -9


class CapturedPopen:
    def __init__(self) -> None:
        self.cmd: list[str] | None = None
        self.cwd = None
        self.env = None
        self.stdout = None
        self.stderr = None
        self.text = None
        self.proc = FakeProc(pid=5151, poll_value=None)

    def __call__(self, cmd, **kwargs):  # type: ignore[no-untyped-def]
        self.cmd = list(cmd)
        self.cwd = kwargs.get("cwd")
        self.env = kwargs.get("env")
        self.stdout = kwargs.get("stdout")
        self.stderr = kwargs.get("stderr")
        self.text = kwargs.get("text")
        return self.proc


class _TestClientProxy:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def __enter__(self) -> _TestClientProxy:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False

    def post(self, url: str, json: dict[str, object]):
        path = urlparse(url).path
        return self.client.post(path, json=json)


def sample_package(**overrides: object) -> WakePackage:
    base = {
        "coordination_id": "coord_123",
        "wake_request_id": "wake_123",
        "room_id": "room_123",
        "join_link": "https://api.clawroom.cc/join/room_123?token=inv_123",
        "role": "responder",
        "task_summary": "Review the current implementation and give a decision.",
        "owner_context": "The owner wants a clear recommendation.",
        "expected_output": "A concise review result.",
        "deadline_at": "2026-03-11T12:00:00Z",
        "preferred_runner_kind": "openclaw_bridge",
        "sender_owner_label": "alice",
        "sender_gateway_label": "telegram-openclaw",
    }
    base.update(overrides)
    return WakePackage.model_validate(base)


def sample_state(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "base_url": "https://api.clawroom.cc",
        "room_id": "room_123",
        "token": "part_123",
        "participant": "guest",
        "runner_id": "openclaw:test",
        "attempt_id": "attempt_123",
        "conversation": {"pending_owner_req_id": None},
        "health": {"status": "active", "last_error": "", "recent_note": "poll_idle"},
    }
    payload.update(overrides)
    return payload


def build_service(tmp_path: Path, monkeypatch, *, proc: FakeProc | None = None, state: dict[str, object] | None = None) -> RunnerdService:
    fake_proc = proc or FakeProc()
    fake_state = state

    service = RunnerdService(state_root=tmp_path / "runnerd")

    def fake_spawn_bridge(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        return fake_proc

    monkeypatch.setattr(service, "_spawn_bridge", fake_spawn_bridge)
    if fake_state is not None:
        monkeypatch.setattr(service, "_load_bridge_state", lambda path: fake_state)
    return service


def test_wake_package_round_trip() -> None:
    package = sample_package()
    rendered = render_wake_package(package)
    parsed = parse_wake_package_text(rendered)
    assert parsed == package
    assert "ClawRoom wake package." in rendered
    assert '"coordination_id": "coord_123"' in rendered


def test_runnerd_wake_is_idempotent_for_same_coordination(tmp_path: Path, monkeypatch) -> None:
    service = build_service(tmp_path, monkeypatch, state=sample_state())
    package = sample_package()

    first = service.wake(package)
    second = service.wake(package)

    assert first.run_id == second.run_id
    assert first.bridge_agent_id == second.bridge_agent_id
    assert second.attempt_id == "attempt_123"
    assert second.status == "active"
    assert second.current_hop == 7
    assert "actively managing the room" in second.summary.lower()
    assert second.next_action is None


def test_runnerd_wake_allows_retry_after_terminal_run(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    first_proc = FakeProc(pid=1111, poll_value=7)
    second_proc = FakeProc(pid=2222, poll_value=None)
    spawned: list[FakeProc] = []

    def fake_spawn_bridge(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        proc = first_proc if not spawned else second_proc
        spawned.append(proc)
        return proc

    monkeypatch.setattr(service, "_spawn_bridge", fake_spawn_bridge)
    monkeypatch.setattr(
        service,
        "_load_bridge_state",
        lambda path: sample_state(runner_id="openclaw:test", attempt_id="", health={"status": "ready", "last_error": "", "recent_note": "joined"}),
    )
    monkeypatch.setattr("runnerd.service.AUTO_RESTART_MAX_ATTEMPTS", 0)
    monkeypatch.setattr("runnerd.service.AUTO_REPLACEMENT_MAX_ATTEMPTS", 0)

    first = service.wake(sample_package())
    second = service.wake(sample_package())
    superseded = service.get_run(first.run_id)

    assert first.status == "abandoned"
    assert second.status != "abandoned"
    assert first.run_id != second.run_id
    assert len(spawned) == 2
    assert second.supersedes_run_id == first.run_id
    assert superseded.status == "replaced"
    assert superseded.reason == f"superseded_by:{second.run_id}"
    assert superseded.superseded_by_run_id == second.run_id
    assert any(hop.code == "replaced_by_new_run" for hop in superseded.hops)


def test_runnerd_owner_reply_appends_reply_file_and_keeps_pending_request(tmp_path: Path, monkeypatch) -> None:
    waiting_state = sample_state(
        conversation={"pending_owner_req_id": "oreq_1"},
        health={"status": "waiting_owner", "last_error": "", "recent_note": "waiting_owner_reply"},
    )
    service = build_service(tmp_path, monkeypatch, state=waiting_state)
    monkeypatch.setattr(service, "_fetch_owner_wait_text", lambda state, owner_req_id: "Need a budget decision")
    run = service.wake(sample_package())

    updated = service.submit_owner_reply(run.run_id, text="Keep it under 3000.", owner_request_id=None)

    reply_lines = Path(updated.owner_reply_file).read_text(encoding="utf-8").strip().splitlines()
    assert reply_lines == ["oreq_1\tKeep it under 3000."]
    assert updated.pending_owner_request is not None
    assert updated.pending_owner_request.owner_request_id == "oreq_1"
    assert "owner input" in updated.summary.lower()
    assert updated.current_hop == 7


def test_runnerd_owner_reply_without_pending_request_raises_lookup_error(tmp_path: Path, monkeypatch) -> None:
    service = build_service(tmp_path, monkeypatch, state=sample_state())
    run = service.wake(sample_package())

    try:
        service.submit_owner_reply(run.run_id, text="Proceed.", owner_request_id=None)
    except LookupError as exc:
        assert "no pending owner request" in str(exc)
    else:
        raise AssertionError("expected LookupError for missing pending owner request")


def test_runnerd_marks_stalled_when_runner_not_claimed_after_wake(tmp_path: Path, monkeypatch) -> None:
    service = build_service(
        tmp_path,
        monkeypatch,
        proc=FakeProc(poll_value=None),
        state=sample_state(runner_id="openclaw:test", attempt_id="", health={"status": "ready", "last_error": "", "recent_note": "joined"}),
    )
    monkeypatch.setattr(service, "_file_or_process_birth", lambda run: 0.0)
    monkeypatch.setattr("time.time", lambda: RUNNER_NOT_CLAIMED_SECONDS + 5.0)
    monkeypatch.setattr("runnerd.service.AUTO_REPLACEMENT_MAX_ATTEMPTS", 0)

    run = service.wake(sample_package())

    assert run.status == "stalled"
    assert run.root_cause_code == "runner_not_claimed_after_wake"
    assert run.current_hop == 6
    assert "has not claimed the room attempt" in run.summary.lower()
    assert run.next_action is not None


def test_runnerd_marks_abandoned_before_claim_when_process_exits(tmp_path: Path, monkeypatch) -> None:
    service = build_service(
        tmp_path,
        monkeypatch,
        proc=FakeProc(poll_value=7),
        state=sample_state(runner_id="openclaw:test", attempt_id="", health={"status": "ready", "last_error": "", "recent_note": "joined"}),
    )

    monkeypatch.setattr("runnerd.service.AUTO_RESTART_MAX_ATTEMPTS", 0)
    monkeypatch.setattr("runnerd.service.AUTO_REPLACEMENT_MAX_ATTEMPTS", 0)
    run = service.wake(sample_package())

    assert run.status == "abandoned"
    assert run.root_cause_code == "runnerd_lost_before_claim"


def test_runnerd_auto_restarts_before_claim_once(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    proc1 = FakeProc(poll_value=7)
    proc2 = FakeProc(poll_value=None)
    spawned: list[FakeProc] = []

    def fake_spawn_bridge(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        proc = proc1 if not spawned else proc2
        spawned.append(proc)
        return proc

    states = [
        sample_state(runner_id="openclaw:test", attempt_id="", health={"status": "ready", "last_error": "", "recent_note": "joined"}),
        sample_state(health={"status": "ready", "last_error": "", "recent_note": "joined"}),
    ]

    def fake_load_state(path):  # type: ignore[no-untyped-def]
        _ = path
        return states.pop(0) if states else sample_state()

    monkeypatch.setattr(service, "_spawn_bridge", fake_spawn_bridge)
    monkeypatch.setattr(service, "_load_bridge_state", fake_load_state)

    run = service.wake(sample_package())

    assert len(spawned) == 2
    assert run.status == "restarting"
    assert run.restart_count == 1
    assert run.root_cause_code is None
    assert run.current_hop == 6
    assert any(hop.code == "automatic_restart" for hop in run.hops)


def test_runnerd_auto_restarts_after_claim_once(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    proc1 = FakeProc(poll_value=9)
    proc2 = FakeProc(poll_value=None)
    spawned: list[FakeProc] = []

    def fake_spawn_bridge(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        proc = proc1 if not spawned else proc2
        spawned.append(proc)
        return proc

    monkeypatch.setattr(service, "_spawn_bridge", fake_spawn_bridge)
    monkeypatch.setattr(
        service,
        "_load_bridge_state",
        lambda path: sample_state(),
    )
    monkeypatch.setattr(service, "_looks_like_clean_exit", lambda run: False)

    run = service.wake(sample_package())

    assert len(spawned) == 2
    assert run.status == "restarting"
    assert run.restart_count == 1
    assert run.root_cause_code is None
    assert run.current_hop == 7
    assert any(hop.code == "automatic_restart" for hop in run.hops)


def test_runnerd_marks_restart_exhausted_before_claim(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    proc1 = FakeProc(poll_value=7)
    proc2 = FakeProc(poll_value=8)
    spawned: list[FakeProc] = []

    def fake_spawn_bridge(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        proc = proc1 if not spawned else proc2
        spawned.append(proc)
        return proc

    monkeypatch.setattr(service, "_spawn_bridge", fake_spawn_bridge)
    monkeypatch.setattr(
        service,
        "_load_bridge_state",
        lambda path: sample_state(runner_id="openclaw:test", attempt_id="", health={"status": "ready", "last_error": "", "recent_note": "joined"}),
    )
    monkeypatch.setattr("runnerd.service.AUTO_REPLACEMENT_MAX_ATTEMPTS", 0)

    run = service.wake(sample_package())
    run = service.get_run(run.run_id)

    assert len(spawned) == 2
    assert run.status == "abandoned"
    assert run.restart_count == 1
    assert run.root_cause_code == "runnerd_restart_exhausted_before_claim"
    assert run.reason == "restart_exhausted:before_claim:8"
    assert "automatic restart budget" in run.summary.lower()
    assert run.current_hop == 6


def test_runnerd_marks_restart_exhausted_after_claim(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    proc1 = FakeProc(poll_value=9)
    proc2 = FakeProc(poll_value=10)
    spawned: list[FakeProc] = []

    def fake_spawn_bridge(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        proc = proc1 if not spawned else proc2
        spawned.append(proc)
        return proc

    monkeypatch.setattr(service, "_spawn_bridge", fake_spawn_bridge)
    monkeypatch.setattr(service, "_load_bridge_state", lambda path: sample_state())
    monkeypatch.setattr(service, "_looks_like_clean_exit", lambda run: False)
    monkeypatch.setattr("runnerd.service.AUTO_REPLACEMENT_MAX_ATTEMPTS", 0)

    run = service.wake(sample_package())
    run = service.get_run(run.run_id)

    assert len(spawned) == 2
    assert run.status == "abandoned"
    assert run.restart_count == 1
    assert run.root_cause_code == "runnerd_restart_exhausted_after_claim"
    assert run.reason == "restart_exhausted:after_claim:10"
    assert "automatic restart" in run.summary.lower()
    assert run.current_hop == 7


def test_runnerd_reports_after_claim_crash_to_room_release(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    proc = FakeProc(poll_value=9)
    release_calls: list[dict[str, object]] = []

    monkeypatch.setattr(service, "_spawn_bridge", lambda **kwargs: proc)
    monkeypatch.setattr(service, "_load_bridge_state", lambda path: sample_state())
    monkeypatch.setattr(service, "_looks_like_clean_exit", lambda run: False)
    monkeypatch.setattr("runnerd.service.AUTO_RESTART_MAX_ATTEMPTS", 0)
    monkeypatch.setattr("runnerd.service.AUTO_REPLACEMENT_MAX_ATTEMPTS", 0)

    def fake_runner_release(**kwargs):  # type: ignore[no-untyped-def]
        release_calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("runnerd.service.runner_release", fake_runner_release)

    run = service.wake(sample_package())
    internal = service._runs[run.run_id]

    assert run.status == "abandoned"
    assert run.root_cause_code == "runnerd_lost_after_claim"
    assert len(release_calls) == 1
    assert release_calls[0]["status"] == "abandoned"
    assert release_calls[0]["reason"] == "runnerd_lost_after_claim"
    assert release_calls[0]["attempt_id"] == "attempt_123"
    assert internal.release_reported is True
    assert internal.release_report_error is None

    service.get_run(run.run_id)
    assert len(release_calls) == 1


def test_runnerd_reports_restart_exhausted_after_claim_to_room_release(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    proc1 = FakeProc(poll_value=9)
    proc2 = FakeProc(poll_value=10)
    spawned: list[FakeProc] = []
    release_calls: list[dict[str, object]] = []

    def fake_spawn_bridge(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        proc = proc1 if not spawned else proc2
        spawned.append(proc)
        return proc

    def fake_runner_release(**kwargs):  # type: ignore[no-untyped-def]
        release_calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(service, "_spawn_bridge", fake_spawn_bridge)
    monkeypatch.setattr(service, "_load_bridge_state", lambda path: sample_state())
    monkeypatch.setattr(service, "_looks_like_clean_exit", lambda run: False)
    monkeypatch.setattr("runnerd.service.runner_release", fake_runner_release)
    monkeypatch.setattr("runnerd.service.AUTO_REPLACEMENT_MAX_ATTEMPTS", 0)

    run = service.wake(sample_package())

    assert len(spawned) == 2
    assert run.status == "restarting"
    assert len(release_calls) == 0

    run = service.get_run(run.run_id)
    internal = service._runs[run.run_id]

    assert run.status == "abandoned"
    assert run.root_cause_code == "runnerd_restart_exhausted_after_claim"
    assert len(release_calls) == 1
    assert release_calls[0]["reason"] == "runnerd_restart_exhausted_after_claim"
    assert internal.release_reported is True
    assert internal.release_report_error is None


def test_runnerd_auto_replaces_after_restart_exhausted_before_claim(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    proc1 = FakeProc(poll_value=7)
    proc2 = FakeProc(poll_value=8)
    proc3 = FakeProc(poll_value=None)
    spawned: list[FakeProc] = []

    def fake_spawn_bridge(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        if not spawned:
            proc = proc1
        elif len(spawned) == 1:
            proc = proc2
        else:
            proc = proc3
        spawned.append(proc)
        return proc

    monkeypatch.setattr(service, "_spawn_bridge", fake_spawn_bridge)

    states = [
        sample_state(runner_id="openclaw:test", attempt_id="", health={"status": "ready", "last_error": "", "recent_note": "joined"}),
        sample_state(runner_id="openclaw:test", attempt_id="", health={"status": "ready", "last_error": "", "recent_note": "joined"}),
        sample_state(runner_id="openclaw:test-replacement", attempt_id="attempt_repl", health={"status": "active", "last_error": "", "recent_note": "poll_idle"}),
    ]

    def fake_load_state(path):  # type: ignore[no-untyped-def]
        _ = path
        return states.pop(0) if states else sample_state()

    monkeypatch.setattr(service, "_load_bridge_state", fake_load_state)

    initial = service.wake(sample_package())
    initial = service.get_run(initial.run_id)
    assert initial.status == "replaced"
    assert initial.superseded_by_run_id is not None
    replacement = service.get_run(initial.superseded_by_run_id)

    assert len(spawned) == 3
    assert replacement.status == "active"
    assert replacement.replacement_count == 1
    assert replacement.supersedes_run_id is not None
    replaced = service.get_run(replacement.supersedes_run_id)
    assert replaced.status == "replaced"
    assert replaced.superseded_by_run_id == replacement.run_id
    assert replacement.current_hop == 7


def test_runnerd_auto_replaces_after_restart_exhausted_after_claim(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    proc1 = FakeProc(poll_value=9)
    proc2 = FakeProc(poll_value=10)
    proc3 = FakeProc(poll_value=None)
    spawned: list[FakeProc] = []
    release_calls: list[dict[str, object]] = []

    def fake_spawn_bridge(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        if not spawned:
            proc = proc1
        elif len(spawned) == 1:
            proc = proc2
        else:
            proc = proc3
        spawned.append(proc)
        return proc

    def fake_runner_release(**kwargs):  # type: ignore[no-untyped-def]
        release_calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(service, "_spawn_bridge", fake_spawn_bridge)
    monkeypatch.setattr(service, "_load_bridge_state", lambda path: sample_state())
    monkeypatch.setattr(service, "_looks_like_clean_exit", lambda run: False)
    monkeypatch.setattr("runnerd.service.runner_release", fake_runner_release)

    initial = service.wake(sample_package())
    initial = service.get_run(initial.run_id)
    assert initial.status == "replaced"
    assert initial.superseded_by_run_id is not None
    replacement = service.get_run(initial.superseded_by_run_id)

    assert len(spawned) == 3
    assert len(release_calls) == 1
    assert release_calls[0]["reason"] == "runnerd_restart_exhausted_after_claim"
    assert replacement.status in {"active", "restarting"}
    assert replacement.replacement_count == 1
    assert replacement.supersedes_run_id is not None
    replaced = service.get_run(replacement.supersedes_run_id)
    assert replaced.status == "replaced"
    assert replaced.superseded_by_run_id == replacement.run_id


def test_runnerd_marks_owner_reply_not_returned_when_wait_expires(tmp_path: Path, monkeypatch) -> None:
    waiting_state = sample_state(
        conversation={"pending_owner_req_id": "oreq_2"},
        health={"status": "waiting_owner", "last_error": "", "recent_note": "waiting_owner_reply"},
    )
    service = build_service(tmp_path, monkeypatch, state=waiting_state)
    monkeypatch.setattr(service, "_fetch_owner_wait_text", lambda state, owner_req_id: "Need a product decision")
    monkeypatch.setattr(service, "_file_or_process_birth", lambda run: 0.0)
    monkeypatch.setattr("time.time", lambda: OWNER_REPLY_OVERDUE_SECONDS + 10.0)

    run = service.wake(sample_package())

    assert run.status == "waiting_owner"
    assert run.root_cause_code == "owner_reply_not_returned"
    assert run.pending_owner_request is not None
    assert run.pending_owner_request.text == "Need a product decision"


def test_runnerd_http_contract(tmp_path: Path, monkeypatch) -> None:
    service = build_service(tmp_path, monkeypatch, state=sample_state())
    app = create_app(service)

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        wake = client.post("/wake", json=sample_package().model_dump(mode="json"))
        assert wake.status_code == 200
        body = wake.json()
        assert body["accepted"] is True
        assert body["runner_kind"] == "openclaw_bridge"
        run_id = body["run_id"]

        get_run = client.get(f"/runs/{run_id}")
        assert get_run.status_code == 200
        assert get_run.json()["attempt_id"] == "attempt_123"
        assert get_run.json()["bridge_agent_id"] == "clawroom-relay"
        assert get_run.json()["current_hop"] == 7
        assert "summary" in get_run.json()

        owner_reply = client.post(
            f"/runs/{run_id}/owner-reply",
            json={"text": "Proceed"},
        )
        assert owner_reply.status_code == 404

        cancel = client.post(f"/runs/{run_id}/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "exited"


def test_runnerd_sets_supervision_origin_for_spawned_bridges(tmp_path: Path, monkeypatch) -> None:
    popen = CapturedPopen()
    monkeypatch.setattr(subprocess, "Popen", popen)
    service = RunnerdService(state_root=tmp_path / "runnerd")
    service.wake(sample_package(preferred_runner_kind="codex_bridge"))
    assert popen.env is not None
    assert popen.env["CLAWROOM_SUPERVISION_ORIGIN"] == "runnerd"


def test_runnerd_sets_replacement_env_for_spawned_bridges(tmp_path: Path, monkeypatch) -> None:
    popen = CapturedPopen()
    monkeypatch.setattr(subprocess, "Popen", popen)
    service = RunnerdService(state_root=tmp_path / "runnerd")
    first = service.wake(sample_package(preferred_runner_kind="codex_bridge"))
    first_run = service._runs[first.run_id]
    service._create_run(package=sample_package(preferred_runner_kind="codex_bridge"), superseded_run=first_run)
    assert popen.env is not None
    assert popen.env["CLAWROOM_REPLACEMENT_COUNT"] == "1"
    assert popen.env["CLAWROOM_SUPERSEDES_RUN_ID"] == first.run_id


def test_submit_cli_parses_rendered_package() -> None:
    package = sample_package()
    rendered = render_wake_package(package)
    parsed = parse_package_input(rendered)
    assert parsed == package


def test_submit_cli_parses_raw_json() -> None:
    package = sample_package()
    parsed = parse_package_input(package.model_dump_json())
    assert parsed == package


def test_submit_and_owner_reply_helpers_against_http_contract(tmp_path: Path, monkeypatch) -> None:
    waiting_state = sample_state(
        conversation={"pending_owner_req_id": "oreq_cli"},
        health={"status": "waiting_owner", "last_error": "", "recent_note": "waiting_owner_reply"},
    )
    service = build_service(tmp_path, monkeypatch, state=waiting_state)
    monkeypatch.setattr(service, "_fetch_owner_wait_text", lambda state, owner_req_id: "Need a go/no-go decision")
    app = create_app(service)

    with TestClient(app) as client:
        monkeypatch.setattr(submit_cli.httpx, "Client", lambda *args, **kwargs: _TestClientProxy(client))
        monkeypatch.setattr(owner_reply_cli.httpx, "Client", lambda *args, **kwargs: _TestClientProxy(client))
        package = sample_package()
        response = submit_package(runnerd_url=str(client.base_url), package=package)
        assert response["accepted"] is True
        run_id = str(response["run_id"])

        updated = submit_owner_reply_http(
            runnerd_url=str(client.base_url),
            run_id=run_id,
            text="Go ahead.",
            owner_request_id="oreq_cli",
        )
        assert updated["run_id"] == run_id
        assert updated["current_hop"] == 7


def test_spawn_openclaw_bridge_uses_isolated_agent_id(tmp_path: Path, monkeypatch) -> None:
    service = RunnerdService(state_root=tmp_path / "runnerd")
    captured = CapturedPopen()
    monkeypatch.setattr(subprocess, "Popen", captured)

    run_dir = service.state_root / "runs" / "run_abc123"
    run_dir.mkdir(parents=True, exist_ok=True)
    bridge_state_path = run_dir / "bridge_state.json"
    owner_reply_file = run_dir / "owner_replies.tsv"
    owner_reply_file.touch()
    log_handle = (run_dir / "bridge.log").open("a", encoding="utf-8")
    try:
        proc = service._spawn_bridge(
            run_id="run_abc123",
            package=sample_package(preferred_runner_kind="openclaw_bridge", role="initiator"),
            bridge_agent_id="clawroom-relay",
            bridge_state_path=bridge_state_path,
            owner_reply_file=owner_reply_file,
            log_handle=log_handle,
        )
    finally:
        log_handle.close()

    assert proc.pid == 5151
    assert captured.cmd is not None
    assert "--agent-id" in captured.cmd
    idx = captured.cmd.index("--agent-id")
    assert captured.cmd[idx + 1] == "clawroom-relay"
