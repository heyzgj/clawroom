from __future__ import annotations

from clawroom_client_core import build_owner_reply_prompt, build_room_reply_prompt


def _room(topic: str, goal: str) -> dict:
    return {
        "topic": topic,
        "goal": goal,
        "required_fields": ["decision"],
        "fields": {"decision": {"value": ""}},
    }


def test_room_prompt_contains_anti_parrot_and_anti_meta_rules() -> None:
    prompt = build_room_reply_prompt(
        role="responder",
        room=_room("What to eat tonight", "Decide what to eat for dinner"),
        self_name="guest",
        latest_event={"type": "relay", "payload": {"message": {"sender": "host", "intent": "ASK", "text": "Sushi or noodles?"}}},
        has_started=True,
        commitments=["Decide dinner tonight"],
        last_counterpart_ask="Sushi or noodles?",
        last_counterpart_message="Sushi or noodles?",
    )
    assert "Do not quote or paraphrase the counterpart's text" in prompt
    assert "Do not mention room mechanics" in prompt
    assert "Bad text: Got it, you said sushi. What do you think?" in prompt
    assert "Better text: Sushi works." in prompt
    assert "prefer DONE over ANSWER" in prompt
    assert "it must invite a reply" in prompt


def test_room_prompt_relaxes_platform_term_rule_for_debug_topics() -> None:
    prompt = build_room_reply_prompt(
        role="responder",
        room=_room("ClawRoom regression test", "Debug a multi-turn room issue"),
        self_name="guest",
        latest_event=None,
        has_started=True,
    )
    assert "platform terms are allowed only when they directly help solve that topic" in prompt


def test_owner_reply_prompt_requires_naturalized_reply() -> None:
    prompt = build_owner_reply_prompt(
        room=_room("What to eat tonight", "Decide dinner"),
        self_name="host",
        role="initiator",
        owner_req_id="oreq_123",
        owner_text="Budget under 200 and no spicy food.",
    )
    assert "without changing your normal voice" in prompt
    assert "Do not say that the owner replied out-of-band." in prompt
    assert "owner_req_id: oreq_123" in prompt


def test_room_prompt_preserves_runtime_voice_instead_of_injecting_persona() -> None:
    prompt = build_room_reply_prompt(
        role="responder",
        room=_room("Where should we go tomorrow?", "Pick one outing plan"),
        self_name="guest",
        latest_event={"type": "relay", "payload": {"message": {"sender": "host", "intent": "ASK", "text": "Lake walk or museum?"}}},
        has_started=True,
    )
    assert "Keep your normal runtime/owner voice. Do not invent a special ClawRoom persona." in prompt


def test_room_prompt_blocks_ask_owner_as_opening_move() -> None:
    prompt = build_room_reply_prompt(
        role="initiator",
        room=_room("Choose tonight's dinner", "Reach one dinner decision"),
        self_name="host",
        latest_event=None,
        has_started=False,
    )
    assert "Do not use ASK_OWNER as the opening turn." in prompt
