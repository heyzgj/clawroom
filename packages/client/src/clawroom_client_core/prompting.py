from __future__ import annotations

import json
from typing import Any


def _field_values(room: dict[str, Any]) -> dict[str, Any]:
    fields = room.get("fields") or {}
    return {str(k): (v.get("value") if isinstance(v, dict) else v) for k, v in fields.items()}


def _latest_message(latest_event: dict[str, Any] | None) -> dict[str, Any]:
    if not latest_event or latest_event.get("type") != "relay":
        return {}
    payload = latest_event.get("payload") or {}
    return payload.get("message") or {}


def _is_system_debug_topic(*, room: dict[str, Any]) -> bool:
    combined = " ".join([str(room.get("topic") or ""), str(room.get("goal") or "")]).lower()
    hints = (
        "test",
        "testing",
        "regression",
        "debug",
        "bug",
        "qa",
        "quality",
        "message format",
        "prompt",
        "skill",
        "api",
        "bridge",
        "room",
    )
    return any(hint in combined for hint in hints)


def build_room_reply_prompt(
    *,
    role: str,
    room: dict[str, Any],
    self_name: str,
    latest_event: dict[str, Any] | None,
    has_started: bool,
    owner_context: str = "",
    commitments: list[str] | None = None,
    last_counterpart_ask: str = "",
    last_counterpart_message: str = "",
) -> str:
    latest_msg = _latest_message(latest_event)
    required_fields = room.get("required_fields") or []
    field_values = _field_values(room)
    debug_topic = _is_system_debug_topic(room=room)

    role_hint = {
        "initiator": "You start the conversation when the peer has joined. Open with a concrete, topic-relevant ask or suggestion.",
        "responder": "You respond directly to the peer's latest point and help the conversation converge on a decision.",
    }.get(role, "You are one participant in the room. Help the conversation move toward a concrete outcome.")

    starter = ""
    if role == "initiator" and not has_started:
        starter = "There is no incoming message yet and both participants are ready. Send the first in-room message now."

    incoming = "No incoming relay message yet."
    if latest_msg:
        incoming = (
            "Incoming message:\n"
            f"- from: {latest_msg.get('sender') or ''}\n"
            f"- intent: {latest_msg.get('intent')}\n"
            f"- text: {latest_msg.get('text')}\n"
            f"- fills: {json.dumps(latest_msg.get('fills') or {}, ensure_ascii=False)}"
        )

    meta_guardrail = (
        "The topic itself is about testing/debugging, so platform terms are allowed only when they directly help solve that topic."
        if debug_topic
        else (
            "Do not mention room mechanics, relay, message format, JSON, testing, regression, turn counts, deadlines, host, guest, owner, skill pages, or APIs. "
            "Talk only about the real topic."
        )
    )

    owner_context_block = f"Owner context: {owner_context.strip()}\n" if owner_context.strip() else ""
    commitments_block = f"Recent commitments: {json.dumps(commitments or [], ensure_ascii=False)}\n"
    ask_block = f"Last counterpart ask: {last_counterpart_ask.strip()}\n" if last_counterpart_ask.strip() else ""
    counterpart_block = (
        f"Last counterpart message: {last_counterpart_message.strip()}\n" if last_counterpart_message.strip() else ""
    )

    return (
        "You are composing ONE in-room message for the other participant.\n"
        "Return ONLY one JSON object and nothing else.\n\n"
        f"Participant: {self_name}\n"
        f"Role: {role}\n"
        f"Role guidance: {role_hint}\n"
        f"Topic: {room.get('topic')}\n"
        f"Goal: {room.get('goal')}\n"
        f"Required fields: {json.dumps(required_fields, ensure_ascii=False)}\n"
        f"Known fields: {json.dumps(field_values, ensure_ascii=False)}\n"
        f"{commitments_block}"
        f"{ask_block}"
        f"{counterpart_block}"
        f"{owner_context_block}"
        f"{incoming}\n"
        f"{starter}\n\n"
        "How to respond:\n"
        "- Keep your normal runtime/owner voice. Do not invent a special ClawRoom persona.\n"
        "- Add new information, a concrete option, a decision, or one forward-moving question.\n"
        "- If you are the initiator sending the very first in-room message, it must invite a reply. Do not end the opening turn with NOTE, DONE, or expect_reply=false.\n"
        "- Do not use ASK_OWNER as the opening turn. First contribute at least one substantive in-room message and let the counterpart react before escalating to the owner, unless the room is explicitly blocked on owner-only information from the start.\n"
        "- If your opening turn is a proposal, explicitly ask for the counterpart's reaction in the same message.\n"
        "- Do not quote or paraphrase the counterpart's text unless one short phrase is necessary for clarity.\n"
        f"- {meta_guardrail}\n"
        "- Avoid filler-only openings like 'Got it', 'Sounds good', 'Thanks for clarifying', or 'I got your suggestion' unless the same sentence immediately adds substance.\n"
        "- Keep text to 1-3 short sentences and under 80 words.\n"
        "- Ask at most one direct question.\n"
        "- If enough information exists to make the decision, say the decision clearly.\n"
        "- If you are locking the final plan and no further reply is needed, prefer DONE over ANSWER.\n"
        "- Use fills for required fields you know.\n"
        "- Use ASK_OWNER only when blocked on owner-only information.\n"
        "- Use NOTE only for brief updates that genuinely do not need a reply.\n"
        "- Use DONE only when you are closing with a clear outcome or no further reply is needed.\n\n"
        "Failure-mode example:\n"
        "- Bad text: Got it, you said sushi. What do you think?\n"
        "- Why bad: repeats the counterpart without adding anything new.\n"
        "- Better text: Sushi works. A salmon bowl or spicy tuna roll would both fit tonight; which one sounds better?\n\n"
        "Self-check before finalizing:\n"
        "- Does this message add something new or move the room to a decision?\n"
        "- Does it avoid platform/meta language unless the topic is about debugging?\n"
        "- Is the intent consistent with whether a reply is needed?\n\n"
        "Output schema (all keys required):\n"
        "{"
        '"intent":"ASK|ANSWER|NOTE|DONE|ASK_OWNER|OWNER_REPLY",'
        '"text":"short message",'
        '"fills":{"optional_field":"value"},'
        '"facts":["optional fact"],'
        '"questions":["optional question"],'
        '"expect_reply":true,'
        '"meta":{}'
        "}"
    )


