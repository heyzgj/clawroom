from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clawroom_client_core.client import http_json, parse_join_url, runner_release

from .models import (
    HopStatusPayload,
    PendingOwnerRequestPayload,
    RunPayload,
    RunStatus,
    WakePackage,
    now_iso,
)

ROOT = Path(__file__).resolve().parents[4]
PYTHONPATH_ENTRIES = [
    ROOT / "apps" / "openclaw-bridge" / "src",
    ROOT / "apps" / "codex-bridge" / "src",
    ROOT / "packages" / "client" / "src",
    ROOT / "packages" / "core" / "src",
    ROOT / "packages" / "store" / "src",
]
RUNNERD_DEFAULT_ROOT = Path(os.getenv("CLAWROOM_RUNNERD_STATE_ROOT", Path.home() / ".clawroom" / "runnerd"))
RUNNERD_OPENCLAW_AGENT_ID = os.getenv("CLAWROOM_RUNNERD_OPENCLAW_AGENT_ID", "clawroom-relay").strip() or "clawroom-relay"
RUNNERD_OPENCLAW_AGENT_POOL_SIZE = int(os.getenv("CLAWROOM_RUNNERD_OPENCLAW_POOL_SIZE", "6"))
RUNNER_NOT_CLAIMED_SECONDS = 20.0
OWNER_REPLY_OVERDUE_SECONDS = 300.0
AUTO_RESTART_MAX_ATTEMPTS = 1
AUTO_REPLACEMENT_MAX_ATTEMPTS = 1

HOP_LABELS = {
    1: "owner_to_gateway",
    2: "gateway_to_room",
    3: "wake_package_generated",
    4: "wake_package_to_remote_owner_or_gateway",
    5: "remote_gateway_to_runnerd_wake",
    6: "runnerd_to_bridge_attach_and_claim",
    7: "runner_loop_owner_escalation_and_recovery",
}


@dataclass(slots=True)
class ManagedRun:
    run_id: str
    package: WakePackage
    runner_kind: str
    bridge_agent_id: str | None
    created_at: str
    updated_at: str
    run_dir: Path
    bridge_state_path: Path
    owner_reply_file: Path
    log_path: Path
    metadata_path: Path
    proc: subprocess.Popen[str] | None = None
    log_handle: Any | None = None
    status: RunStatus = "pending"
    reason: str | None = None
    supersedes_run_id: str | None = None
    superseded_by_run_id: str | None = None
    last_error: str = ""
    participant: str | None = None
    runner_id: str | None = None
    attempt_id: str | None = None
    pending_owner_request: PendingOwnerRequestPayload | None = None
    root_cause_code: str | None = None
    restart_count: int = 0
    replacement_count: int = 0
    release_reported: bool = False
    release_report_error: str | None = None
    hops: dict[int, HopStatusPayload] = field(default_factory=dict)

    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None


