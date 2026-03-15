from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[4]
CLIENT_SRC = ROOT / "packages" / "client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from clawroom_client_core import (
    RunnerCapabilities,
    build_owner_reply_prompt,
    build_room_reply_prompt,
    build_runner_state,
    http_json,
    next_relays,
    relay_requires_reply,
    runner_claim,
    runner_release,
    runner_renew,
)


def log(*parts: object) -> None:
    print("[codex-bridge]", *parts, flush=True)


def short(s: str, n: int = 220) -> str:
    return s if len(s) <= n else s[: n - 1] + "..."


def call_openai(model: str, api_key: str, prompt: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "input": prompt,
        "text": {"format": {"type": "json_object"}},
    }
    with httpx.Client(timeout=40.0, trust_env=False) as client:
        resp = client.post("https://api.openai.com/v1/responses", headers=headers, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI error status={resp.status_code} body={short(resp.text, 400)}")
    data = resp.json()
    text = data.get("output_text", "")
    if not text:
        output = data.get("output") or []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = str(content.get("text", "")).strip()
                    if text:
                        break
            if text:
                break
    if not text:
        raise RuntimeError("OpenAI returned empty output_text")
    return json.loads(text)


def parse_json_object_text(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise RuntimeError("Codex CLI returned empty output")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise RuntimeError(f"Codex CLI did not return JSON: {short(cleaned, 300)}") from None
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise RuntimeError("Codex CLI returned non-object JSON")
    return parsed


def call_codex_cli(*, codex_bin: str, workdir: Path, model: str, prompt: str, timeout_seconds: int) -> dict[str, Any]:
    executable = shutil.which(codex_bin)
    if not executable:
        raise RuntimeError(f"Codex CLI not found: {codex_bin}")
    with tempfile.TemporaryDirectory(prefix="codex-bridge-") as tmpdir:
        out_path = Path(tmpdir) / "last-message.txt"
        cmd = [
            executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--color",
            "never",
            "-c",
            "suppress_unstable_features_warning=true",
            "-C",
            str(workdir),
            "-o",
            str(out_path),
        ]
        if model:
            cmd.extend(["-m", model])
        cmd.append(prompt)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(10, timeout_seconds))
        content = out_path.read_text(encoding="utf-8").strip() if out_path.exists() else ""
        if proc.returncode != 0:
            raise RuntimeError(
                "Codex CLI failed "
                f"status={proc.returncode} "
                f"stderr={short(proc.stderr or proc.stdout or content, 400)}"
            )
        return parse_json_object_text(content)


def normalize_message(raw: dict[str, Any]) -> dict[str, Any]:
    intent = str(raw.get("intent", "ANSWER")).upper().strip()
    if intent == "NEED_HUMAN":
        intent = "ASK_OWNER"
    if intent not in {"ASK", "ANSWER", "NOTE", "DONE", "ASK_OWNER", "OWNER_REPLY"}:
        intent = "ANSWER"
    text = str(raw.get("text", "")).strip() or "(no text)"
    fills = raw.get("fills") if isinstance(raw.get("fills"), dict) else {}
    facts = raw.get("facts") if isinstance(raw.get("facts"), list) else []
    questions = raw.get("questions") if isinstance(raw.get("questions"), list) else []
    if intent == "ASK":
        expect_reply = True
    elif intent in {"NOTE", "DONE", "ASK_OWNER"}:
        expect_reply = False
    elif isinstance(raw.get("expect_reply"), bool):
        expect_reply = bool(raw["expect_reply"])
    elif intent in {"ANSWER", "OWNER_REPLY"}:
        expect_reply = True
    else:
        expect_reply = False
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    return {
        "intent": intent,
        "text": text,
        "fills": {str(k): str(v) for k, v in fills.items() if str(k).strip() and str(v).strip()},
        "facts": [str(x).strip() for x in facts if str(x).strip()],
        "questions": [str(x).strip() for x in questions if str(x).strip()],
        "expect_reply": expect_reply,
        "meta": meta,
    }


def coerce_opening_message(message: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(message)
    changed: list[str] = []
    if str(coerced.get("intent") or "") in {"DONE", "NOTE", "OWNER_REPLY"}:
        coerced["intent"] = "ANSWER"
        changed.append("intent->ANSWER")
    if not bool(coerced.get("expect_reply")):
        coerced["expect_reply"] = True
        changed.append("expect_reply->true")
    if changed:
        meta = dict(coerced.get("meta") or {})
        meta["opening_coercion"] = changed
        coerced["meta"] = meta
    return coerced


def _room_outcomes_complete(room_snapshot: dict[str, Any], message: dict[str, Any]) -> bool:
    expected = room_snapshot.get("expected_outcomes") or room_snapshot.get("required_fields") or []
    if not isinstance(expected, list) or not expected:
        return False
    known_fields: dict[str, str] = {}
    room_fields = room_snapshot.get("fields") if isinstance(room_snapshot.get("fields"), dict) else {}
    for key, raw in room_fields.items():
        if isinstance(raw, dict):
            value = str(raw.get("value") or "").strip()
        else:
            value = str(raw or "").strip()
        if value:
            known_fields[str(key)] = value
    for key, value in (message.get("fills") or {}).items():
        text = str(value or "").strip()
        if text:
            known_fields[str(key)] = text
    return all(str(name) in known_fields and known_fields[str(name)].strip() for name in expected)


def coerce_terminal_message(message: dict[str, Any], room_snapshot: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(message)
    intent = str(coerced.get("intent") or "")
    if intent not in {"ANSWER", "NOTE"}:
        return coerced
    if bool(coerced.get("expect_reply")):
        return coerced
    questions = coerced.get("questions") if isinstance(coerced.get("questions"), list) else []
    if any(str(item).strip() for item in questions):
        return coerced
    if not _room_outcomes_complete(room_snapshot, coerced):
        return coerced
    coerced["intent"] = "DONE"
    coerced["expect_reply"] = False
    meta = dict(coerced.get("meta") or {})
    changes = list(meta.get("terminal_coercion") or [])
    changes.append("intent->DONE")
    meta["terminal_coercion"] = changes
    coerced["meta"] = meta
    return coerced


def notify_owner(cmd_template: str | None, text: str, owner_req_id: str) -> bool:
    if not cmd_template:
        return False
    command = cmd_template.format(text=text, owner_req_id=owner_req_id)
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    if proc.returncode != 0:
        log("owner notify failed", short(proc.stderr or proc.stdout, 300))
        return False
    log("owner notified", owner_req_id)
    return True


def parse_owner_reply_marker(text: str, owner_req_id: str) -> str | None:
    if not text:
        return None
    req = re.escape(owner_req_id)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(f"{owner_req_id}\t"):
            reply = line.split("\t", 1)[1].strip()
            if reply:
                return reply
        match = re.search(rf"owner_req_id\s*=\s*{req}\s*;\s*reply\s*=\s*(.+)$", line, flags=re.IGNORECASE)
        if match:
            reply = match.group(1).strip()
            if reply:
                return reply
    return None


def parse_owner_reply_from_json(value: Any, owner_req_id: str) -> str | None:
    if isinstance(value, dict):
        for key in ("owner_req_id", "ownerReqId", "req_id", "request_id"):
            value_req_id = value.get(key)
            if value_req_id is not None and str(value_req_id).strip() == owner_req_id:
                for body_key in ("reply", "owner_reply", "text", "message", "content", "body"):
                    candidate = value.get(body_key)
                    if isinstance(candidate, str) and candidate.strip():
                        marker_reply = parse_owner_reply_marker(candidate, owner_req_id)
                        return marker_reply or candidate.strip()
        for nested in value.values():
            found = parse_owner_reply_from_json(nested, owner_req_id)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = parse_owner_reply_from_json(item, owner_req_id)
            if found:
                return found
        return None
    if isinstance(value, str):
        return parse_owner_reply_marker(value, owner_req_id)
    return None


def read_owner_reply_from_command(*, cmd_template: str, owner_req_id: str, seen_signatures: set[str]) -> str | None:
    try:
        command = cmd_template.format(owner_req_id=owner_req_id)
    except KeyError as exc:
        log("owner reply cmd template error", exc)
        return None
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    if proc.returncode != 0:
        log("owner reply cmd failed", short(proc.stderr or proc.stdout, 300))
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None

    reply: str | None = None
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        reply = parse_owner_reply_marker(out, owner_req_id)
    else:
        reply = parse_owner_reply_from_json(parsed, owner_req_id)

    if not reply:
        return None
    signature = f"{owner_req_id}\u0000{reply}"
    if signature in seen_signatures:
        return None
    seen_signatures.add(signature)
    return reply


def read_owner_reply_from_file(path: Path, owner_req_id: str) -> str | None:
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    remaining: list[str] = []
    found: str | None = None
    for line in lines:
        if "\t" not in line:
            remaining.append(line)
            continue
        key, value = line.split("\t", 1)
        if key.strip() == owner_req_id and found is None:
            found = value.strip()
        else:
            remaining.append(line)

    if found is not None:
        path.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")
    return found


def wait_owner_reply(
    *,
    owner_req_id: str,
    timeout_seconds: int,
    owner_reply_file: Path | None,
    owner_reply_fetcher: Any = None,
    poll_seconds: float = 1.0,
    on_poll: Any = None,
) -> str | None:
    started = time.time()
    while time.time() - started <= timeout_seconds:
        if owner_reply_file:
            reply = read_owner_reply_from_file(owner_reply_file, owner_req_id)
            if reply:
                return reply
        if owner_reply_fetcher:
            reply = owner_reply_fetcher(owner_req_id)
            if reply:
                return reply
        if on_poll:
            try:
                on_poll()
            except Exception as exc:  # noqa: BLE001
                log("owner wait heartbeat failed", short(str(exc), 220))
        time.sleep(max(0.2, poll_seconds))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex ClawRoom adapter")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--role", choices=["initiator", "responder", "auto"], default="auto")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=5.0)
    parser.add_argument("--cursor", type=int, default=0, help="Initial events cursor for handoff mode.")
    parser.add_argument(
        "--state-path",
        default="",
        help="Optional runner state path for cursor/seen persistence. Default: ~/.codex/clawroom/<room_id>.json",
    )
    parser.add_argument(
        "--owner-context",
        default="",
        help="Optional owner constraints/context injected into every model prompt.",
    )
    parser.add_argument("--owner-wait-timeout-seconds", type=int, default=1800)
    parser.add_argument(
        "--owner-reply-file",
        default=None,
        help="Optional file polled for owner replies, format: owner_req_id<TAB>reply text",
    )
    parser.add_argument(
        "--owner-reply-cmd",
        default=None,
        help="Optional shell template polled for owner replies; supports {owner_req_id}.",
    )
    parser.add_argument(
        "--owner-reply-poll-seconds",
        type=float,
        default=1.0,
        help="Polling interval for owner reply file/cmd.",
    )
    parser.add_argument(
        "--owner-notify-cmd",
        default=None,
        help="Optional shell template with {text} and {owner_req_id}",
    )
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=0,
        help="Max runtime in seconds; set 0 to disable timeout and keep loop alive.",
    )
    parser.add_argument("--offline-mock", action="store_true")
    parser.add_argument(
        "--backend",
        choices=["auto", "openai-responses", "codex-cli"],
        default=os.getenv("CODEX_BRIDGE_BACKEND", "auto"),
        help="Model backend. auto prefers local Codex CLI when available, otherwise OpenAI Responses.",
    )
    parser.add_argument(
        "--codex-bin",
        default=os.getenv("CODEX_BRIDGE_CODEX_BIN", "codex"),
        help="Executable used when backend=codex-cli.",
    )
    parser.add_argument(
        "--codex-workdir",
        default=str(ROOT),
        help="Working directory passed to local Codex CLI executions.",
    )
    parser.add_argument(
        "--model-timeout-seconds",
        type=int,
        default=120,
        help="Timeout for a single model generation call.",
    )
    args = parser.parse_args()
    if args.heartbeat_seconds < 1:
        parser.error("--heartbeat-seconds must be >= 1")

    base = args.base_url.rstrip("/")
    started = time.time()
    role = args.role
    started_message_sent = False
    kickoff_wait_logged = False
    last_heartbeat_at = 0.0
    default_state_path = Path.home() / ".codex" / "clawroom" / f"{args.room_id}.json"
    state_path = Path(args.state_path).expanduser() if args.state_path else default_state_path
    state = build_runner_state(
        base_url=base,
        room_id=args.room_id,
        token=args.token,
        initial_cursor=max(0, int(args.cursor)),
        state_path=state_path,
        logger=lambda msg: log(msg),
    )
    state.note_owner_context(args.owner_context)
    if not state.runner_id:
        state.runner_id = f"codex:{uuid.uuid4().hex[:10]}"
    state.execution_mode = "managed_attached"
    owner_reply_file = Path(args.owner_reply_file).expanduser() if args.owner_reply_file else None
    owner_reply_seen: set[str] = set()

    def owner_reply_channel_available() -> bool:
        return bool(args.owner_reply_cmd or owner_reply_file)

    def fetch_owner_reply(owner_req_id: str) -> str | None:
        if args.owner_reply_cmd:
            reply = read_owner_reply_from_command(
                cmd_template=args.owner_reply_cmd,
                owner_req_id=owner_req_id,
                seen_signatures=owner_reply_seen,
            )
            if reply:
                return reply
        return None

    capabilities = RunnerCapabilities(
        strategy="daemon-safe",
        owner_reply_supported=owner_reply_channel_available(),
        background_safe=True,
        persistence_supported=bool(state.state_path),
        health_surface=True,
        managed_certified=True,
        recovery_policy="automatic",
        supervision_origin=str(os.getenv("CLAWROOM_SUPERVISION_ORIGIN", "direct")).strip().lower() or "direct",
        replacement_count=max(0, int(os.getenv("CLAWROOM_REPLACEMENT_COUNT", "0") or "0")),
        supersedes_run_id=str(os.getenv("CLAWROOM_SUPERSEDES_RUN_ID", "")).strip()[:120] or None,
    )
    state.set_capabilities(capabilities)
    state.set_health(status="ready", recent_note=f"strategy={capabilities.strategy}")

    def selected_backend() -> str:
        if args.backend != "auto":
            return args.backend
        if os.getenv("OPENAI_API_KEY"):
            return "openai-responses"
        if shutil.which(args.codex_bin):
            return "codex-cli"
        return "openai-responses"

    backend = selected_backend()
    state.set_health(status="ready", recent_note=f"strategy={capabilities.strategy};backend={backend}")
    last_reported_phase: str | None = None
    last_reported_phase_detail: str | None = None
    shutdown_reason = "client_exit"
    shutdown_note = "client_exit"
    shutdown_last_error = ""

    def mark_shutdown(reason: str, *, note: str, last_error: str = "", overwrite: bool = True) -> None:
        nonlocal shutdown_reason, shutdown_note, shutdown_last_error
        if not overwrite and shutdown_reason != "client_exit":
            return
        shutdown_reason = str(reason or "client_exit").strip() or "client_exit"
        shutdown_note = str(note or shutdown_reason).strip()[:500]
        shutdown_last_error = str(last_error or "").strip()[:500]
        state.set_health(status="exited", last_error=shutdown_last_error, recent_note=shutdown_note)
        state.save(logger=lambda msg: log(msg))

    def handle_shutdown_signal(signum: int, _frame: object) -> None:
        try:
            signame = signal.Signals(signum).name
        except Exception:  # noqa: BLE001
            signame = f"SIG{signum}"
        lower = signame.lower()
        log("signal_received", signame)
        mark_shutdown(
            f"signal_{lower}",
            note=f"signal:{signame}",
            last_error=f"signal:{signame}",
        )
        raise SystemExit(0)

    for sig_name in ("SIGTERM", "SIGHUP", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        signal.signal(sig, handle_shutdown_signal)

    def renew_runner_claim(
        *,
        status_override: str | None = None,
        recovery_reason: str | None = None,
        phase: str | None = None,
        phase_detail: str | None = None,
    ) -> None:
        nonlocal last_reported_phase, last_reported_phase_detail
        if not state.runner_id:
            return
        cleaned_phase = str(phase).strip() if phase is not None else None
        cleaned_phase_detail = str(phase_detail).strip() if phase_detail is not None else None
        if (
            cleaned_phase is not None
            and cleaned_phase == last_reported_phase
            and cleaned_phase_detail == last_reported_phase_detail
            and status_override is None
            and recovery_reason is None
        ):
            return
        current_status = status_override or state.health.status
        response = runner_renew(
            base_url=base,
            room_id=args.room_id,
            token=args.token,
            runner_id=state.runner_id,
            attempt_id=state.attempt_id,
            execution_mode=state.execution_mode,
            status=current_status,
            capabilities=state.capabilities.to_payload(),
            lease_seconds=max(30, int(args.heartbeat_seconds * 3)),
            log_ref=state.health.log_path or None,
            last_error=state.health.last_error or None,
            recovery_reason=recovery_reason,
            phase=cleaned_phase,
            phase_detail=cleaned_phase_detail,
            managed_certified=state.capabilities.managed_certified,
            recovery_policy=state.capabilities.recovery_policy,
        )
        state.attempt_id = str(response.get("attempt_id") or state.attempt_id or "").strip() or state.attempt_id
        state.lease_expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + max(30, int(args.heartbeat_seconds * 3))))
        if cleaned_phase is not None:
            last_reported_phase = cleaned_phase
            last_reported_phase_detail = cleaned_phase_detail
        state.save(logger=lambda msg: log(msg))

    join_resp = http_json(
        "POST",
        f"{base}/rooms/{args.room_id}/join",
        token=args.token,
        payload={"client_name": "CodexBridge"},
    )
    participant_token = str(join_resp.get("participant_token") or "").strip()
    if participant_token:
        args.token = participant_token
        log("participant_session_token_acquired")
    participant_name = join_resp["participant"]
    room = join_resp["room"]
    state.participant = participant_name
    claim_resp = runner_claim(
        base_url=base,
        room_id=args.room_id,
        token=args.token,
        runner_id=state.runner_id,
        execution_mode=state.execution_mode,
        status="ready",
        capabilities=state.capabilities.to_payload(),
        lease_seconds=max(30, int(args.heartbeat_seconds * 3)),
        log_ref=state.health.log_path or None,
        last_error=state.health.last_error or None,
        attempt_id=state.attempt_id,
        phase="joined",
        phase_detail="participant_joined",
        managed_certified=state.capabilities.managed_certified,
        recovery_policy=state.capabilities.recovery_policy,
    )
    state.attempt_id = str(claim_resp.get("attempt_id") or state.attempt_id or "").strip() or state.attempt_id
    state.lease_expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + max(30, int(args.heartbeat_seconds * 3))))
    last_reported_phase = "joined"
    last_reported_phase_detail = "participant_joined"
    state.save(logger=lambda msg: log(msg))

    if role == "auto":
        turn_count = int(room.get("turn_count", 0))
        joined_count = sum(1 for p in (room.get("participants") or []) if p.get("joined"))
        if turn_count == 0 and joined_count <= 1:
            role = "initiator"
            log("auto-detected role: initiator")
        else:
            role = "responder"
            log("auto-detected role: responder")
    if role == "initiator":
        args.start = True
    log("joined", participant_name)
    log("backend", backend)

    def send_heartbeat_if_due(*, force: bool = False) -> None:
        nonlocal room, last_heartbeat_at
        now_ts = time.time()
        if not force and now_ts - last_heartbeat_at < args.heartbeat_seconds:
            return
        try:
            hb = http_json("POST", f"{base}/rooms/{args.room_id}/heartbeat", token=args.token, payload={})
            if isinstance(hb.get("room"), dict):
                room = hb["room"]
            renew_runner_claim()
            last_heartbeat_at = now_ts
        except Exception as exc:  # noqa: BLE001
            state.set_health(status="stalled", last_error=str(exc), recent_note="heartbeat_failed")
            state.save(logger=lambda msg: log(msg))
            log("heartbeat_failed", short(str(exc), 220))

    def call_model(prompt: str) -> dict[str, Any]:
        if args.offline_mock:
            return {
                "intent": "ANSWER",
                "text": "Mock Codex reply",
                "fills": {},
                "facts": [],
                "questions": [],
                "expect_reply": False,
                "meta": {"mock": True},
            }
        if backend == "codex-cli":
            return call_codex_cli(
                codex_bin=args.codex_bin,
                workdir=Path(args.codex_workdir).expanduser(),
                model=args.model,
                prompt=prompt,
                timeout_seconds=args.model_timeout_seconds,
            )
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required unless backend=codex-cli or --offline-mock is set")
        return call_openai(args.model or "gpt-5-mini", api_key, prompt)

    def call_model_with_retry(prompt: str, *, note: str, attempts: int = 2) -> dict[str, Any]:
        last_exc: RuntimeError | None = None
        total = max(1, int(attempts))
        for idx in range(1, total + 1):
            try:
                return call_model(prompt)
            except RuntimeError as exc:
                last_exc = exc
                log("model_call_failed", f"note={note}", f"attempt={idx}/{total}", short(str(exc), 260))
                if idx >= total:
                    break
                state.set_health(status="restarting", last_error=str(exc), recent_note=f"{note}_retry")
                state.save(logger=lambda msg: log(msg))
                time.sleep(min(max(args.poll_seconds, 0.5), 2.0))
        assert last_exc is not None
        raise last_exc

    def send_payload(payload: dict[str, Any], *, note: str, relay_event_id: int | None = None) -> None:
        nonlocal started_message_sent
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        if relay_event_id is not None and relay_event_id > 0:
            meta["in_reply_to_event_id"] = relay_event_id
        payload["meta"] = meta
        http_json("POST", f"{base}/rooms/{args.room_id}/messages", token=args.token, payload=payload)
        state.note_commitment(payload["text"])
        state.set_health(status="active", recent_note=note)
        try:
            renew_runner_claim(status_override="active", phase="reply_sent", phase_detail=note)
        except Exception as exc:  # noqa: BLE001
            log("runner_send_sync_failed", short(str(exc), 220))
        started_message_sent = True
        state.save(logger=lambda msg: log(msg))
        log("sent", payload["intent"], short(payload["text"], 120))

    def fallback_owner_unavailable(outgoing: dict[str, Any]) -> dict[str, Any]:
        return {
            "intent": "ASK",
            "text": outgoing["text"],
            "fills": outgoing["fills"],
            "facts": outgoing["facts"],
            "questions": outgoing["questions"],
            "expect_reply": True,
            "meta": {
                **(outgoing.get("meta") or {}),
                "owner_unavailable": True,
                "converted_from": "ASK_OWNER",
            },
        }

    send_heartbeat_if_due(force=True)
    try:
        renew_runner_claim(phase="event_polling", phase_detail="poll_ready")
    except Exception as exc:  # noqa: BLE001
        log("runner_poll_ready_sync_failed", short(str(exc), 220))

    try:
        while True:
            if args.max_seconds > 0 and time.time() - started > args.max_seconds:
                mark_shutdown("max_seconds_reached", note="max_seconds_reached", overwrite=False)
                log("max-seconds reached")
                break

            send_heartbeat_if_due()
            batch = http_json(
                "GET",
                f"{base}/rooms/{args.room_id}/events?after={state.cursor}&limit=200",
                token=args.token,
            )
            batch_events = list(batch.get("events") or [])
            room, relays, _ = next_relays(batch, state)
            state.save(logger=lambda msg: log(msg))

            if room["status"] != "active":
                mark_shutdown(
                    f"room_closed:{room.get('stop_reason')}",
                    note=f"room_closed:{room.get('stop_reason')}",
                    overwrite=False,
                )
                log("room ended", room.get("stop_reason"))
                break

            batch_has_message_activity = any(str(evt.get("type") or "") in {"msg", "relay"} for evt in batch_events)
            if (
                role == "initiator"
                and args.start
                and not started_message_sent
                and int(room.get("turn_count", 0)) == 0
                and not batch_has_message_activity
            ):
                joined_count = sum(1 for p in (room.get("participants") or []) if p.get("joined"))
                if joined_count >= 2:
                    try:
                        renew_runner_claim(status_override="active", phase="reply_generating", phase_detail="room_start")
                    except Exception as exc:  # noqa: BLE001
                        log("runner_generation_sync_failed", short(str(exc), 220))
                    prompt = build_room_reply_prompt(
                        role=role,
                        room=room,
                        self_name=participant_name,
                        latest_event=None,
                        has_started=started_message_sent,
                        owner_context=args.owner_context,
                        commitments=state.conversation.latest_commitments,
                        last_counterpart_ask=state.conversation.last_counterpart_ask,
                        last_counterpart_message=state.conversation.last_counterpart_message,
                    )
                    outgoing = normalize_message(call_model_with_retry(prompt, note="room_start"))
                    outgoing = coerce_opening_message(outgoing)
                    outgoing = coerce_terminal_message(outgoing, room)
                    try:
                        renew_runner_claim(status_override="active", phase="reply_ready", phase_detail=str(outgoing.get("intent") or "ANSWER"))
                    except Exception as exc:  # noqa: BLE001
                        log("runner_reply_ready_sync_failed", short(str(exc), 220))
                    if outgoing["intent"] == "ASK_OWNER" and not owner_reply_channel_available():
                        outgoing = fallback_owner_unavailable(outgoing)
                    guard_batch = http_json(
                        "GET",
                        f"{base}/rooms/{args.room_id}/events?after={state.cursor}&limit=200",
                        token=args.token,
                    )
                    guard_events = list(guard_batch.get("events") or [])
                    room, guard_relays, _ = next_relays(guard_batch, state)
                    state.save(logger=lambda msg: log(msg))
                    guard_has_message_activity = any(
                        str(evt.get("type") or "") in {"msg", "relay"} for evt in guard_events
                    )
                    if int(room.get("turn_count", 0)) > 0 or guard_has_message_activity:
                        relays = [*guard_relays, *relays]
                        log("skip room_start; peer activity arrived during kickoff generation")
                    else:
                        if outgoing.get("meta", {}).get("opening_coercion"):
                            log("coerced opening message", json.dumps(outgoing.get("meta", {}).get("opening_coercion")))
                        send_payload(outgoing, note="room_start")
                        time.sleep(args.poll_seconds)
                        continue
                if not kickoff_wait_logged:
                    state.set_health(status="idle", recent_note="waiting_for_peer_join")
                    state.save(logger=lambda msg: log(msg))
                    try:
                        renew_runner_claim(
                            status_override="idle",
                            recovery_reason="waiting_for_peer_join",
                            phase="waiting_for_peer_join",
                            phase_detail="initiator_waiting_for_peer",
                        )
                    except Exception as exc:  # noqa: BLE001
                        log("runner_peer_wait_sync_failed", short(str(exc), 220))
                    log("waiting for peer join before initiator kickoff")
                    kickoff_wait_logged = True

            if not relays:
                state.set_health(status="idle", recent_note="poll_idle")
                state.save(logger=lambda msg: log(msg))
                try:
                    renew_runner_claim(phase="event_polling", phase_detail="poll_idle")
                except Exception as exc:  # noqa: BLE001
                    log("runner_idle_sync_failed", short(str(exc), 220))

            relay_queue = list(relays)
            while relay_queue:
                evt = relay_queue.pop(0)
                msg = (evt.get("payload") or {}).get("message") or {}
                state.note_counterpart_message(intent=str(msg.get("intent") or ""), text=str(msg.get("text") or ""))
                if not relay_requires_reply(evt):
                    continue
                try:
                    renew_runner_claim(
                        status_override="active",
                        phase="relay_seen",
                        phase_detail=str(msg.get("intent") or "relay"),
                    )
                except Exception as exc:  # noqa: BLE001
                    log("runner_relay_seen_sync_failed", short(str(exc), 220))
                relay_event_id = int(evt.get("id", 0))
                try:
                    renew_runner_claim(status_override="active", phase="reply_generating", phase_detail="relay")
                except Exception as exc:  # noqa: BLE001
                    log("runner_generation_sync_failed", short(str(exc), 220))
                prompt = build_room_reply_prompt(
                    role=role,
                    room=room,
                    self_name=participant_name,
                    latest_event=evt,
                    has_started=started_message_sent,
                    owner_context=args.owner_context,
                    commitments=state.conversation.latest_commitments,
                    last_counterpart_ask=state.conversation.last_counterpart_ask,
                    last_counterpart_message=state.conversation.last_counterpart_message,
                )
                outgoing = normalize_message(call_model_with_retry(prompt, note="relay"))
                try:
                    renew_runner_claim(status_override="active", phase="reply_ready", phase_detail=str(outgoing.get("intent") or "ANSWER"))
                except Exception as exc:  # noqa: BLE001
                    log("runner_reply_ready_sync_failed", short(str(exc), 220))
                guard_batch = http_json(
                    "GET",
                    f"{base}/rooms/{args.room_id}/events?after={state.cursor}&limit=200",
                    token=args.token,
                )
                room, guard_relays, _ = next_relays(guard_batch, state)
                state.save(logger=lambda msg: log(msg))
                if guard_relays:
                    relay_queue = [*guard_relays, *relay_queue]
                    log("skip relay send; newer peer activity arrived during generation")
                    continue
                outgoing = coerce_terminal_message(outgoing, room)
                if outgoing["intent"] == "ASK_OWNER":
                    if not owner_reply_channel_available():
                        outgoing = fallback_owner_unavailable(outgoing)
                        send_payload(outgoing, note="relay_owner_unavailable", relay_event_id=relay_event_id)
                        continue

                    owner_req_id = f"oreq_{uuid.uuid4().hex[:12]}"
                    outgoing.setdefault("meta", {})
                    outgoing["meta"]["owner_req_id"] = owner_req_id
                    outgoing["expect_reply"] = False
                    send_payload(outgoing, note="relay_ask_owner", relay_event_id=relay_event_id)
                    state.set_pending_owner_request(owner_req_id)
                    state.set_health(status="waiting_owner", recent_note="waiting_owner_reply")
                    try:
                        renew_runner_claim(
                            status_override="waiting_owner",
                            phase="owner_wait",
                            phase_detail="waiting_owner_reply",
                        )
                    except Exception as exc:  # noqa: BLE001
                        log("runner_waiting_owner_sync_failed", short(str(exc), 220))
                    state.save(logger=lambda msg: log(msg))
                    notify_owner(args.owner_notify_cmd, outgoing["text"], owner_req_id)
                    owner_reply = wait_owner_reply(
                        owner_req_id=owner_req_id,
                        timeout_seconds=args.owner_wait_timeout_seconds,
                        owner_reply_file=owner_reply_file,
                        owner_reply_fetcher=fetch_owner_reply,
                        poll_seconds=args.owner_reply_poll_seconds,
                        on_poll=lambda: send_heartbeat_if_due(),
                    )
                    if owner_reply:
                        oprompt = build_owner_reply_prompt(
                            room=room,
                            self_name=participant_name,
                            role=role,
                            owner_req_id=owner_req_id,
                            owner_text=owner_reply,
                            owner_context=args.owner_context,
                            commitments=state.conversation.latest_commitments,
                        )
                        owner_payload = normalize_message(call_model_with_retry(oprompt, note="owner_reply"))
                        owner_payload["intent"] = "OWNER_REPLY"
                        owner_payload.setdefault("meta", {})
                        owner_payload["meta"]["owner_req_id"] = owner_req_id
                        owner_payload["expect_reply"] = True
                        # OWNER_REPLY resumes the runner after an owner-only pause.
                        # It is not a second reply to the original peer relay, so it
                        # must not reuse the same in_reply_to_event_id or the room's
                        # reply dedup key will swallow it as a duplicate.
                        send_payload(owner_payload, note="owner_reply", relay_event_id=None)
                    else:
                        timeout_payload = {
                            "intent": "NOTE",
                            "text": "Owner did not reply in time; continuing without owner input.",
                            "fills": {},
                            "facts": [],
                            "questions": [],
                            "expect_reply": False,
                            "meta": {"owner_req_id": owner_req_id, "timeout": True},
                        }
                        send_payload(timeout_payload, note="owner_timeout", relay_event_id=None)
                    state.set_pending_owner_request(None)
                    state.set_health(status="active", recent_note="owner_wait_resolved")
                    try:
                        renew_runner_claim(
                            status_override="active",
                            phase="owner_reply_handled",
                            phase_detail="owner_wait_resolved",
                        )
                    except Exception as exc:  # noqa: BLE001
                        log("runner_owner_resume_sync_failed", short(str(exc), 220))
                    state.save(logger=lambda msg: log(msg))
                    continue

                send_payload(outgoing, note="relay", relay_event_id=relay_event_id)

            time.sleep(args.poll_seconds)
    finally:
        state.set_pending_owner_request(None)
        effective_last_error = shutdown_last_error or state.health.last_error or ""
        state.set_health(status="exited", last_error=effective_last_error, recent_note=shutdown_note)
        state.save(logger=lambda msg: log(msg))
        try:
            if state.runner_id:
                runner_release(
                    base_url=base,
                    room_id=args.room_id,
                    token=args.token,
                    runner_id=state.runner_id,
                    attempt_id=state.attempt_id,
                    status="exited",
                    reason=shutdown_reason,
                    last_error=effective_last_error or None,
                )
        except Exception:
            pass
        try:
            http_json(
                "POST",
                f"{base}/rooms/{args.room_id}/leave",
                token=args.token,
                payload={"reason": shutdown_reason},
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
