#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from state_paths import resolve_state_root


DEFAULT_API_BASE = "https://api.clawroom.cc"
_SSL_CONTEXT: ssl.SSLContext | None = None
CONTROL_FILL_VALUES = {"ASK_OWNER", "OWNER_REPLY", "DONE", "ANSWER", "ASK", "NOTE"}


def spool_root() -> Path:
    return resolve_state_root() / "rooms"


def participant_key(participant_name: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in participant_name.strip().lower())
    return text or "participant"


def room_root_dir(room_id: str) -> Path:
    return spool_root() / room_id


def room_dir(room_id: str, participant_name: str) -> Path:
    return room_root_dir(room_id) / participant_key(participant_name)


def owner_context_path(room_id: str, participant_name: str) -> Path:
    return room_dir(room_id, participant_name) / "owner_context.json"


def pending_question_path(room_id: str, participant_name: str) -> Path:
    return room_dir(room_id, participant_name) / "pending_question.json"


def owner_reply_path(room_id: str, participant_name: str) -> Path:
    return room_dir(room_id, participant_name) / "owner_reply.json"


def poller_pid_path(room_id: str, participant_name: str) -> Path:
    return room_dir(room_id, participant_name) / "poller.pid"


def joined_state_path(room_id: str, participant_name: str) -> Path:
    return room_dir(room_id, participant_name) / "joined.json"


def final_result_path(room_id: str, participant_name: str) -> Path:
    return room_dir(room_id, participant_name) / "final_result.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def request_json(method: str, url: str, *, headers: dict[str, str] | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    final_headers = dict(headers or {})
    final_headers.setdefault("Accept", "application/json")
    final_headers.setdefault("User-Agent", "ClawRoomMiniBridge/1.2 (+OpenClaw)")
    if payload is not None:
        final_headers.setdefault("Content-Type", "application/json")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, method=method, headers=final_headers, data=data)
    try:
        with urllib.request.urlopen(request, timeout=20, context=ssl_context()) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} -> {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} -> {exc.reason}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {url} -> invalid json: {body[:400]}") from exc


def ssl_context() -> ssl.SSLContext:
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT
    try:
        import certifi  # type: ignore
    except Exception:
        _SSL_CONTEXT = ssl.create_default_context()
    else:
        _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    return _SSL_CONTEXT


def parse_join_url(join_url: str) -> tuple[str, str, str]:
    parsed = urllib.parse.urlparse(join_url)
    api_base = f"{parsed.scheme}://{parsed.netloc}"
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] != "join":
        raise ValueError("join_url must look like https://api.clawroom.cc/join/{room_id}?token=...")
    room_id = parts[1]
    invite_token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0].strip()
    if not invite_token:
        raise ValueError("join_url is missing token")
    return api_base, room_id, invite_token


def load_owner_context(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("owner context must be a JSON object")
    for key in ("owner_name", "owner_role", "task_context", "language"):
        if not str(raw.get(key) or "").strip():
            raise ValueError(f"owner context missing {key}")
    confirmed_facts = raw.get("confirmed_facts")
    do_not_share = raw.get("do_not_share")
    if not isinstance(confirmed_facts, list) or not all(isinstance(item, str) for item in confirmed_facts):
        raise ValueError("owner context confirmed_facts must be a string list")
    if not isinstance(do_not_share, list) or not all(isinstance(item, str) for item in do_not_share):
        raise ValueError("owner context do_not_share must be a string list")
    return {
        "owner_name": str(raw["owner_name"]).strip(),
        "owner_role": str(raw["owner_role"]).strip(),
        "confirmed_facts": [str(item).strip() for item in confirmed_facts if str(item).strip()],
        "do_not_share": [str(item).strip() for item in do_not_share if str(item).strip()],
        "task_context": str(raw["task_context"]).strip(),
        "language": str(raw["language"]).strip() or "en",
    }


def build_context_envelope(owner_context: dict[str, Any]) -> dict[str, Any]:
    facts = [fact for fact in owner_context.get("confirmed_facts", []) if fact not in set(owner_context.get("do_not_share", []))]
    summary = (
        f"Owner: {owner_context['owner_name']} ({owner_context['owner_role']}). "
        f"Task context: {owner_context['task_context']}. "
        f"Confirmed facts: {'; '.join(facts[:8]) if facts else 'none provided'}."
    )
    return {"summary": summary, "refs": []}


def extract_text_from_openclaw_response(response: dict[str, Any]) -> str:
    result = response.get("result") or {}
    payloads = result.get("payloads") or []
    for item in payloads:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            return str(item["text"])
    raise RuntimeError("OpenClaw response did not contain a text payload")


def extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : index + 1])
        start = text.find("{", start + 1)
    raise RuntimeError("OpenClaw response did not contain a JSON object")


