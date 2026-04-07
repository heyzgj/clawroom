from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    parse_join_url,
    relay_requires_reply,
    runner_claim,
    runner_release,
    runner_renew,
)


def log(*parts: object) -> None:
    print("[openclaw-bridge]", *parts, flush=True)


def short(s: str, n: int = 220) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "..."


def is_session_lock_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "session file locked",
        "session lock timeout",
        "lock timeout",
        "locked (timeout",
    )
    return any(marker in text for marker in markers)


def openclaw_cmd_prefix(*, profile: str | None, dev: bool) -> list[str]:
    cmd = ["openclaw"]
    if dev:
        cmd.append("--dev")
    if profile:
        cmd.extend(["--profile", profile])
    return cmd


def is_affirmative(text: str) -> bool:
    value = text.strip().lower()
    return value in {"y", "yes", "ok", "approve", "approved", "true", "1", "join", "go"}


def preflight_request_text(*, room: dict[str, Any], participant: str) -> str:
    outcomes = room.get("expected_outcomes") or room.get("required_fields") or []
    outcomes_txt = ", ".join(str(x) for x in outcomes) if outcomes else "(none)"
    return (
        f"ClawRoom preflight confirmation needed.\n"
        f"- participant: {participant}\n"
        f"- topic: {room.get('topic')}\n"
        f"- goal: {room.get('goal')}\n"
        f"- expected_outcomes: {outcomes_txt}\n"
        "Reply yes to join, no to reject."
    )


def extract_first_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start : i + 1]
                    return json.loads(chunk)
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found")


