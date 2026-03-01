from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


def log(*parts: object) -> None:
    print("[openclaw-room-client]", *parts, flush=True)


def http_json(method: str, url: str, *, token: str | None = None, payload: dict[str, Any] | None = None) -> Any:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["X-Invite-Token"] = token
    req = urllib.request.Request(url=url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        raise RuntimeError(f"HTTP {exc.code}: {parsed}") from exc


def extract_first_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("empty model response")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found")


def short(s: str, n: int = 220) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


@dataclass(slots=True)
class OpenClawRunner:
    agent_id: str
    profile: str | None
    dev: bool
    timeout_seconds: int
    thinking: str

    def session_id_for(self, room_id: str, participant_name: str) -> str:
        seed = f"roombridge:{room_id}:{self.agent_id}:{participant_name}"
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
            raise RuntimeError(f"openclaw failed rc={proc.returncode}\nstdout={stdout}\nstderr={stderr}")
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"openclaw returned non-JSON stdout: {short(proc.stdout, 800)}") from exc

        payloads = parsed.get("payloads") or []
        texts = [p.get("text", "") for p in payloads if isinstance(p, dict)]
        text = "\n".join([t for t in texts if t]).strip()
        if not text:
            raise RuntimeError("openclaw returned no text payload")
        log("openclaw text", short(text, 300))
        result = extract_first_json_object(text)
        return result


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
            "If enough information is already available, send DONE."
        ),
        "responder": (
            "You are the responding partner-side agent. Answer directly and fill required fields when known. "
            "If you can satisfy the room goal in one reply, do so."
        ),
    }.get(role, "You are a participant in this room. Respond helpfully and briefly.")

    starter_instruction = ""
    if role == "initiator" and not has_started:
        starter_instruction = (
            "This is the start of the room. You should initiate with an ASK message that requests the missing required fields. "
            "Be brief."
        )

    incoming_block = "No incoming relay message yet.\n"
    if latest_msg:
        incoming_block = (
            "Incoming relay message:\n"
            f"- from: {latest_msg.get('sender') or (latest_event.get('payload') or {}).get('from')}\n"
            f"- intent: {latest_msg.get('intent')}\n"
            f"- text: {latest_msg.get('text')}\n"
            f"- fills: {json.dumps(latest_msg.get('fills') or {}, ensure_ascii=False)}\n"
        )

    return (
        "You are acting as an OpenClaw participant in a machine-to-machine room.\n"
        "Return ONLY a single JSON object and nothing else.\n\n"
        f"Participant name: {self_name}\n"
        f"Role: {role}\n"
        f"Role guidance: {role_hint}\n\n"
        f"Room topic: {room.get('topic')}\n"
        f"Room goal: {room.get('goal')}\n"
        f"Required fields: {json.dumps(required_fields, ensure_ascii=False)}\n"
        f"Current known fields: {json.dumps(field_values, ensure_ascii=False)}\n"
        f"Room status: {room.get('status')} stop_reason={room.get('stop_reason')}\n\n"
        f"{incoming_block}\n"
        f"{starter_instruction}\n\n"
        "Output schema (all keys required):\n"
        '{'
        '"intent":"ASK|ANSWER|DONE|NEED_HUMAN|NOTE",'
        '"text":"short message",'
        '"fills":{"optional_field":"value"},'
        '"facts":["optional new fact"],'
        '"questions":["optional question"],'
        '"wants_reply":true'
        '}\n\n'
        "Rules:\n"
        "- Keep text under 200 words.\n"
        "- Use fills for any required fields you know.\n"
        "- If blocked on missing human-only info, use NEED_HUMAN.\n"
        "- If no further reply is needed, use DONE (wants_reply can be false).\n"
    )


def normalize_model_message(raw: dict[str, Any], *, fallback_intent: str = "ANSWER") -> dict[str, Any]:
    intent = str(raw.get("intent", fallback_intent)).upper()
    if intent not in {"ASK", "ANSWER", "DONE", "NEED_HUMAN", "NOTE"}:
        intent = fallback_intent
    text = str(raw.get("text", "")).strip()
    if not text:
        text = "(no text)"
    fills_in = raw.get("fills")
    fills: dict[str, str] = {}
    if isinstance(fills_in, dict):
        for k, v in fills_in.items():
            ks = str(k).strip()
            vs = str(v).strip()
            if ks and vs:
                fills[ks] = vs
    facts_in = raw.get("facts")
    facts = [str(x).strip() for x in facts_in] if isinstance(facts_in, list) else []
    facts = [x for x in facts if x]
    qs_in = raw.get("questions")
    questions = [str(x).strip() for x in qs_in] if isinstance(qs_in, list) else []
    questions = [x for x in questions if x]
    wants_reply = bool(raw.get("wants_reply", True))
    if intent == "DONE" and "wants_reply" not in raw:
        wants_reply = False
    return {
        "intent": intent,
        "text": text,
        "fills": fills,
        "facts": facts,
        "questions": questions,
        "wants_reply": wants_reply,
    }