def call_openclaw_json(*, agent_id: str, session_id: str, prompt: str, timeout_seconds: int, thinking: str) -> dict[str, Any]:
    command = [
        "openclaw",
        "agent",
        "--json",
        "--agent",
        agent_id,
        "--session-id",
        session_id,
        "--message",
        prompt,
        "--timeout",
        str(timeout_seconds),
        "--thinking",
        thinking,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds + 20, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "openclaw agent failed").strip())
    return json.loads(result.stdout)


def deliver_owner_message(
    *,
    agent_id: str,
    owner_session_id: str,
    timeout_seconds: int,
    thinking: str,
    message: str,
    reply_channel: str | None,
    reply_to: str | None,
    reply_account: str | None,
) -> None:
    prompt = (
        "Send this update to the owner in their language. Keep it short, natural, and non-technical.\n\n"
        f"{message}"
    )
    command = [
        "openclaw",
        "agent",
        "--agent",
        agent_id,
        "--session-id",
        owner_session_id,
        "--deliver",
        "--message",
        prompt,
        "--timeout",
        str(timeout_seconds),
        "--thinking",
        thinking,
    ]
    if reply_channel:
        command.extend(["--reply-channel", reply_channel])
    if reply_to:
        command.extend(["--reply-to", reply_to])
    if reply_account:
        command.extend(["--reply-account", reply_account])
    subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds + 20, check=False)


def normalize_model_message(raw: dict[str, Any], *, fallback_intent: str = "ANSWER") -> dict[str, Any]:
    text = str(raw.get("text") or "").strip()
    if not text:
        raise RuntimeError("model output missing text")
    intent = str(raw.get("intent") or fallback_intent).strip().upper() or fallback_intent
    fills = raw.get("fills")
    if not isinstance(fills, dict):
        fills = {}
    expect_reply = raw.get("expect_reply")
    if not isinstance(expect_reply, bool):
        expect_reply = intent not in {"DONE", "NOTE"}
    return {
        "intent": intent,
        "text": text,
        "fills": {str(key): str(value) for key, value in fills.items() if str(key).strip() and str(value).strip()},
        "expect_reply": expect_reply,
        "facts": [],
        "questions": [],
        "meta": {},
    }


def sanitize_message_for_room(message: dict[str, Any], room: dict[str, Any]) -> dict[str, Any]:
    allowed = {str(item).strip() for item in (room.get("required_fields") or []) if str(item).strip()}
    sanitized_fills: dict[str, str] = {}
    for key, value in (message.get("fills") or {}).items():
        fill_key = str(key).strip()
        fill_value = str(value).strip()
        if not fill_key or not fill_value:
            continue
        if allowed and fill_key not in allowed:
            continue
        if fill_value.upper() in CONTROL_FILL_VALUES:
            continue
        sanitized_fills[fill_key] = fill_value
    message["fills"] = sanitized_fills
    return message


def relay_requires_reply(event: dict[str, Any]) -> bool:
    message = (event.get("payload") or {}).get("message") or {}
    intent = str(message.get("intent") or "").upper().strip()
    expect_reply = bool(message.get("expect_reply", True))
    return intent == "DONE" or expect_reply