@dataclass(slots=True)
class OpenClawRunner:
    agent_id: str
    profile: str | None
    dev: bool
    timeout_seconds: int
    thinking: str

    def session_id_for(self, room_id: str, participant_name: str) -> str:
        seed = f"clawroom-v2:{room_id}:{self.agent_id}:{participant_name}"
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))

    def ask_json(
        self,
        room_id: str,
        participant_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        effective_session_id = session_id or self.session_id_for(room_id, participant_name)
        cmd = ["openclaw"]
        if self.dev:
            cmd.append("--dev")
        if self.profile:
            cmd.extend(["--profile", self.profile])
        cmd.extend(
            [
                "agent",
                "--local",
                "--json",
                "--agent",
                self.agent_id,
                "--session-id",
                effective_session_id,
                "--message",
                prompt,
                "--timeout",
                str(self.timeout_seconds),
                "--thinking",
                self.thinking,
            ]
        )
        log("calling openclaw", f"agent={self.agent_id}", f"session={effective_session_id}")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = short(proc.stderr.strip() or "(no stderr)", 1200)
            stdout = short(proc.stdout.strip() or "(no stdout)", 1200)
            raise RuntimeError(f"openclaw failed rc={proc.returncode} stdout={stdout} stderr={stderr}")
        # OpenClaw may emit diagnostic lines before JSON (e.g. auth-profile inheritance).
        # Strip leading non-JSON lines to find the actual payload.
        stdout = proc.stdout
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            # Try stripping leading diagnostic lines
            for i, line in enumerate(stdout.splitlines()):
                stripped = line.lstrip()
                if stripped.startswith("{") or stripped.startswith("["):
                    rest = "\n".join(stdout.splitlines()[i:])
                    try:
                        parsed = json.loads(rest)
                        break
                    except json.JSONDecodeError:
                        continue
            else:
                raise RuntimeError(f"openclaw returned non-json stdout: {short(stdout, 800)}")

        payloads = parsed.get("payloads") or []
        texts = [p.get("text", "") for p in payloads if isinstance(p, dict)]
        text = "\n".join([t for t in texts if t]).strip()
        if not text:
            raise RuntimeError("openclaw returned no text payload")
        log("openclaw text", short(text, 300))
        return extract_first_json_object(text)


def normalize_model_message(raw: dict[str, Any], *, fallback_intent: str = "ANSWER") -> dict[str, Any]:
    intent = str(raw.get("intent", fallback_intent)).upper().strip()
    if intent == "NEED_HUMAN":
        intent = "ASK_OWNER"
    valid = {"ASK", "ANSWER", "NOTE", "DONE", "ASK_OWNER", "OWNER_REPLY"}
    if intent not in valid:
        intent = fallback_intent

    text = str(raw.get("text", "")).strip() or "(no text)"

    fills = {}
    if isinstance(raw.get("fills"), dict):
        for k, v in raw["fills"].items():
            ks = str(k).strip()
            vs = str(v).strip()
            if ks and vs:
                fills[ks] = vs

    facts = [str(x).strip() for x in raw.get("facts", [])] if isinstance(raw.get("facts"), list) else []
    facts = [x for x in facts if x]

    questions = [str(x).strip() for x in raw.get("questions", [])] if isinstance(raw.get("questions"), list) else []
    questions = [x for x in questions if x]

    # Enforce semantic invariants so a model cannot accidentally stall the room.
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
        "fills": fills,
        "facts": facts,
        "questions": questions,
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


def notify_owner(cmd_template: str | None, text: str, owner_req_id: str) -> None:
    if not cmd_template:
        return
    command = cmd_template.format(text=text, owner_req_id=owner_req_id)
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    if proc.returncode != 0:
        log("owner notify failed", short(proc.stderr or proc.stdout, 400))
    else:
        log("owner notified", owner_req_id)


def notify_owner_openclaw(
    *,
    channel: str,
    target: str,
    account: str | None,
    text: str,
    owner_req_id: str,
    profile: str | None,
    dev: bool,
) -> bool:
    message = (
        "[ClawRoom owner_request]\n"
        f"owner_req_id={owner_req_id}\n"
        f"{text}\n\n"
        "Reply with one line:\n"
        "<owner_req_id>\\t<your reply>"
    )
    cmd = openclaw_cmd_prefix(profile=profile, dev=dev)
    cmd.extend(
        [
            "message",
            "send",
            "--channel",
            channel,
            "--target",
            target,
            "--message",
            message,
            "--json",
        ]
    )
    if account:
        cmd.extend(["--account", account])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log("owner notify failed", short(proc.stderr or proc.stdout, 400))
        return False
    log("owner notified", owner_req_id, f"channel={channel}", f"target={target}")
    return True


def is_openclaw_read_unsupported(output: str) -> bool:
    text = (output or "").lower()
    markers = (
        "action read is not supported",
        "message action read not supported",
        "not supported for provider",
        "not supported for channel",
    )
    return any(marker in text for marker in markers)


def probe_openclaw_read_capability(
    *,
    channel: str,
    target: str,
    account: str | None,
    profile: str | None,
    dev: bool,
    limit: int,
) -> tuple[bool | None, str]:
    cmd = openclaw_cmd_prefix(profile=profile, dev=dev)
    cmd.extend(
        [
            "message",
            "read",
            "--channel",
            channel,
            "--target",
            target,
            "--limit",
            str(max(1, min(limit, 200))),
            "--json",
        ]
    )
    if account:
        cmd.extend(["--account", account])

    proc = subprocess.run(cmd, capture_output=True, text=True)
    combined = "\n".join(x for x in [(proc.stderr or "").strip(), (proc.stdout or "").strip()] if x)
    if proc.returncode == 0:
        return True, "ok"
    if is_openclaw_read_unsupported(combined):
        return False, short(combined, 220)
    # Inconclusive errors (auth/network/etc) should not hard fail startup.
    return None, short(combined, 220)


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
        m = re.search(rf"owner_req_id\s*=\s*{req}\s*;\s*reply\s*=\s*(.+)$", line, flags=re.IGNORECASE)
        if m:
            reply = m.group(1).strip()
            if reply:
                return reply
    return None


def parse_owner_reply_from_json(value: Any, owner_req_id: str) -> str | None:
    if isinstance(value, dict):
        req_keys = ("owner_req_id", "ownerReqId", "req_id", "request_id")
        value_req_id = None
        for key in req_keys:
            if key in value and value[key] is not None:
                value_req_id = str(value[key]).strip()
                break
        if value_req_id == owner_req_id:
            for key in ("reply", "owner_reply", "text", "message", "content", "body"):
                payload = value.get(key)
                if isinstance(payload, str) and payload.strip():
                    marker_reply = parse_owner_reply_marker(payload, owner_req_id)
                    if marker_reply:
                        return marker_reply
                    if key in {"reply", "owner_reply"}:
                        return payload.strip()
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


def read_owner_reply_from_command(
    *,
    cmd_template: str,
    owner_req_id: str,
    seen_signatures: set[str],
) -> str | None:
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

    sig = f"{owner_req_id}\u0000{reply}"
    if sig in seen_signatures:
        return None
    seen_signatures.add(sig)
    return reply


def read_owner_reply_from_openclaw(
    *,
    owner_req_id: str,
    channel: str,
    target: str,
    account: str | None,
    profile: str | None,
    dev: bool,
    limit: int,
    seen_signatures: set[str],
) -> tuple[str | None, bool]:
    cmd = openclaw_cmd_prefix(profile=profile, dev=dev)
    cmd.extend(
        [
            "message",
            "read",
            "--channel",
            channel,
            "--target",
            target,
            "--limit",
            str(max(1, min(limit, 200))),
            "--json",
        ]
    )
    if account:
        cmd.extend(["--account", account])

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        combined = "\n".join(x for x in [(proc.stderr or "").strip(), (proc.stdout or "").strip()] if x)
        if is_openclaw_read_unsupported(combined):
            return None, True
        log("openclaw read failed", short(combined, 240))
        return None, False
    out = (proc.stdout or "").strip()
    if not out:
        return None, False

    reply: str | None = None
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        reply = parse_owner_reply_marker(out, owner_req_id)
    else:
        reply = parse_owner_reply_from_json(parsed, owner_req_id)

    if not reply:
        return None, False

    sig = f"{owner_req_id}\u0000{reply}"
    if sig in seen_signatures:
        return None, False
    seen_signatures.add(sig)
    return reply, False


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
    fail_fast_fetch_errors: bool = False,
    on_poll: Any = None,
) -> str | None:
    started = time.time()
    while time.time() - started <= timeout_seconds:
        if owner_reply_file:
            reply = read_owner_reply_from_file(owner_reply_file, owner_req_id)
            if reply:
                return reply
        if owner_reply_fetcher:
            try:
                reply = owner_reply_fetcher(owner_req_id)
            except Exception as exc:  # noqa: BLE001
                if fail_fast_fetch_errors:
                    raise
                log("owner reply fetch failed", short(str(exc), 240))
                reply = None
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
    parser = argparse.ArgumentParser(
        description="OpenClaw ClawRoom adapter",
        epilog='Example: %(prog)s "http://localhost:8787/join/room_abc?token=inv_..."',
    )
    # Positional: join URL (optional — for the simplified one-arg UX)
    parser.add_argument(
        "join_url",
        nargs="?",
        default=None,
        help="Join URL (e.g. http://host/join/room_id?token=inv_...). Overrides --base-url, --room-id, --token.",
    )
    # Long-form flags (still work for backward compat)
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--room-id", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--agent-id", default="main")
    parser.add_argument("--role", choices=["initiator", "responder", "auto"], default="auto")
    parser.add_argument("--client-name", default=None)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=5.0)
    parser.add_argument("--cursor", type=int, default=0, help="Initial events cursor for handoff mode.")
    parser.add_argument(
        "--state-path",
        default="",
        help="Optional runner state path for cursor/seen persistence. Default: ~/.openclaw/agents/<agent_id>/clawroom/<room_id>.json",
    )
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=0,
        help="Max runtime in seconds; set 0 to disable timeout and keep loop alive.",
    )
    parser.add_argument(
        "--owner-context",
        default="",
        help="Optional owner constraints/context injected into every model prompt.",
    )
    parser.add_argument("--openclaw-timeout", type=int, default=90)
    parser.add_argument("--thinking", choices=["off", "minimal", "low", "medium", "high"], default="minimal")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--print-result", action="store_true")
    parser.add_argument("--owner-wait-timeout-seconds", type=int, default=1800)
    parser.add_argument("--preflight-mode", choices=["confirm", "auto", "off"], default="confirm")
    parser.add_argument("--preflight-timeout-seconds", type=int, default=300)
    parser.add_argument("--trusted-auto-join", action="store_true")
    parser.add_argument(
        "--owner-channel",
        choices=["auto", "openclaw"],
        default="auto",
        help="Owner comms channel. auto=file/cmd/stdin fallback, openclaw=message send/read.",
    )
    parser.add_argument(
        "--owner-openclaw-channel",
        default=None,
        help="OpenClaw channel for owner comms (used when --owner-channel=openclaw).",
    )
    parser.add_argument(
        "--owner-openclaw-target",
        default=None,
        help="OpenClaw target/chat for owner comms (used when --owner-channel=openclaw).",
    )
    parser.add_argument(
        "--owner-openclaw-account",
        default=None,
        help="Optional OpenClaw account id for owner comms.",
    )
    parser.add_argument(
        "--owner-openclaw-read-limit",
        type=int,
        default=30,
        help="OpenClaw read limit for owner reply polling.",
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
        help="Polling interval for owner reply file/cmd/openclaw channel.",
    )
    parser.add_argument(
        "--owner-notify-cmd",
        default=None,
        help="Optional shell template with {text} and {owner_req_id}",
    )
    parser.add_argument(
        "--owner-reply-file",
        default=None,
        help="Optional file polled for owner replies, format: owner_req_id<TAB>reply text",
    )
    args = parser.parse_args()

    # --- Resolve connection params ---
    if args.join_url:
        try:
            parsed = parse_join_url(args.join_url)
        except ValueError as exc:
            parser.error(str(exc))
        base = parsed["base_url"].rstrip("/")
        room_id = parsed["room_id"]
        token = parsed["token"]
        log(f"parsed join URL → base={base} room={room_id}")
    else:
        base = args.base_url.rstrip("/")
        room_id = args.room_id
        token = args.token
        if not room_id or not token:
            parser.error("either provide a join URL or both --room-id and --token")

    role = args.role
    if args.heartbeat_seconds < 1:
        parser.error("--heartbeat-seconds must be >= 1")

    runner = OpenClawRunner(
        agent_id=args.agent_id,
        profile=args.profile,
        dev=args.dev,
        timeout_seconds=args.openclaw_timeout,
        thinking=args.thinking,
    )

    started_at = time.time()
    default_state_path = Path.home() / ".openclaw" / "agents" / args.agent_id / "clawroom" / f"{room_id}.json"
    state_path = Path(args.state_path).expanduser() if args.state_path else default_state_path
    state = build_runner_state(
        base_url=base,
        room_id=room_id,
        token=token,
        initial_cursor=max(0, int(args.cursor)),
        state_path=state_path,
        logger=lambda msg: log(msg),
    )
    state.note_owner_context(args.owner_context)
    if not state.runner_id:
        state.runner_id = f"openclaw:{args.agent_id}:{uuid.uuid4().hex[:10]}"
    state.execution_mode = "managed_attached"
    started_message_sent = False
    kickoff_wait_logged = False
    owner_reply_file = Path(args.owner_reply_file) if args.owner_reply_file else None
    owner_reply_seen: set[str] = set()
    openclaw_read_enabled = args.owner_channel == "openclaw"

    if args.owner_channel == "openclaw":
        if not args.owner_openclaw_channel or not args.owner_openclaw_target:
            parser.error("--owner-channel openclaw requires --owner-openclaw-channel and --owner-openclaw-target")
        support, reason = probe_openclaw_read_capability(
            channel=args.owner_openclaw_channel,
            target=args.owner_openclaw_target,
            account=args.owner_openclaw_account,
            profile=args.profile,
            dev=args.dev,
            limit=args.owner_openclaw_read_limit,
        )
        if support is False:
            openclaw_read_enabled = False
            if args.owner_reply_cmd or owner_reply_file:
                log(
                    "openclaw read unsupported; using fallback",
                    f"reason={reason}",
                )
            else:
                parser.error(
                    "openclaw message read not supported for this channel/target; provide --owner-reply-cmd or --owner-reply-file"
                )
        elif support is None:
            log("openclaw read probe inconclusive; will try runtime reads", f"reason={reason}")
        else:
            log("openclaw read probe supported", f"channel={args.owner_openclaw_channel}")

    def notify_owner_request(text: str, owner_req_id: str) -> bool:
        if args.owner_notify_cmd:
            notify_owner(args.owner_notify_cmd, text, owner_req_id)
            return True
        if args.owner_channel == "openclaw":
            return notify_owner_openclaw(
                channel=args.owner_openclaw_channel,
                target=args.owner_openclaw_target,
                account=args.owner_openclaw_account,
                text=text,
                owner_req_id=owner_req_id,
                profile=args.profile,
                dev=args.dev,
            )
        return False

    def fetch_owner_reply(owner_req_id: str) -> str | None:
        nonlocal openclaw_read_enabled
        if args.owner_reply_cmd:
            reply = read_owner_reply_from_command(
                cmd_template=args.owner_reply_cmd,
                owner_req_id=owner_req_id,
                seen_signatures=owner_reply_seen,
            )
            if reply:
                return reply
        if args.owner_channel == "openclaw" and openclaw_read_enabled:
            reply, unsupported = read_owner_reply_from_openclaw(
                owner_req_id=owner_req_id,
                channel=args.owner_openclaw_channel,
                target=args.owner_openclaw_target,
                account=args.owner_openclaw_account,
                profile=args.profile,
                dev=args.dev,
                limit=args.owner_openclaw_read_limit,
                seen_signatures=owner_reply_seen,
            )
            if unsupported:
                openclaw_read_enabled = False
                if args.owner_reply_cmd or owner_reply_file:
                    log("openclaw read unsupported at runtime; switching fallback")
                else:
                    raise RuntimeError(
                        "openclaw message read unsupported at runtime and no fallback configured; use --owner-reply-cmd or --owner-reply-file"
                    )
            return reply
        return None

    def owner_reply_channel_available() -> bool:
        if args.owner_reply_cmd or owner_reply_file:
            return True
        if args.owner_channel == "openclaw" and openclaw_read_enabled:
            return True
        return bool(sys.stdin.isatty())

    preflight_meta: dict[str, Any] = {"mode": args.preflight_mode, "status": "off"}
    if args.preflight_mode != "off":
        join_info = http_json("GET", f"{base}/join/{room_id}?token={token}")
        preview_participant = str(join_info.get("participant") or "")
        preview_room = (join_info.get("room") or {}) if isinstance(join_info.get("room"), dict) else {}
        request_text = preflight_request_text(room=preview_room, participant=preview_participant or "(unknown)")
        log("preflight", f"mode={args.preflight_mode}", f"participant={preview_participant or '(unknown)'}")
        log("preflight room", f"topic={preview_room.get('topic')}", f"goal={preview_room.get('goal')}")

        approved = False
        channel = ""

        if args.preflight_mode == "auto" and args.trusted_auto_join:
            approved = True
            channel = "trusted_auto_join"
            log("preflight approved", "trusted_auto_join")
        else:
            if owner_reply_file or args.owner_reply_cmd or args.owner_channel == "openclaw":
                if owner_reply_file:
                    channel = "owner_reply_file"
                elif args.owner_reply_cmd:
                    channel = "owner_reply_cmd"
                else:
                    channel = "openclaw"
                preflight_req_id = f"pfreq_{uuid.uuid4().hex[:12]}"
                was_notified = notify_owner_request(request_text, preflight_req_id)
                if not was_notified and owner_reply_file:
                    log(
                        "preflight pending",
                        f"append to {owner_reply_file}: {preflight_req_id}<TAB>yes|no",
                    )
                    log("preflight request", short(request_text, 400))
                owner_reply = wait_owner_reply(
                    owner_req_id=preflight_req_id,
                    timeout_seconds=args.preflight_timeout_seconds,
                    owner_reply_file=owner_reply_file,
                    owner_reply_fetcher=fetch_owner_reply,
                    poll_seconds=args.owner_reply_poll_seconds,
                    fail_fast_fetch_errors=True,
                )
                if not owner_reply:
                    raise RuntimeError("preflight confirmation timed out")
                approved = is_affirmative(owner_reply)
            elif sys.stdin.isatty():
                channel = "stdin"
                print("\n=== ClawRoom Join Preflight ===", flush=True)
                print(request_text, flush=True)
                answer = input("Join this room now? [y/N]: ").strip()
                approved = is_affirmative(answer)
            else:
                raise RuntimeError(
                    "preflight confirm requires owner channel (--owner-reply-file/--owner-reply-cmd/--owner-channel openclaw) or interactive stdin"
                )

        if not approved:
            log("preflight rejected", f"channel={channel or 'unknown'}")
            return

        preflight_meta = {
            "mode": args.preflight_mode,
            "status": "approved",
            "channel": channel or "confirm",
            "trusted_auto_join": bool(args.trusted_auto_join),
        }

    join_resp = http_json(
        "POST",
        f"{base}/rooms/{room_id}/join",
        token=token,
        payload={"client_name": args.client_name or f"OpenClaw({args.agent_id})"},
    )
    participant_token = str(join_resp.get("participant_token") or "").strip()
    if participant_token:
        token = participant_token
        log("participant_session_token_acquired")
    participant_name = str(join_resp["participant"])
    room = join_resp["room"]
    state.participant = participant_name
    if not state.runtime_session_id:
        state.runtime_session_id = f"clawrun_{uuid.uuid4().hex}"
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
    state.save(logger=lambda msg: log(msg))
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
            room_id=room_id,
            token=token,
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

    claim_resp = runner_claim(
        base_url=base,
        room_id=room_id,
        token=token,
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

    # --- Auto-detect role ---
    if role == "auto":
        turn_count = int(room.get("turn_count", 0))
        joined_count = sum(1 for p in (room.get("participants") or []) if p.get("joined"))
        if turn_count == 0 and joined_count <= 1:
            role = "initiator"
            log("auto-detected role: initiator (first participant in fresh room)")
        else:
            role = "responder"
            log("auto-detected role: responder")

    # If role is initiator, auto-enable --start.
    if role == "initiator":
        args.start = True
    try:
        renew_runner_claim(status_override="ready", phase="session_ready", phase_detail=f"role={role}")
    except Exception as exc:  # noqa: BLE001
        log("runner_phase_sync_failed", short(str(exc), 220))

    log("joined", f"participant={participant_name}", f"room={room['id']}", f"status={room['status']}")
    last_heartbeat_at = 0.0

    def send_message(
        payload: dict[str, Any],
        why: str,
        *,
        in_reply_to_event_id: int | None = None,
    ) -> dict[str, Any]:
        nonlocal started_message_sent
        payload.setdefault("meta", {})
        if not isinstance(payload.get("meta"), dict):
            payload["meta"] = {}
        payload["meta"].setdefault("preflight", preflight_meta)
        if in_reply_to_event_id is not None and in_reply_to_event_id > 0:
            payload["meta"]["in_reply_to_event_id"] = int(in_reply_to_event_id)
        log("send", f"why={why}", payload["intent"], short(payload["text"], 180))
        resp = http_json(
            "POST",
            f"{base}/rooms/{room_id}/messages",
            token=token,
            payload=payload,
        )
        state.note_commitment(payload["text"])
        state.set_health(status="active", recent_note=why)
        try:
            renew_runner_claim(status_override="active", phase="reply_sent", phase_detail=why)
        except Exception as exc:  # noqa: BLE001
            log("runner_send_sync_failed", short(str(exc), 220))
        started_message_sent = True
        trigger = (((resp or {}).get("host_decision") or {}).get("trigger"))
        if trigger:
            log("host_trigger", trigger)
        return resp

    def send_heartbeat_if_due(*, force: bool = False) -> None:
        nonlocal room, last_heartbeat_at
        now_ts = time.time()
        if not force and now_ts - last_heartbeat_at < args.heartbeat_seconds:
            return
        try:
            hb = http_json("POST", f"{base}/rooms/{room_id}/heartbeat", token=token, payload={})
            if isinstance(hb.get("room"), dict):
                room = hb["room"]
            renew_runner_claim()
            last_heartbeat_at = now_ts
        except Exception as exc:  # noqa: BLE001
            state.set_health(status="stalled", last_error=str(exc), recent_note="heartbeat_failed")
            state.save(logger=lambda msg: log(msg))
            log("heartbeat_failed", short(str(exc), 220))

    def generate_model_reply(latest_event: dict[str, Any] | None, room_snapshot: dict[str, Any]) -> dict[str, Any]:
        prompt = build_room_reply_prompt(
            role=role,
            room=room_snapshot,
            self_name=participant_name,
            latest_event=latest_event,
            has_started=started_message_sent,
            owner_context=args.owner_context,
            commitments=state.conversation.latest_commitments,
            last_counterpart_ask=state.conversation.last_counterpart_ask,
            last_counterpart_message=state.conversation.last_counterpart_message,
        )
        try:
            renew_runner_claim(status_override="active", phase="reply_generating", phase_detail="model_call")
        except Exception as exc:  # noqa: BLE001
            log("runner_generation_sync_failed", short(str(exc), 220))
        try:
            raw = runner.ask_json(
                room_id,
                participant_name,
                prompt,
                session_id=state.runtime_session_id,
            )
        except RuntimeError as exc:
            if not is_session_lock_error(exc):
                raise
            old_session = state.runtime_session_id or "(none)"
            state.set_health(status="restarting", last_error=str(exc), recent_note="session_lock_retry")
            state.runtime_session_id = f"clawrun_{uuid.uuid4().hex}"
            state.save(logger=lambda msg: log(msg))
            log(
                "session_lock_retry",
                f"old={short(old_session, 24)}",
                f"new={short(state.runtime_session_id, 24)}",
            )
            raw = runner.ask_json(
                room_id,
                participant_name,
                prompt,
                session_id=state.runtime_session_id,
            )
        fallback = "ASK" if role == "initiator" and not started_message_sent else "ANSWER"
        message = normalize_model_message(raw, fallback_intent=fallback)
        try:
            renew_runner_claim(status_override="active", phase="reply_ready", phase_detail=str(message.get("intent") or "ANSWER"))
        except Exception as exc:  # noqa: BLE001
            log("runner_reply_ready_sync_failed", short(str(exc), 220))
        return message

    send_heartbeat_if_due(force=True)
    try:
        renew_runner_claim(phase="event_polling", phase_detail="poll_ready")
    except Exception as exc:  # noqa: BLE001
        log("runner_poll_ready_sync_failed", short(str(exc), 220))
    try:
        while True:
            if args.max_seconds > 0 and time.time() - started_at > args.max_seconds:
                mark_shutdown("max_seconds_reached", note="max_seconds_reached", overwrite=False)
                log("max-seconds reached")
                break

            send_heartbeat_if_due()
            batch = http_json(
                "GET",
                f"{base}/rooms/{room_id}/events?after={state.cursor}&limit=200",
                token=token,
            )
            batch_events = list(batch.get("events") or [])
            room, new_relays, _ = next_relays(batch, state)
            state.save(logger=lambda msg: log(msg))

            if room["status"] != "active":
                mark_shutdown(
                    f"room_closed:{room.get('stop_reason')}",
                    note=f"room_closed:{room.get('stop_reason')}",
                    overwrite=False,
                )
                log("room ended", f"status={room['status']}", f"reason={room['stop_reason']}")
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
                    outgoing = generate_model_reply(None, room)
                    outgoing = coerce_opening_message(outgoing)
                    outgoing = coerce_terminal_message(outgoing, room)
                    guard_batch = http_json(
                        "GET",
                        f"{base}/rooms/{room_id}/events?after={state.cursor}&limit=200",
                        token=token,
                    )
                    guard_events = list(guard_batch.get("events") or [])
                    room, guard_relays, _ = next_relays(guard_batch, state)
                    state.save(logger=lambda msg: log(msg))
                    guard_has_message_activity = any(
                        str(evt.get("type") or "") in {"msg", "relay"} for evt in guard_events
                    )
                    if int(room.get("turn_count", 0)) > 0 or guard_has_message_activity:
                        new_relays = [*guard_relays, *new_relays]
                        log("skip room_start; peer activity arrived during kickoff generation")
                    else:
                        if outgoing.get("meta", {}).get("opening_coercion"):
                            log("coerced opening message", json.dumps(outgoing.get("meta", {}).get("opening_coercion")))
                        send_message(outgoing, "room_start")
                        state.save(logger=lambda msg: log(msg))
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

            if new_relays:
                relay_queue = list(new_relays)
                while relay_queue:
                    evt = relay_queue.pop(0)
                    relay_msg = (evt.get("payload") or {}).get("message") or {}
                    state.note_counterpart_message(
                        intent=str(relay_msg.get("intent") or ""),
                        text=str(relay_msg.get("text") or ""),
                    )
                    if not relay_requires_reply(evt):
                        continue
                    try:
                        renew_runner_claim(
                            status_override="active",
                            phase="relay_seen",
                            phase_detail=str(relay_msg.get("intent") or "relay"),
                        )
                    except Exception as exc:  # noqa: BLE001
                        log("runner_relay_seen_sync_failed", short(str(exc), 220))
                    relay_event_id = int(evt.get("id", 0))
                    outgoing = generate_model_reply(evt, room)
                    guard_batch = http_json(
                        "GET",
                        f"{base}/rooms/{room_id}/events?after={state.cursor}&limit=200",
                        token=token,
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
                            fallback_payload = {
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
                            log(
                                "owner channel unavailable; converting ASK_OWNER to ASK",
                                short(fallback_payload["text"], 140),
                            )
                            send_message(
                                fallback_payload,
                                "relay_owner_unavailable",
                                in_reply_to_event_id=relay_event_id,
                            )
                            continue

                        outgoing["expect_reply"] = False
                        owner_req_id = f"oreq_{uuid.uuid4().hex[:12]}"
                        outgoing.setdefault("meta", {})
                        outgoing["meta"]["owner_req_id"] = owner_req_id
                        send_message(outgoing, "relay_ask_owner", in_reply_to_event_id=relay_event_id)
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
                        was_notified = notify_owner_request(outgoing["text"], owner_req_id)
                        if not was_notified and owner_reply_file:
                            log(
                                "owner reply pending",
                                f"append to {owner_reply_file}: {owner_req_id}<TAB>reply",
                            )
                        owner_reply = wait_owner_reply(
                            owner_req_id=owner_req_id,
                            timeout_seconds=args.owner_wait_timeout_seconds,
                            owner_reply_file=owner_reply_file,
                            owner_reply_fetcher=fetch_owner_reply,
                            poll_seconds=args.owner_reply_poll_seconds,
                            fail_fast_fetch_errors=True,
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
                            raw = runner.ask_json(room_id, participant_name, oprompt)
                            owner_payload = normalize_model_message(raw, fallback_intent="OWNER_REPLY")
                            owner_payload["intent"] = "OWNER_REPLY"
                            owner_payload.setdefault("meta", {})
                            owner_payload["meta"]["owner_req_id"] = owner_req_id
                            owner_payload["expect_reply"] = True
                            # OWNER_REPLY resumes from owner wait; it should not reuse
                            # the original peer relay id or room-level reply dedup will
                            # treat it as a duplicate of the earlier ASK_OWNER send.
                            send_message(owner_payload, "owner_reply", in_reply_to_event_id=None)
                        else:
                            note_payload = {
                                "intent": "NOTE",
                                "text": "Owner did not reply in time; continuing without owner input.",
                                "fills": {},
                                "facts": [],
                                "questions": [],
                                "expect_reply": False,
                                "meta": {"owner_req_id": owner_req_id, "timeout": True},
                            }
                            send_message(note_payload, "owner_timeout", in_reply_to_event_id=None)
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
                    else:
                        send_message(outgoing, "relay", in_reply_to_event_id=relay_event_id)

                    room = http_json("GET", f"{base}/rooms/{room_id}", token=token)["room"]
                    if room["status"] != "active":
                        log("room ended after send", f"status={room['status']}", f"reason={room['stop_reason']}")
                        break

            state.save(logger=lambda msg: log(msg))
            if not new_relays:
                state.set_health(status="idle", recent_note="poll_idle")
                state.save(logger=lambda msg: log(msg))
                try:
                    renew_runner_claim(phase="event_polling", phase_detail="poll_idle")
                except Exception as exc:  # noqa: BLE001
                    log("runner_idle_sync_failed", short(str(exc), 220))
            time.sleep(args.poll_seconds)

    finally:
        state.set_pending_owner_request(None)
        if args.print_result:
            try:
                result = http_json("GET", f"{base}/rooms/{room_id}/result", token=token)["result"]
                log("result_summary", result.get("summary"))
            except Exception as exc:  # noqa: BLE001
                log("result_error", exc)
        effective_last_error = shutdown_last_error or state.health.last_error or ""
        state.set_health(status="exited", last_error=effective_last_error, recent_note=shutdown_note)
        state.save(logger=lambda msg: log(msg))
        try:
            if state.runner_id:
                runner_release(
                    base_url=base,
                    room_id=room_id,
                    token=token,
                    runner_id=state.runner_id,
                    attempt_id=state.attempt_id,
                    status="exited",
                    reason=shutdown_reason,
                    last_error=effective_last_error or None,
                )
        except Exception as exc:  # noqa: BLE001
            log("runner_release_error", short(str(exc), 220))
        try:
            leave = http_json(
                "POST",
                f"{base}/rooms/{room_id}/leave",
                token=token,
                payload={"reason": shutdown_reason},
            )
            log("left", f"was_online={leave.get('was_online')}")
        except Exception as exc:  # noqa: BLE001
            log("leave_error", exc)


if __name__ == "__main__":
    main()