def participant_name_from_room(room: dict[str, Any], token_digest: str) -> str:
    # Fallback only; caller should use join response participant.
    participants = room.get("participants") or []
    if not participants:
        return "agent"
    idx = int(token_digest[:2], 16) % len(participants)
    return participants[idx]["name"] if isinstance(participants[idx], dict) else str(participants[idx])


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def main() -> None:
    p = argparse.ArgumentParser(description="OpenClaw Room Client (local OpenClaw -> RoomBridge)")
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.add_argument("--room-id", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--agent-id", required=True, help="OpenClaw agent id, e.g. main / sam")
    p.add_argument("--role", choices=["initiator", "responder"], default="responder")
    p.add_argument("--client-name", default=None)
    p.add_argument("--poll-seconds", type=float, default=1.0)
    p.add_argument("--max-seconds", type=int, default=300)
    p.add_argument("--openclaw-timeout", type=int, default=90)
    p.add_argument("--thinking", choices=["off", "minimal", "low", "medium", "high"], default="minimal")
    p.add_argument("--profile", default=None, help="Optional openclaw --profile value")
    p.add_argument("--dev", action="store_true", help="Use openclaw --dev")
    p.add_argument("--start", action="store_true", help="For initiator: send an opening message immediately")
    p.add_argument("--print-result", action="store_true", help="Print room result before exit")
    args = p.parse_args()

    base = args.base_url.rstrip("/")
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

    join_resp = http_json(
        "POST",
        f"{base}/rooms/{args.room_id}/join",
        token=args.token,
        payload={"client_name": args.client_name or f"OpenClaw({args.agent_id})"},
    )
    participant_name = str(join_resp["participant"])
    room = join_resp["room"]
    log("joined", f"participant={participant_name}", f"room={room['id']}", f"status={room['status']}")

    def generate_and_send(latest_event: dict[str, Any] | None, room_snapshot: dict[str, Any], why: str) -> None:
        nonlocal started_message_sent
        prompt = room_prompt(
            role=args.role,
            room=room_snapshot,
            self_name=participant_name,
            latest_event=latest_event,
            has_started=started_message_sent,
        )
        model_raw = runner.ask_json(args.room_id, participant_name, prompt)
        outgoing = normalize_model_message(model_raw, fallback_intent="ASK" if args.role == "initiator" and not started_message_sent else "ANSWER")
        log("send", f"why={why}", outgoing["intent"], short(outgoing["text"], 160))
        resp = http_json(
            "POST",
            f"{base}/rooms/{args.room_id}/messages",
            token=args.token,
            payload=outgoing,
        )
        started_message_sent = True
        trigger = (((resp or {}).get("host_decision") or {}).get("trigger"))
        if trigger:
            log("host_trigger", trigger)

    try:
        while True:
            if time.time() - started_at > args.max_seconds:
                log("max-seconds reached, leaving")
                break

            batch = http_json(
                "GET",
                f"{base}/rooms/{args.room_id}/events?after={cursor}&limit=200",
                token=args.token,
            )
            room = batch["room"]
            events = batch["events"]
            cursor = int(batch["next_cursor"])
            new_relays = find_new_relay_events(events, seen_event_ids)

            if room["status"] != "active":
                log("room ended", f"status={room['status']}", f"reason={room['stop_reason']}")
                break

            if args.role == "initiator" and args.start and not started_message_sent and room.get("turn_count", 0) == 0:
                generate_and_send(None, room, "room_start")
                time.sleep(args.poll_seconds)
                continue

            if new_relays:
                for evt in new_relays:
                    generate_and_send(evt, room, "relay")
                    # Refresh room quickly after each send to pick up closure
                    room = http_json("GET", f"{base}/rooms/{args.room_id}", token=args.token)["room"]
                    if room["status"] != "active":
                        log("room ended after send", f"status={room['status']}", f"reason={room['stop_reason']}")
                        break

            time.sleep(args.poll_seconds)
    finally:
        if args.print_result:
            try:
                result = http_json("GET", f"{base}/rooms/{args.room_id}/result", token=args.token)["result"]
                log("result_summary", result.get("summary"))
            except Exception as exc:
                log("result_error", exc)
        try:
            leave = http_json(
                "POST",
                f"{base}/rooms/{args.room_id}/leave",
                token=args.token,
                payload={"reason": "client_exit"},
            )
            log("left", f"was_online={leave.get('was_online')}")
        except Exception as exc:
            log("leave_error", exc)


if __name__ == "__main__":
    main()