def build_reply_prompt(
    *,
    role: str,
    room: dict[str, Any],
    latest_event: dict[str, Any] | None,
    owner_context: dict[str, Any],
    has_started: bool,
) -> str:
    required_fields = room.get("required_fields") or []
    fields = room.get("fields") or {}
    latest_message = {}
    if latest_event and latest_event.get("type") == "relay":
        latest_message = (latest_event.get("payload") or {}).get("message") or {}
    known_fields = {str(key): (value.get("value") if isinstance(value, dict) else value) for key, value in fields.items()}
    incoming = "No incoming room message yet."
    if latest_message:
        incoming = (
            f"Latest room message from {latest_message.get('sender') or 'peer'} "
            f"(intent={latest_message.get('intent') or 'ANSWER'}): {latest_message.get('text') or ''}"
        )
    confirmed_facts = owner_context.get("confirmed_facts") or []
    do_not_share = owner_context.get("do_not_share") or []
    starter = ""
    if role == "host" and not has_started:
        starter = "Both sides are in the room and there is no substantive turn yet. Send the opening room message now."
    return (
        "You are composing exactly one in-room ClawRoom message for another OpenClaw.\n"
        "Return exactly one JSON object and nothing else.\n\n"
        f"Topic: {room.get('topic') or ''}\n"
        f"Goal: {room.get('goal') or ''}\n"
        f"Required fields: {json.dumps(required_fields, ensure_ascii=False)}\n"
        f"Known field values: {json.dumps(known_fields, ensure_ascii=False)}\n"
        f"Owner name: {owner_context['owner_name']}\n"
        f"Owner role: {owner_context['owner_role']}\n"
        f"Task context: {owner_context['task_context']}\n"
        f"Confirmed facts you may use: {json.dumps(confirmed_facts, ensure_ascii=False)}\n"
        f"Do not share: {json.dumps(do_not_share, ensure_ascii=False)}\n"
        f"{incoming}\n"
        f"{starter}\n\n"
        "Rules:\n"
        "- Use only the confirmed facts above as factual owner information.\n"
        "- If a required fact is missing, use ASK_OWNER instead of inventing it.\n"
        "- Never mention room mechanics, tokens, pollers, protocol, fields, relay, or statuses.\n"
        "- Keep the message natural, direct, and in the owner's language.\n"
        "- Keep it to 1-4 short sentences.\n"
        "- Ask at most one direct question.\n"
        "- Use fills whenever you can provide real content.\n"
        "- If this is the host opening turn, give useful owner context and invite a reply.\n"
        "- Use DONE only when the room is genuinely ready to close.\n\n"
        "Output schema:\n"
        '{"intent":"ANSWER","text":"...","fills":{"key":"value"},"expect_reply":true}'
    )


def build_owner_reply_prompt(
    *,
    room: dict[str, Any],
    owner_context: dict[str, Any],
    owner_question: str,
    owner_reply: str,
) -> str:
    required_fields = room.get("required_fields") or []
    fields = room.get("fields") or {}
    known_fields = {str(key): (value.get("value") if isinstance(value, dict) else value) for key, value in fields.items()}
    return (
        "You are resuming a ClawRoom after asking your owner a blocking question.\n"
        "Return exactly one JSON object and nothing else.\n\n"
        f"Topic: {room.get('topic') or ''}\n"
        f"Goal: {room.get('goal') or ''}\n"
        f"Required fields: {json.dumps(required_fields, ensure_ascii=False)}\n"
        f"Known field values: {json.dumps(known_fields, ensure_ascii=False)}\n"
        f"Owner context: {json.dumps(owner_context, ensure_ascii=False)}\n"
        f"Question you asked the owner: {owner_question}\n"
        f"Owner reply: {owner_reply}\n\n"
        "Rules:\n"
        "- Use OWNER_REPLY.\n"
        "- Convert the owner's answer into one natural in-room message.\n"
        "- Use fills if the owner's answer directly resolves a required field.\n"
        "- Do not mention that the answer came from an out-of-band owner check.\n"
        '- Output schema: {"intent":"OWNER_REPLY","text":"...","fills":{"key":"value"},"expect_reply":true}'
    )


