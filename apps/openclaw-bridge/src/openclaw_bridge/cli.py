from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

import httpx


def parse_join_url(url: str) -> dict[str, str]:
    """Parse a join URL into {base_url, room_id, token}.
    
    Supported formats:
      http://host:port/join/room_abc?token=inv_...
      http://host:port/rooms/room_abc/join_info?token=inv_...
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else url.split('/join/')[0]
    
    path_parts = [p for p in parsed.path.split('/') if p]
    room_id = ''
    if 'join' in path_parts:
        idx = path_parts.index('join')
        if idx + 1 < len(path_parts):
            room_id = path_parts[idx + 1]
    elif 'rooms' in path_parts:
        idx = path_parts.index('rooms')
        if idx + 1 < len(path_parts):
            room_id = path_parts[idx + 1]
    
    params = parse_qs(parsed.query)
    token = params.get('token', [''])[0]
    
    if not room_id or not token:
        raise ValueError(f"Cannot parse join URL: {url}  (need /join/<room_id>?token=<token>)")
    
    return {'base_url': base, 'room_id': room_id, 'token': token}


def log(*parts: object) -> None:
    print("[openclaw-bridge]", *parts, flush=True)


def short(s: str, n: int = 220) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "..."


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


def http_json(
    method: str,
    url: str,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
    retries: int = 4,
) -> dict[str, Any]:
    headers = {}
    if token:
        headers["X-Invite-Token"] = token
    retryable = (httpx.TransportError, httpx.TimeoutException)
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                resp = client.request(method, url, headers=headers, json=payload)
        except retryable:
            if attempt >= retries - 1:
                raise
            time.sleep(min(2.0, 0.25 * (2**attempt)))
            continue

        if resp.status_code >= 500 and attempt < retries - 1:
            time.sleep(min(2.0, 0.25 * (2**attempt)))
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"http {method} {url} failed status={resp.status_code} body={short(resp.text, 500)}")
        return resp.json()
    raise RuntimeError(f"http {method} {url} failed after {retries} retries")


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

    def ask_json(self, room_id: str, participant_name: str, prompt: str) -> dict[str, Any]:
        session_id = self.session_id_for(room_id, participant_name)
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
                session_id,
                "--message",
                prompt,
                "--timeout",
                str(self.timeout_seconds),
                "--thinking",
                self.thinking,
            ]
        )
        log("calling openclaw", f"agent={self.agent_id}", f"session={session_id}")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = short(proc.stderr.strip() or "(no stderr)", 1200)
            stdout = short(proc.stdout.strip() or "(no stdout)", 1200)
            raise RuntimeError(f"openclaw failed rc={proc.returncode} stdout={stdout} stderr={stderr}")
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"openclaw returned non-json stdout: {short(proc.stdout, 800)}") from exc

        payloads = parsed.get("payloads") or []
        texts = [p.get("text", "") for p in payloads if isinstance(p, dict)]
        text = "\n".join([t for t in texts if t]).strip()
        if not text:
            raise RuntimeError("openclaw returned no text payload")
        log("openclaw text", short(text, 300))
        return extract_first_json_object(text)


def room_prompt(
    *,
    role: str,
    room: dict[str, Any],
    self_name: str,
    latest_event: dict[str, Any] | None,
    has_started: bool,
) -> str:
    required_fields = room.get("required_fields") or []
    fields = room.get("fields") or {}
    field_values = {k: v.get("value") if isinstance(v, dict) else v for k, v in fields.items()}
    latest_msg = None
    if latest_event and latest_event.get("type") == "relay":
        latest_msg = (latest_event.get("payload") or {}).get("message") or {}

    role_hint = {
        "initiator": (
            "You are the initiating product-side agent. Ask concise questions to collect missing required fields. "
            "If enough information is available, send DONE."
        ),
        "responder": (
            "You are the responding partner-side agent. Answer directly and fill required fields when known."
        ),
    }.get(role, "You are a participant in this room. Respond helpfully and briefly.")

    starter = ""
    if role == "initiator" and not has_started:
        starter = (
            "This is room start. Initiate with ASK and request missing required fields."
        )

    incoming = "No incoming relay message yet."
    if latest_msg:
        incoming = (
            "Incoming relay message:\n"
            f"- from: {latest_msg.get('sender') or (latest_event.get('payload') or {}).get('from')}\n"
            f"- intent: {latest_msg.get('intent')}\n"
            f"- text: {latest_msg.get('text')}\n"
            f"- fills: {json.dumps(latest_msg.get('fills') or {}, ensure_ascii=False)}"
        )

    return (
        "You are acting as an OpenClaw participant in a machine-to-machine room.\n"
        "Return ONLY a single JSON object and nothing else.\n\n"
        f"Participant: {self_name}\n"
        f"Role: {role}\n"
        f"Role guidance: {role_hint}\n\n"
        f"Room topic: {room.get('topic')}\n"
        f"Room goal: {room.get('goal')}\n"
        f"Required fields: {json.dumps(required_fields, ensure_ascii=False)}\n"
        f"Known fields: {json.dumps(field_values, ensure_ascii=False)}\n"
        f"Room status: {room.get('status')} stop_reason={room.get('stop_reason')}\n\n"
        f"{incoming}\n\n"
        f"{starter}\n\n"
        "Output schema (all keys required):\n"
        "{"
        '"intent":"ASK|ANSWER|NOTE|DONE|ASK_OWNER|OWNER_REPLY",'
        '"text":"short message",'
        '"fills":{"optional_field":"value"},'
        '"facts":["optional fact"],'
        '"questions":["optional question"],'
        '"expect_reply":true,'
        '"meta":{}'
        "}\n\n"
        "Rules:\n"
        "- Keep text under 200 words.\n"
        "- Use fills for required fields you know.\n"
        "- If blocked on owner-only info, use ASK_OWNER and expect_reply=false.\n"
        "- If no further reply needed, use DONE and expect_reply=false.\n"
    )


def owner_reply_prompt(*, room: dict[str, Any], self_name: str, role: str, owner_req_id: str, owner_text: str) -> str:
    required_fields = room.get("required_fields") or []
    fields = room.get("fields") or {}
    field_values = {k: v.get("value") if isinstance(v, dict) else v for k, v in fields.items()}

    role_hint = {
        "initiator": "You are the product-side agent. Convert owner input into structured fills if possible.",
        "responder": "You are the partner-side agent. Convert owner input into structured fills if possible.",
    }.get(role, "You are a participant. Convert owner input into structured fills if possible.")

    return (
        "You are acting as an OpenClaw participant in a machine-to-machine room.\n"
        "Return ONLY a single JSON object and nothing else.\n\n"
        f"Participant: {self_name}\n"
        f"Role: {role}\n"
        f"Role guidance: {role_hint}\n\n"
        f"Room topic: {room.get('topic')}\n"
        f"Room goal: {room.get('goal')}\n"
        f"Required fields: {json.dumps(required_fields, ensure_ascii=False)}\n"
        f"Known fields: {json.dumps(field_values, ensure_ascii=False)}\n"
        f"Room status: {room.get('status')} stop_reason={room.get('stop_reason')}\n\n"
        "Owner replied out-of-band. Convert it into a clean OWNER_REPLY message.\n"
        f"- owner_req_id: {owner_req_id}\n"
        f"- owner_text: {owner_text}\n\n"
        "Output schema (all keys required):\n"
        "{"
        '"intent":"OWNER_REPLY",'
        '"text":"short message",'
        '"fills":{"optional_field":"value"},'
        '"facts":["optional fact"],'
        '"questions":["optional question"],'
        '"expect_reply":true,'
        '"meta":{}'
        "}\n\n"
        "Rules:\n"
        "- Keep text under 120 words.\n"
        "- Use fills for required fields when possible.\n"
        "- Always set intent=OWNER_REPLY.\n"
        "- Set meta.owner_req_id to the provided value.\n"
    )


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

    expect_reply = bool(raw.get("expect_reply", True))
    if intent in {"DONE", "ASK_OWNER"} and "expect_reply" not in raw:
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


def find_new_relay_events(events: list[dict[str, Any]], seen: set[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for evt in events:
        eid = int(evt.get("id", 0))
        if eid in seen:
            continue
        seen.add(eid)
        if evt.get("type") == "relay":
            out.append(evt)
    return out


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
    parser.add_argument("--max-seconds", type=int, default=480)
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

    runner = OpenClawRunner(
        agent_id=args.agent_id,
        profile=args.profile,
        dev=args.dev,
        timeout_seconds=args.openclaw_timeout,
        thinking=args.thinking,
    )

    started_at = time.time()
    cursor = 0
    seen_event_ids: set[int] = set()
    started_message_sent = False
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
    participant_name = str(join_resp["participant"])
    room = join_resp["room"]

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

    log("joined", f"participant={participant_name}", f"room={room['id']}", f"status={room['status']}")

    def send_message(payload: dict[str, Any], why: str) -> dict[str, Any]:
        nonlocal started_message_sent
        payload.setdefault("meta", {})
        if isinstance(payload.get("meta"), dict):
            payload["meta"].setdefault("preflight", preflight_meta)
        log("send", f"why={why}", payload["intent"], short(payload["text"], 180))
        resp = http_json(
            "POST",
            f"{base}/rooms/{room_id}/messages",
            token=token,
            payload=payload,
        )
        started_message_sent = True
        trigger = (((resp or {}).get("host_decision") or {}).get("trigger"))
        if trigger:
            log("host_trigger", trigger)
        return resp

    def generate_model_reply(latest_event: dict[str, Any] | None, room_snapshot: dict[str, Any]) -> dict[str, Any]:
        prompt = room_prompt(
            role=role,
            room=room_snapshot,
            self_name=participant_name,
            latest_event=latest_event,
            has_started=started_message_sent,
        )
        raw = runner.ask_json(room_id, participant_name, prompt)
        fallback = "ASK" if role == "initiator" and not started_message_sent else "ANSWER"
        return normalize_model_message(raw, fallback_intent=fallback)

    try:
        while True:
            if time.time() - started_at > args.max_seconds:
                log("max-seconds reached")
                break

            batch = http_json(
                "GET",
                f"{base}/rooms/{room_id}/events?after={cursor}&limit=200",
                token=token,
            )
            room = batch["room"]
            events = batch["events"]
            cursor = int(batch["next_cursor"])
            new_relays = find_new_relay_events(events, seen_event_ids)

            if room["status"] != "active":
                log("room ended", f"status={room['status']}", f"reason={room['stop_reason']}")
                break

            if role == "initiator" and args.start and not started_message_sent and int(room.get("turn_count", 0)) == 0:
                outgoing = generate_model_reply(None, room)
                send_message(outgoing, "room_start")
                time.sleep(args.poll_seconds)
                continue

            if new_relays:
                for evt in new_relays:
                    outgoing = generate_model_reply(evt, room)

                    if outgoing["intent"] == "ASK_OWNER":
                        outgoing["expect_reply"] = False
                        owner_req_id = f"oreq_{uuid.uuid4().hex[:12]}"
                        outgoing.setdefault("meta", {})
                        outgoing["meta"]["owner_req_id"] = owner_req_id
                        send_message(outgoing, "relay_ask_owner")
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
                        )
                        if owner_reply:
                            oprompt = owner_reply_prompt(
                                room=room,
                                self_name=participant_name,
                                role=role,
                                owner_req_id=owner_req_id,
                                owner_text=owner_reply,
                            )
                            raw = runner.ask_json(room_id, participant_name, oprompt)
                            owner_payload = normalize_model_message(raw, fallback_intent="OWNER_REPLY")
                            owner_payload["intent"] = "OWNER_REPLY"
                            owner_payload.setdefault("meta", {})
                            owner_payload["meta"]["owner_req_id"] = owner_req_id
                            owner_payload["expect_reply"] = True
                            send_message(owner_payload, "owner_reply")
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
                            send_message(note_payload, "owner_timeout")
                    else:
                        send_message(outgoing, "relay")

                    room = http_json("GET", f"{base}/rooms/{room_id}", token=token)["room"]
                    if room["status"] != "active":
                        log("room ended after send", f"status={room['status']}", f"reason={room['stop_reason']}")
                        break

            time.sleep(args.poll_seconds)

    finally:
        if args.print_result:
            try:
                result = http_json("GET", f"{base}/rooms/{room_id}/result", token=token)["result"]
                log("result_summary", result.get("summary"))
            except Exception as exc:  # noqa: BLE001
                log("result_error", exc)
        try:
            leave = http_json(
                "POST",
                f"{base}/rooms/{room_id}/leave",
                token=token,
                payload={"reason": "client_exit"},
            )
            log("left", f"was_online={leave.get('was_online')}")
        except Exception as exc:  # noqa: BLE001
            log("leave_error", exc)


if __name__ == "__main__":
    main()
