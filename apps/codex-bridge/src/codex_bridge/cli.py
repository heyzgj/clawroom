from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

import httpx


def log(*parts: object) -> None:
    print("[codex-bridge]", *parts, flush=True)


def short(s: str, n: int = 220) -> str:
    return s if len(s) <= n else s[: n - 1] + "..."


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
            raise RuntimeError(f"http {method} {url} failed status={resp.status_code} body={short(resp.text, 400)}")
        return resp.json()
    raise RuntimeError(f"http {method} {url} failed after {retries} retries")


def find_new_relays(events: list[dict[str, Any]], seen: set[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for evt in events:
        eid = int(evt.get("id", 0))
        if eid in seen:
            continue
        seen.add(eid)
        if evt.get("type") == "relay":
            out.append(evt)
    return out


def build_prompt(room: dict[str, Any], latest_event: dict[str, Any] | None) -> str:
    latest_text = ""
    if latest_event:
        msg = (latest_event.get("payload") or {}).get("message") or {}
        latest_text = f"Incoming from {msg.get('sender')}: {msg.get('text')}"
    return (
        "Return ONLY JSON."
        " Schema keys: intent,text,fills,facts,questions,expect_reply,meta."
        " intent in ASK,ANSWER,NOTE,DONE,ASK_OWNER,OWNER_REPLY."
        f" Room goal: {room.get('goal')}"
        f" Required fields: {json.dumps(room.get('required_fields') or [])}"
        f" Known fields: {json.dumps(room.get('fields') or {})}"
        f" {latest_text}"
    )


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
    expect_reply = bool(raw.get("expect_reply", True))
    if intent in {"DONE", "ASK_OWNER"} and "expect_reply" not in raw:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex ClawRoom adapter")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--max-seconds", type=int, default=480)
    parser.add_argument("--offline-mock", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    started = time.time()
    cursor = 0
    seen: set[int] = set()

    join_resp = http_json(
        "POST",
        f"{base}/rooms/{args.room_id}/join",
        token=args.token,
        payload={"client_name": "CodexBridge"},
    )
    participant_name = join_resp["participant"]
    log("joined", participant_name)

    try:
        while True:
            if time.time() - started > args.max_seconds:
                log("max-seconds reached")
                break

            batch = http_json(
                "GET",
                f"{base}/rooms/{args.room_id}/events?after={cursor}&limit=200",
                token=args.token,
            )
            room = batch["room"]
            cursor = int(batch["next_cursor"])

            if room["status"] != "active":
                log("room ended", room.get("stop_reason"))
                break

            relays = find_new_relays(batch.get("events") or [], seen)
            for evt in relays:
                prompt = build_prompt(room, evt)
                if args.offline_mock:
                    raw = {
                        "intent": "ANSWER",
                        "text": "Mock Codex reply",
                        "fills": {},
                        "facts": [],
                        "questions": [],
                        "expect_reply": False,
                        "meta": {"mock": True},
                    }
                else:
                    api_key = os.getenv("OPENAI_API_KEY")
                    if not api_key:
                        raise RuntimeError("OPENAI_API_KEY is required unless --offline-mock is set")
                    raw = call_openai(args.model, api_key, prompt)
                outgoing = normalize_message(raw)
                if outgoing["intent"] == "ASK_OWNER":
                    outgoing["expect_reply"] = False
                http_json("POST", f"{base}/rooms/{args.room_id}/messages", token=args.token, payload=outgoing)
                log("sent", outgoing["intent"], short(outgoing["text"], 120))

            time.sleep(args.poll_seconds)
    finally:
        try:
            http_json(
                "POST",
                f"{base}/rooms/{args.room_id}/leave",
                token=args.token,
                payload={"reason": "client_exit"},
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