class Poller:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.api_base = args.api_base.rstrip("/")
        self.room_id = str(args.room_id or "").strip()
        self.participant_token = str(args.participant_token or "").strip()
        self.join_url = str(args.join_url or "").strip()
        if self.join_url:
            parsed_api, parsed_room_id, invite_token = parse_join_url(self.join_url)
            if not self.room_id:
                self.room_id = parsed_room_id
            self.api_base = parsed_api
            self.invite_token = invite_token
        else:
            self.invite_token = ""
        if not self.room_id:
            raise SystemExit("room_id or join_url is required")
        if not self.join_url and not self.participant_token:
            raise SystemExit("participant_token is required when join_url is not provided")
        self.participant_name = str(args.participant_name or "").strip() or (
            "host_openclaw" if args.role == "host" else "counterpart_openclaw"
        )
        self.room_dir = room_dir(self.room_id, self.participant_name)
        self.room_dir.mkdir(parents=True, exist_ok=True)
        self.owner_context = load_owner_context(Path(args.owner_context_file).expanduser())
        write_json_atomic(owner_context_path(self.room_id, self.participant_name), self.owner_context)
        self.session_id = str(args.session_id or f"clawroom-{self.room_id}")
        self.cursor = int(args.after or 0)
        self.has_started = False
        self.should_stop = False
        self.last_heartbeat_at = 0.0
        self.last_observation = ""

    def acquire_pid_lock(self) -> None:
        pid_file = poller_pid_path(self.room_id, self.participant_name)
        if pid_file.exists():
            previous = pid_file.read_text(encoding="utf-8").strip()
            if previous.isdigit():
                try:
                    os.kill(int(previous), 0)
                except OSError:
                    pass
                else:
                    raise SystemExit(f"room poller already running for {self.room_id} (pid {previous})")
        write_text_atomic(pid_file, f"{os.getpid()}\n")

    def log(self, message: str) -> None:
        print(f"[clawroom:{self.room_id}:{self.participant_name}] {message}", flush=True)

    def cleanup_pid_lock(self) -> None:
        pid_file = poller_pid_path(self.room_id, self.participant_name)
        try:
            if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass

    def ensure_joined(self) -> dict[str, Any]:
        if self.participant_token:
            room_response = request_json(
                "GET",
                f"{self.api_base}/rooms/{self.room_id}",
                headers={"X-Participant-Token": self.participant_token},
            )
            joined = {
                "participant": self.participant_name,
                "participant_token": self.participant_token,
                "room": room_response.get("room") or {},
            }
            write_json_atomic(joined_state_path(self.room_id, self.participant_name), joined)
            self.log("resumed with existing participant token")
            return joined

        join_response = request_json(
            "POST",
            f"{self.api_base}/rooms/{self.room_id}/join",
            headers={"X-Invite-Token": self.invite_token},
            payload={
                "client_name": self.args.client_name,
                "context_envelope": build_context_envelope(self.owner_context),
            },
        )
        self.participant_token = str(join_response.get("participant_token") or "").strip()
        if not self.participant_token:
            raise RuntimeError("join response missing participant_token")
        participant_name = str(join_response.get("participant") or "").strip()
        if participant_name:
            self.participant_name = participant_name
            self.room_dir = room_dir(self.room_id, self.participant_name)
            self.room_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(owner_context_path(self.room_id, self.participant_name), self.owner_context)
        write_json_atomic(joined_state_path(self.room_id, self.participant_name), join_response)
        self.log("joined room from invite")
        return join_response

    def poll_events(self) -> dict[str, Any]:
        response = request_json(
            "GET",
            f"{self.api_base}/rooms/{self.room_id}/events?after={self.cursor}&limit=200",
            headers={"X-Participant-Token": self.participant_token},
        )
        next_cursor = response.get("next_cursor")
        if isinstance(next_cursor, int):
            self.cursor = max(self.cursor, next_cursor)
        elif isinstance(next_cursor, str) and next_cursor.isdigit():
            self.cursor = max(self.cursor, int(next_cursor))
        return response

    def fetch_room(self) -> dict[str, Any]:
        response = request_json(
            "GET",
            f"{self.api_base}/rooms/{self.room_id}",
            headers={"X-Participant-Token": self.participant_token},
        )
        return response.get("room") or {}

    def heartbeat(self) -> dict[str, Any]:
        response = request_json(
            "POST",
            f"{self.api_base}/rooms/{self.room_id}/heartbeat",
            headers={"X-Participant-Token": self.participant_token},
        )
        self.last_heartbeat_at = time.time()
        return response

    def maybe_heartbeat(self, *, force: bool = False) -> None:
        interval = max(5.0, float(self.args.heartbeat_seconds))
        if not force and (time.time() - self.last_heartbeat_at) < interval:
            return
        try:
            self.heartbeat()
        except Exception:
            # Keep the poller alive; the next room/event request will surface a hard failure if the room is gone.
            return

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = request_json(
            "POST",
            f"{self.api_base}/rooms/{self.room_id}/messages",
            headers={"X-Participant-Token": self.participant_token},
            payload=payload,
        )
        self.log(f"sent {payload.get('intent') or 'ANSWER'} message")
        if payload.get("intent") not in {"NOTE", "DONE"}:
            self.has_started = True
        self.maybe_heartbeat(force=True)
        return response

    def notify_owner_question(self, question_text: str) -> None:
        message = (
            f"The current collaboration room needs one owner answer before it can continue.\n"
            f"Question: {question_text}\n"
            f"When the owner replies here, record that answer for room {self.room_id}."
        )
        deliver_owner_message(
            agent_id=self.args.agent_id,
            owner_session_id=self.args.owner_session_id,
            timeout_seconds=self.args.openclaw_timeout,
            thinking=self.args.thinking,
            message=message,
            reply_channel=self.args.reply_channel,
            reply_to=self.args.reply_to,
            reply_account=self.args.reply_account,
        )

    def notify_owner_result(self, room: dict[str, Any], result: dict[str, Any]) -> None:
        fields = result.get("fields") or room.get("fields") or {}
        field_lines = []
        for key, value in fields.items():
            current = value.get("value") if isinstance(value, dict) else value
            if str(current or "").strip():
                field_lines.append(f"- {key}: {current}")
        body = "\n".join(field_lines) or "- No final field values were recorded."
        message = (
            f"The collaboration room has finished.\n"
            f"Topic: {room.get('topic') or ''}\n"
            f"Goal: {room.get('goal') or ''}\n"
            f"Outcome:\n{body}"
        )
        deliver_owner_message(
            agent_id=self.args.agent_id,
            owner_session_id=self.args.owner_session_id,
            timeout_seconds=self.args.openclaw_timeout,
            thinking=self.args.thinking,
            message=message,
            reply_channel=self.args.reply_channel,
            reply_to=self.args.reply_to,
            reply_account=self.args.reply_account,
        )

    def read_owner_reply(self, request_id: str) -> str | None:
        path = owner_reply_path(self.room_id, self.participant_name)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(payload.get("request_id") or "").strip() != request_id:
            return None
        reply = str(payload.get("reply") or "").strip()
        if not reply:
            return None
        path.unlink(missing_ok=True)
        return reply

    def wait_for_owner_reply(self, request_id: str, question_text: str) -> str | None:
        del question_text
        deadline = time.time() + max(30, self.args.owner_wait_timeout)
        while time.time() < deadline and not self.should_stop:
            self.maybe_heartbeat()
            reply = self.read_owner_reply(request_id)
            if reply:
                pending_question_path(self.room_id, self.participant_name).unlink(missing_ok=True)
                return reply
            room = self.fetch_room()
            if str(room.get("status") or "").lower() != "active":
                pending_question_path(self.room_id, self.participant_name).unlink(missing_ok=True)
                return None
            time.sleep(max(0.5, self.args.poll_seconds))
        return None

    def generate_model_message(self, *, room: dict[str, Any], latest_event: dict[str, Any] | None) -> dict[str, Any]:
        prompt = build_reply_prompt(
            role=self.args.role,
            room=room,
            latest_event=latest_event,
            owner_context=self.owner_context,
            has_started=self.has_started,
        )
        response = call_openclaw_json(
            agent_id=self.args.agent_id,
            session_id=self.session_id,
            prompt=prompt,
            timeout_seconds=self.args.openclaw_timeout,
            thinking=self.args.thinking,
        )
        text = extract_text_from_openclaw_response(response)
        return sanitize_message_for_room(normalize_model_message(extract_json_object(text), fallback_intent="ANSWER"), room)

    def generate_owner_reply_message(self, *, room: dict[str, Any], question_text: str, owner_reply: str) -> dict[str, Any]:
        prompt = build_owner_reply_prompt(
            room=room,
            owner_context=self.owner_context,
            owner_question=question_text,
            owner_reply=owner_reply,
        )
        response = call_openclaw_json(
            agent_id=self.args.agent_id,
            session_id=self.session_id,
            prompt=prompt,
            timeout_seconds=self.args.openclaw_timeout,
            thinking=self.args.thinking,
        )
        text = extract_text_from_openclaw_response(response)
        message = normalize_model_message(extract_json_object(text), fallback_intent="OWNER_REPLY")
        message["intent"] = "OWNER_REPLY"
        return sanitize_message_for_room(message, room)

    def handle_owner_wait(self, *, room: dict[str, Any], latest_event: dict[str, Any]) -> None:
        payload = latest_event.get("payload") or {}
        question_text = str(payload.get("text") or "The room needs one owner answer before it can continue.").strip()
        request_id = str(payload.get("owner_req_id") or f"owner_req_{int(time.time())}").strip()
        write_json_atomic(
            pending_question_path(self.room_id, self.participant_name),
            {
                "request_id": request_id,
                "question": question_text,
                "room_id": self.room_id,
                "asked_at": int(time.time()),
            },
        )
        self.notify_owner_question(question_text)
        self.log("asked owner a blocking question")
        owner_reply = self.wait_for_owner_reply(request_id, question_text)
        if not owner_reply:
            self.log("owner reply timed out or room closed before reply")
            return
        owner_message = self.generate_owner_reply_message(room=room, question_text=question_text, owner_reply=owner_reply)
        self.send_message(owner_message)

    def run(self) -> None:
        self.acquire_pid_lock()
        try:
            joined = self.ensure_joined()
            self.maybe_heartbeat(force=True)
            room = joined.get("room") or {}
            if self.args.role == "host" and int(room.get("turn_count") or 0) > 0:
                self.has_started = True

            while not self.should_stop:
                self.maybe_heartbeat()
                batch = self.poll_events()
                room = batch.get("room") or {}
                events = [event for event in (batch.get("events") or []) if isinstance(event, dict)]
                joined_count = sum(1 for participant in (room.get("participants") or []) if participant.get("joined"))
                observation = f"status={room.get('status')} turns={room.get('turn_count')} joined={joined_count} events={len(events)}"
                if observation != self.last_observation:
                    self.log(observation)
                    self.last_observation = observation
                if str(room.get("status") or "").lower() != "active":
                    result = request_json(
                        "GET",
                        f"{self.api_base}/rooms/{self.room_id}/result",
                        headers={"X-Participant-Token": self.participant_token},
                    )
                    write_json_atomic(final_result_path(self.room_id, self.participant_name), result)
                    self.notify_owner_result(room, result.get("result") or {})
                    self.log("room closed; final result delivered to owner")
                    break

                if self.args.role == "host" and not self.has_started:
                    if joined_count >= 2 and int(room.get("turn_count") or 0) == 0:
                        self.log("both sides joined; generating host opening message")
                        opening = self.generate_model_message(room=room, latest_event=None)
                        if opening.get("intent") in {"NOTE", "DONE", "OWNER_REPLY"}:
                            opening["intent"] = "ANSWER"
                            opening["expect_reply"] = True
                        self.send_message(opening)
                        time.sleep(max(0.5, self.args.poll_seconds))
                        continue

                handled = False
                for event in events:
                    event_type = str(event.get("type") or "")
                    payload = event.get("payload") or {}
                    participant = str(payload.get("participant") or "")
                    if event_type == "owner_wait" and participant == str(joined.get("participant") or ""):
                        self.handle_owner_wait(room=room, latest_event=event)
                        handled = True
                        break
                    if event_type != "relay":
                        continue
                    message = payload.get("message") or {}
                    if str(message.get("sender") or "") == str(joined.get("participant") or ""):
                        continue
                    if not relay_requires_reply(event):
                        continue
                    outgoing = self.generate_model_message(room=room, latest_event=event)
                    self.send_message(outgoing)
                    handled = True
                    break

                if not handled:
                    time.sleep(max(0.5, self.args.poll_seconds))
        finally:
            self.cleanup_pid_lock()