def build_owner_reply_prompt(
    *,
    room: dict[str, Any],
    self_name: str,
    role: str,
    owner_req_id: str,
    owner_text: str,
    owner_context: str = "",
    commitments: list[str] | None = None,
) -> str:
    required_fields = room.get("required_fields") or []
    field_values = _field_values(room)
    debug_topic = _is_system_debug_topic(room=room)
    meta_guardrail = (
        "The topic is about testing/debugging, so platform terms are allowed only when genuinely useful."
        if debug_topic
        else "Do not mention room mechanics, JSON, relay, host, guest, owner, testing, or APIs in the visible message."
    )

    owner_context_block = f"Owner context: {owner_context.strip()}\n" if owner_context.strip() else ""
    commitments_block = f"Recent commitments: {json.dumps(commitments or [], ensure_ascii=False)}\n"

    return (
        "You are composing ONE in-room message after receiving an owner reply out-of-band.\n"
        "Return ONLY one JSON object and nothing else.\n\n"
        f"Participant: {self_name}\n"
        f"Role: {role}\n"
        f"Topic: {room.get('topic')}\n"
        f"Goal: {room.get('goal')}\n"
        f"Required fields: {json.dumps(required_fields, ensure_ascii=False)}\n"
        f"Known fields: {json.dumps(field_values, ensure_ascii=False)}\n"
        f"{commitments_block}"
        f"{owner_context_block}"
        f"owner_req_id: {owner_req_id}\n"
        f"owner_text: {owner_text}\n\n"
        "Turn the owner reply into an in-room message that moves the topic forward without changing your normal voice.\n"
        "How to respond:\n"
        "- Keep text under 80 words.\n"
        "- Use fills for required fields when possible.\n"
        f"- {meta_guardrail}\n"
        "- Do not say that the owner replied out-of-band.\n"
        "- Intent MUST be OWNER_REPLY.\n"
        "- Set meta.owner_req_id to the provided value.\n\n"
        "Output schema (all keys required):\n"
        "{"
        '"intent":"OWNER_REPLY",'
        '"text":"short message",'
        '"fills":{"optional_field":"value"},'
        '"facts":["optional fact"],'
        '"questions":["optional question"],'
        '"expect_reply":true,'
        '"meta":{}'
        "}"
    )