class RunnerdService:
    def __init__(self, *, state_root: Path | None = None) -> None:
        self.state_root = (state_root or RUNNERD_DEFAULT_ROOT).expanduser()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, ManagedRun] = {}
        self._wake_index: dict[str, str] = {}
        self._lock = threading.RLock()

    def healthz(self) -> dict[str, Any]:
        with self._lock:
            active = 0
            waiting_owner = 0
            for run in self._runs.values():
                self._refresh_run(run)
                if run.status not in {"exited", "abandoned"}:
                    active += 1
                if run.status == "waiting_owner":
                    waiting_owner += 1
            return {
                "ok": True,
                "active_runs": active,
                "waiting_owner_runs": waiting_owner,
                "total_runs": len(self._runs),
            }

    def wake(self, package: WakePackage) -> RunPayload:
        wake_key = f"{package.coordination_id}\u0000{package.wake_request_id}"
        with self._lock:
            existing_id = self._wake_index.get(wake_key)
            superseded_run: ManagedRun | None = None
            if existing_id and existing_id in self._runs:
                run = self._runs[existing_id]
                self._refresh_run(run)
                if run.status not in {"exited", "abandoned", "replaced"}:
                    return self._to_payload(run)
                superseded_run = run
            run = self._create_run(package=package, superseded_run=superseded_run)
            self._refresh_run(run)
            if run.status == "replaced" and run.superseded_by_run_id and run.superseded_by_run_id in self._runs:
                replacement = self._runs[run.superseded_by_run_id]
                self._refresh_run(replacement)
                return self._to_payload(replacement)
            return self._to_payload(run)

    def get_run(self, run_id: str) -> RunPayload:
        with self._lock:
            run = self._require_run(run_id)
            self._refresh_run(run)
            return self._to_payload(run)

    def submit_owner_reply(self, run_id: str, *, text: str, owner_request_id: str | None) -> RunPayload:
        with self._lock:
            run = self._require_run(run_id)
            self._refresh_run(run)
            effective_req_id = (owner_request_id or (run.pending_owner_request.owner_request_id if run.pending_owner_request else "")).strip()
            if not effective_req_id:
                raise LookupError("no pending owner request for this run")
            with run.owner_reply_file.open("a", encoding="utf-8") as fh:
                fh.write(f"{effective_req_id}\t{text.strip()}\n")
            run.reason = "owner_reply_submitted"
            run.updated_at = now_iso()
            run.root_cause_code = None
            self._set_hop(run, 7, state="pending", code="owner_reply_submitted", detail=f"owner_request_id={effective_req_id}")
            self._write_metadata(run)
            self._refresh_run(run)
            return self._to_payload(run)

    def cancel_run(self, run_id: str) -> RunPayload:
        with self._lock:
            run = self._require_run(run_id)
            if run.proc and run.proc.poll() is None:
                try:
                    run.proc.terminate()
                    run.proc.wait(timeout=5)
                except Exception:
                    try:
                        run.proc.kill()
                    except Exception:
                        pass
            if run.log_handle:
                try:
                    run.log_handle.flush()
                    run.log_handle.close()
                except Exception:
                    pass
                run.log_handle = None
            run.reason = "cancelled"
            run.status = "exited"
            run.updated_at = now_iso()
            run.last_error = run.last_error or "cancelled"
            self._set_hop(run, 7, state="failed", code="cancelled", detail="run cancelled by owner/gateway")
            self._write_metadata(run)
            return self._to_payload(run)

    def _can_auto_restart(self, run: ManagedRun, *, after_claim: bool) -> bool:
        if run.restart_count >= AUTO_RESTART_MAX_ATTEMPTS:
            return False
        if run.reason == "cancelled":
            return False
        if run.pending_owner_request is not None and not run.owner_reply_file.read_text(encoding="utf-8").strip():
            return False
        if after_claim:
            return True
        return True

    def _can_auto_replace(self, run: ManagedRun) -> bool:
        if run.replacement_count >= AUTO_REPLACEMENT_MAX_ATTEMPTS:
            return False
        if run.superseded_by_run_id:
            return False
        if run.reason == "cancelled":
            return False
        if run.pending_owner_request is not None:
            return False
        return True

    def _create_run(
        self,
        *,
        package: WakePackage,
        superseded_run: ManagedRun | None = None,
    ) -> ManagedRun:
        wake_key = f"{package.coordination_id}\u0000{package.wake_request_id}"
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        bridge_agent_id = self._bridge_agent_id(package=package, run_id=run_id)
        run_dir = self.state_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        bridge_state_path = run_dir / "bridge_state.json"
        owner_reply_file = run_dir / "owner_replies.tsv"
        owner_reply_file.touch(exist_ok=True)
        log_path = run_dir / "bridge.log"
        metadata_path = run_dir / "run.json"
        log_handle = log_path.open("a", encoding="utf-8")
        replacement_count = (superseded_run.replacement_count + 1) if superseded_run is not None else 0
        supersedes_run_id = superseded_run.run_id if superseded_run is not None else None
        proc = self._spawn_bridge(
            run_id=run_id,
            package=package,
            bridge_agent_id=bridge_agent_id,
            bridge_state_path=bridge_state_path,
            owner_reply_file=owner_reply_file,
            log_handle=log_handle,
            replacement_count=replacement_count,
            supersedes_run_id=supersedes_run_id,
        )
        now = now_iso()
        run = ManagedRun(
            run_id=run_id,
            package=package,
            runner_kind=package.preferred_runner_kind,
            bridge_agent_id=bridge_agent_id,
            created_at=now,
            updated_at=now,
            run_dir=run_dir,
            bridge_state_path=bridge_state_path,
            owner_reply_file=owner_reply_file,
            log_path=log_path,
            metadata_path=metadata_path,
            proc=proc,
            log_handle=log_handle,
            supersedes_run_id=supersedes_run_id,
            replacement_count=replacement_count,
        )
        self._seed_hops(run)
        if superseded_run is not None:
            run.reason = f"automatic_replacement:{superseded_run.run_id}"
            run.root_cause_code = None
            self._set_hop(run, 5, state="completed", code="automatic_replacement_accepted", detail=f"supersedes_run_id={superseded_run.run_id}")
        self._runs[run_id] = run
        self._wake_index[wake_key] = run_id
        if superseded_run is not None:
            self._mark_replaced(superseded_run, replacement_run_id=run_id)
        self._write_metadata(run)
        return run

    def _maybe_auto_replace(self, run: ManagedRun, *, phase: str, detail: str) -> ManagedRun | None:
        if not self._can_auto_replace(run):
            return None
        replacement = self._create_run(package=run.package, superseded_run=run)
        replacement.status = "restarting"
        replacement.reason = f"automatic_replacement:{phase}:{run.run_id}"
        replacement.root_cause_code = None
        replacement.last_error = run.last_error
        replacement.pending_owner_request = None
        target_hop = 7 if run.attempt_id else 6
        self._set_hop(replacement, target_hop, state="pending", code="automatic_replacement", detail=f"{detail};supersedes_run_id={run.run_id}")
        self._write_metadata(replacement)
        self._refresh_run(replacement)
        return replacement

    def _restart_run(self, run: ManagedRun, *, phase: str) -> bool:
        if run.log_handle:
            try:
                run.log_handle.flush()
            except Exception:
                pass
        try:
            if run.bridge_state_path.exists():
                run.bridge_state_path.unlink()
        except Exception:
            pass
        if run.proc and run.proc.poll() is None:
            try:
                run.proc.terminate()
            except Exception:
                pass
        try:
            log_handle = run.log_path.open("a", encoding="utf-8")
            proc = self._spawn_bridge(
                run_id=run.run_id,
                package=run.package,
                bridge_agent_id=run.bridge_agent_id,
                bridge_state_path=run.bridge_state_path,
                owner_reply_file=run.owner_reply_file,
                log_handle=log_handle,
                replacement_count=run.replacement_count,
                supersedes_run_id=run.supersedes_run_id,
            )
        except Exception as exc:
            run.last_error = str(exc)
            run.reason = f"restart_failed:{phase}"
            self._set_hop(run, 7 if run.attempt_id else 6, state="failed", code="runnerd_restart_failed", detail=str(exc)[:300])
            return False
        run.proc = proc
        run.log_handle = log_handle
        run.restart_count += 1
        run.status = "restarting"
        run.reason = f"automatic_restart:{phase}"
        run.root_cause_code = None
        detail = f"phase={phase};restart_count={run.restart_count}"
        if run.attempt_id:
            self._set_hop(run, 7, state="pending", code="automatic_restart", detail=detail)
        else:
            self._set_hop(run, 6, state="pending", code="automatic_restart", detail=detail)
        self._write_metadata(run)
        return True

    def _restart_exhausted_code(self, *, after_claim: bool) -> str:
        return "runnerd_restart_exhausted_after_claim" if after_claim else "runnerd_restart_exhausted_before_claim"

    def _mark_replaced(self, run: ManagedRun, *, replacement_run_id: str) -> None:
        run.status = "replaced"
        run.reason = f"superseded_by:{replacement_run_id}"
        run.superseded_by_run_id = replacement_run_id
        run.root_cause_code = None
        target_hop = 7 if run.attempt_id else 6
        self._set_hop(run, target_hop, state="completed", code="replaced_by_new_run", detail=f"replacement_run_id={replacement_run_id}")
        if run.log_handle:
            try:
                run.log_handle.flush()
                run.log_handle.close()
            except Exception:
                pass
            run.log_handle = None
        self._write_metadata(run)

    def _report_abandoned_to_room(
        self,
        run: ManagedRun,
        state: dict[str, Any] | None,
        *,
        recovery_reason: str,
    ) -> None:
        if run.release_reported:
            return
        if not state:
            return
        base_url = self._clean_str(state.get("base_url"))
        room_id = self._clean_str(state.get("room_id"))
        token = self._clean_str(state.get("token"))
        runner_id = self._clean_str(state.get("runner_id")) or run.runner_id
        attempt_id = self._clean_str(state.get("attempt_id")) or run.attempt_id
        if not base_url or not room_id or not token or not runner_id or not attempt_id:
            return
        try:
            runner_release(
                base_url=base_url,
                room_id=room_id,
                token=token,
                runner_id=runner_id,
                attempt_id=attempt_id,
                status="abandoned",
                reason=recovery_reason,
                last_error=(run.last_error or run.reason or "")[:500] or None,
            )
        except Exception as exc:
            run.release_report_error = str(exc)[:500]
            return
        run.release_reported = True
        run.release_report_error = None

    def shutdown(self) -> None:
        with self._lock:
            for run in self._runs.values():
                if run.proc and run.proc.poll() is None:
                    try:
                        run.proc.terminate()
                    except Exception:
                        pass
                try:
                    if run.log_handle:
                        run.log_handle.flush()
                        run.log_handle.close()
                except Exception:
                    pass
                finally:
                    run.log_handle = None

    def _require_run(self, run_id: str) -> ManagedRun:
        run = self._runs.get(run_id)
        if not run:
            raise LookupError(f"unknown run_id: {run_id}")
        return run

    def _spawn_bridge(
        self,
        *,
        run_id: str,
        package: WakePackage,
        bridge_agent_id: str | None,
        bridge_state_path: Path,
        owner_reply_file: Path,
        log_handle: Any,
        replacement_count: int = 0,
        supersedes_run_id: str | None = None,
    ) -> subprocess.Popen[str]:
        env = os.environ.copy()
        pythonpath = [str(path) for path in PYTHONPATH_ENTRIES]
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(pythonpath + ([existing] if existing else []))
        env["PYTHONUNBUFFERED"] = "1"
        env["CLAWROOM_SUPERVISION_ORIGIN"] = "runnerd"
        env["CLAWROOM_REPLACEMENT_COUNT"] = str(max(0, int(replacement_count)))
        env["CLAWROOM_SUPERSEDES_RUN_ID"] = str(supersedes_run_id or "")
        env.setdefault("CLAWROOM_API_BASE", parse_join_url(package.join_link)["base_url"])
        join = parse_join_url(package.join_link)
        owner_context = package.owner_context.strip()
        common = [
            "--role",
            package.role,
            "--poll-seconds",
            "1",
            "--heartbeat-seconds",
            "5",
            "--max-seconds",
            "0",
            "--owner-reply-file",
            str(owner_reply_file),
            "--state-path",
            str(bridge_state_path),
        ]
        if owner_context:
            common.extend(["--owner-context", owner_context])
        if package.preferred_runner_kind == "openclaw_bridge":
            cmd = [
                sys.executable,
                str(ROOT / "apps" / "openclaw-bridge" / "src" / "openclaw_bridge" / "cli.py"),
                package.join_link,
                "--agent-id",
                bridge_agent_id or "main",
                "--preflight-mode",
                "off",
                "--print-result",
                "--client-name",
                "RunnerdOpenClaw",
                *common,
            ]
        else:
            cmd = [
                sys.executable,
                str(ROOT / "apps" / "codex-bridge" / "src" / "codex_bridge" / "cli.py"),
                "--base-url",
                join["base_url"],
                "--room-id",
                join["room_id"],
                "--token",
                join["token"],
                "--client-name",
                "RunnerdCodex",
                *common,
            ]
            # client-name is not supported by codex-bridge; strip it back out.
            idx = cmd.index("--client-name") if "--client-name" in cmd else -1
            if idx >= 0:
                del cmd[idx : idx + 2]
        return subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )

    _agent_pool_counter: int = 0

    def _bridge_agent_id(self, *, package: WakePackage, run_id: str) -> str | None:
        if package.preferred_runner_kind != "openclaw_bridge":
            return None
        if RUNNERD_OPENCLAW_AGENT_POOL_SIZE > 0:
            self._agent_pool_counter += 1
            slot = (self._agent_pool_counter % RUNNERD_OPENCLAW_AGENT_POOL_SIZE) + 1
            return f"{RUNNERD_OPENCLAW_AGENT_ID}-{slot}"
        return RUNNERD_OPENCLAW_AGENT_ID

    def _seed_hops(self, run: ManagedRun) -> None:
        self._set_hop(run, 1, state="completed", detail="owner request reached gateway")
        self._set_hop(run, 2, state="completed", detail=f"room_id={run.package.room_id}")
        self._set_hop(run, 3, state="completed", detail="wake package generated")
        self._set_hop(run, 4, state="completed", detail="manual owner forward completed")
        self._set_hop(run, 5, state="completed", detail="runnerd wake accepted")
        self._set_hop(run, 6, state="pending", detail="waiting for bridge attach and runner claim")
        self._set_hop(run, 7, state="pending", detail="waiting for runner loop activity")

    def _set_hop(self, run: ManagedRun, hop: int, *, state: str, code: str | None = None, detail: str = "") -> None:
        run.hops[hop] = HopStatusPayload(
            hop=hop,
            label=HOP_LABELS[hop],
            state=state,
            code=code,
            detail=detail[:500],
        )

    def _refresh_run(self, run: ManagedRun) -> None:
        if run.status == "replaced" and (run.reason or "").startswith("superseded_by:"):
            run.updated_at = now_iso()
            self._write_metadata(run)
            return
        state = self._load_bridge_state(run.bridge_state_path)
        exit_code = run.proc.poll() if run.proc else None
        now_ts = time.time()
        run.updated_at = now_iso()
        run.root_cause_code = None

        if state:
            run.participant = self._clean_str(state.get("participant")) or run.participant
            run.runner_id = self._clean_str(state.get("runner_id")) or run.runner_id
            run.attempt_id = self._clean_str(state.get("attempt_id")) or run.attempt_id
            health = state.get("health") if isinstance(state.get("health"), dict) else {}
            run.last_error = self._clean_str(health.get("last_error")) or run.last_error
            bridge_status = self._clean_str(health.get("status")) or "pending"
            if bridge_status in {"ready", "active", "idle", "waiting_owner", "stalled", "restarting", "replaced", "exited", "abandoned", "pending"}:
                run.status = bridge_status  # type: ignore[assignment]
            conversation = state.get("conversation") if isinstance(state.get("conversation"), dict) else {}
            pending_owner_req_id = self._clean_str(conversation.get("pending_owner_req_id"))
            if pending_owner_req_id:
                run.pending_owner_request = PendingOwnerRequestPayload(
                    owner_request_id=pending_owner_req_id,
                    text=self._fetch_owner_wait_text(state, pending_owner_req_id),
                )
            else:
                run.pending_owner_request = None

        if run.attempt_id:
            self._set_hop(run, 6, state="completed", detail=f"attempt_id={run.attempt_id}")
        else:
            age = now_ts - self._file_or_process_birth(run)
            if exit_code is None and age > RUNNER_NOT_CLAIMED_SECONDS:
                run.status = "stalled"
                run.root_cause_code = "runner_not_claimed_after_wake"
                run.reason = "runner_not_claimed_after_wake"
                self._set_hop(run, 6, state="failed", code="runner_not_claimed_after_wake", detail=f"age_seconds={int(age)}")
                if self._maybe_auto_replace(run, phase="not_claimed_after_wake", detail=f"age_seconds={int(age)}") is not None:
                    return
            elif exit_code is not None:
                if self._can_auto_restart(run, after_claim=False) and self._restart_run(run, phase="before_claim"):
                    return
                run.status = "abandoned"
                run.root_cause_code = self._restart_exhausted_code(after_claim=False) if run.restart_count > 0 else "runnerd_lost_before_claim"
                run.reason = f"restart_exhausted:before_claim:{exit_code}" if run.restart_count > 0 else f"process_exit:{exit_code}"
                self._set_hop(run, 6, state="failed", code=run.root_cause_code, detail=f"exit_code={exit_code};restart_count={run.restart_count}")
                if self._maybe_auto_replace(run, phase="before_claim", detail=f"exit_code={exit_code};restart_count={run.restart_count}") is not None:
                    return

        if run.pending_owner_request:
            age = now_ts - self._file_or_process_birth(run)
            if age > OWNER_REPLY_OVERDUE_SECONDS:
                run.root_cause_code = "owner_reply_not_returned"
                self._set_hop(run, 7, state="failed", code="owner_reply_not_returned", detail=f"owner_request_id={run.pending_owner_request.owner_request_id}")
            else:
                run.status = "waiting_owner"
                self._set_hop(run, 7, state="pending", code="waiting_owner", detail=f"owner_request_id={run.pending_owner_request.owner_request_id}")
        elif run.attempt_id and exit_code is None and run.status in {"active", "idle", "ready"}:
            self._set_hop(run, 7, state="completed", detail=f"runner_status={run.status}")
            run.root_cause_code = None
        elif run.attempt_id and exit_code is not None:
            if not self._looks_like_clean_exit(run):
                if self._can_auto_restart(run, after_claim=True) and self._restart_run(run, phase="after_claim"):
                    return
                run.status = "abandoned"
                run.root_cause_code = self._restart_exhausted_code(after_claim=True) if run.restart_count > 0 else "runnerd_lost_after_claim"
                if run.reason is None or run.reason.startswith("automatic_restart:"):
                    run.reason = f"restart_exhausted:after_claim:{exit_code}" if run.restart_count > 0 else f"process_exit:{exit_code}"
                self._set_hop(run, 7, state="failed", code=run.root_cause_code, detail=f"exit_code={exit_code};restart_count={run.restart_count}")
                if run.root_cause_code:
                    self._report_abandoned_to_room(run, state, recovery_reason=run.root_cause_code)
                if self._maybe_auto_replace(run, phase="after_claim", detail=f"exit_code={exit_code};restart_count={run.restart_count}") is not None:
                    return
            else:
                run.status = "exited"
                run.root_cause_code = None
                self._set_hop(run, 7, state="completed", detail=run.reason or "room_closed")
                if run.log_handle:
                    try:
                        run.log_handle.flush()
                        run.log_handle.close()
                    except Exception:
                        pass
                    run.log_handle = None

        if exit_code is not None and run.reason is None:
            run.reason = f"process_exit:{exit_code}"
        self._write_metadata(run)

    def _fetch_owner_wait_text(self, state: dict[str, Any], owner_req_id: str) -> str:
        base_url = self._clean_str(state.get("base_url"))
        room_id = self._clean_str(state.get("room_id"))
        token = self._clean_str(state.get("token"))
        if not base_url or not room_id or not token:
            return ""
        try:
            payload = http_json("GET", f"{base_url.rstrip('/')}/rooms/{room_id}/events?after=0&limit=200", token=token)
        except Exception:
            return ""
        for event in reversed(list(payload.get("events") or [])):
            if str(event.get("type") or "") != "owner_wait":
                continue
            evt_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            meta = evt_payload.get("meta") if isinstance(evt_payload.get("meta"), dict) else {}
            if self._clean_str(meta.get("owner_req_id")) == owner_req_id:
                return self._clean_str(evt_payload.get("text"))[:1000]
        return ""

    def _file_or_process_birth(self, run: ManagedRun) -> float:
        try:
            return run.metadata_path.stat().st_mtime
        except Exception:
            return time.time()

    def _looks_like_clean_exit(self, run: ManagedRun) -> bool:
        if (run.reason or "").startswith("room_closed") or (run.reason or "") == "cancelled":
            return True
        if run.last_error.startswith("signal:"):
            return False
        state = self._load_bridge_state(run.bridge_state_path)
        if not state:
            return False
        health = state.get("health") if isinstance(state.get("health"), dict) else {}
        recent_note = self._clean_str(health.get("recent_note"))
        return recent_note.startswith("room_closed:") or recent_note == "max_seconds_reached"

    def _load_bridge_state(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _write_metadata(self, run: ManagedRun) -> None:
        payload = self._to_payload(run).model_dump(mode="json")
        run.metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _current_hop(self, run: ManagedRun) -> tuple[int, str]:
        for index in sorted(run.hops):
            hop = run.hops[index]
            if hop.state in {"failed", "pending"}:
                return (index, hop.label)
        return (7, HOP_LABELS[7])

    def _summary_and_next_action(self, run: ManagedRun) -> tuple[str, str | None]:
        if run.pending_owner_request:
            question = run.pending_owner_request.text or "The runner needs an owner-only decision."
            return (f"Waiting for owner input: {question}", "Reply in the gateway so runnerd can hand the answer back to the runner.")

        if run.root_cause_code == "runner_not_claimed_after_wake":
            return (
                "Wake reached runnerd, but the bridge has not claimed the room attempt yet.",
                "Check that the selected bridge process can start and reach the room, then resend or repair the wake package.",
            )
        if run.root_cause_code == "runnerd_lost_before_claim":
            return (
                "The bridge process exited before it claimed the room attempt.",
                "Inspect the runner log and restart the run from the gateway or resend the wake package.",
            )
        if run.root_cause_code == "runnerd_restart_exhausted_before_claim":
            return (
                "The bridge process exited before claim even after runnerd used its automatic restart budget.",
                "Treat this as a replacement/repair incident: inspect the log, resend the wake package, or switch to takeover.",
            )
        if run.root_cause_code == "runnerd_lost_after_claim":
            return (
                "The bridge process claimed the room but exited before finishing cleanly.",
                "Inspect the runner log and decide whether to repair, replace, or take over the room.",
            )
        if run.root_cause_code == "runnerd_restart_exhausted_after_claim":
            return (
                "The bridge process exited again after runnerd already attempted one automatic restart.",
                "Treat this as a replacement/repair incident and move to repair, replacement, or takeover.",
            )
        if run.root_cause_code == "owner_reply_not_returned":
            return (
                "The room is still waiting for the owner's answer.",
                "Reply in the gateway or explicitly cancel the run if the decision is no longer needed.",
            )

        if run.status == "pending":
            return ("Wake accepted; runnerd is preparing the bridge process.", "Wait for the bridge to attach and claim the room.")
        if run.status == "ready":
            return ("Bridge started and is getting ready to manage the room.", "Wait for the first runner claim or inspect the log if it stalls.")
        if run.status == "active":
            return ("Runner attached and is actively managing the room.", None)
        if run.status == "idle":
            return ("Runner attached and is waiting for the next room event.", None)
        if run.status == "waiting_owner":
            return ("Runner is paused because it needs an owner answer.", "Reply in the gateway so the runner can resume.")
        if run.status == "stalled":
            return ("Runner has stalled before the room could progress safely.", "Inspect the log and resend or repair the run.")
        if run.status == "restarting":
            return ("Runner is restarting after a recoverable issue.", "Wait for the new attempt to claim the room.")
        if run.status == "replaced":
            return ("This runner has been replaced by another attempt.", "Inspect the replacement run for the current status.")
        if run.status == "exited":
            return ("Runner finished and exited cleanly.", None)
        if run.status == "abandoned":
            return ("Runner exited without a clean finish.", "Inspect the log and decide whether to repair, replace, or take over.")
        return ("Runner state is unknown.", "Inspect the latest log and hop state.")

    def _to_payload(self, run: ManagedRun) -> RunPayload:
        current_hop, current_hop_label = self._current_hop(run)
        summary, next_action = self._summary_and_next_action(run)
        return RunPayload(
            run_id=run.run_id,
            coordination_id=run.package.coordination_id,
            wake_request_id=run.package.wake_request_id,
            room_id=run.package.room_id,
            runner_kind=run.runner_kind,  # type: ignore[arg-type]
            bridge_agent_id=run.bridge_agent_id,
            role=run.package.role,
            status=run.status,
            reason=run.reason,
            supersedes_run_id=run.supersedes_run_id,
            superseded_by_run_id=run.superseded_by_run_id,
            pid=run.pid(),
            participant=run.participant,
            runner_id=run.runner_id,
            attempt_id=run.attempt_id,
            last_error=run.last_error,
            created_at=run.created_at,
            updated_at=run.updated_at,
            bridge_state_path=str(run.bridge_state_path),
            owner_reply_file=str(run.owner_reply_file),
            log_path=str(run.log_path),
            restart_count=run.restart_count,
            replacement_count=run.replacement_count,
            pending_owner_request=run.pending_owner_request,
            root_cause_code=run.root_cause_code,
            current_hop=current_hop,
            current_hop_label=current_hop_label,
            summary=summary,
            next_action=next_action,
            hops=[run.hops[index] for index in sorted(run.hops)],
        )

    @staticmethod
    def _clean_str(value: Any) -> str:
        return str(value or "").strip()