def install_signal_handlers(poller: Poller) -> None:
    def handle_signal(_signum: int, _frame: object) -> None:
        poller.should_stop = True

    for signame in ("SIGTERM", "SIGINT", "SIGHUP"):
        signal.signal(getattr(signal, signame), handle_signal)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a minimal OpenClaw ClawRoom poller for one room participant.")
    parser.add_argument("--room-id")
    parser.add_argument("--participant-token")
    parser.add_argument("--join-url")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--owner-context-file", required=True)
    parser.add_argument("--role", choices=["host", "guest"], required=True)
    parser.add_argument("--participant-name")
    parser.add_argument("--agent-id", default="main")
    parser.add_argument("--owner-session-id", default="main")
    parser.add_argument("--session-id")
    parser.add_argument("--client-name", default="ClawRoomPoller")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--openclaw-timeout", type=int, default=90)
    parser.add_argument("--owner-wait-timeout", type=int, default=300)
    parser.add_argument("--heartbeat-seconds", type=float, default=20.0)
    parser.add_argument("--thinking", default="minimal")
    parser.add_argument("--reply-channel")
    parser.add_argument("--reply-to")
    parser.add_argument("--reply-account")
    parser.add_argument("--after", type=int, default=0)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    poller = Poller(args)
    install_signal_handlers(poller)
    poller.run()


if __name__ == "__main__":
    main()
