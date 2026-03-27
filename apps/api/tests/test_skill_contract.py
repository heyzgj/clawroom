from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL_PATHS = [
    ROOT / "skills" / "clawroom" / "SKILL.md",
    ROOT / "apps" / "monitor" / "public" / "skill.md",
]


def test_skill_files_are_english_and_loop_safe():
    required = [
        "Match the user's language",
        "Keep technical detail hidden",
        "One message, then wait.",
        "Do not dump multiple messages.",
        "After `POST /join`, switch to the `participant_token`",
        "If `runnerd` is active, do NOT also join the room directly via API",
        "`ASK_OWNER` blocks the room until a real `OWNER_REPLY` arrives.",
        "DO NOT send your first message until the counterpart has joined the room.",
        "Always do one owner clarify before creating a room.",
        "Keep the clarify brief and human.",
        "Pre-room clarify is single-shot.",
        "Average owners often send one short sentence like",
        "Always ask one short clarify before creating the room, even if the request already sounds clear.",
        "The clarify can be a confirmation question or a single focused missing-detail question.",
        "If you need both the target agent and the desired handoff shape, ask for both in the same short message.",
        'If the owner already said "another OpenClaw" or "another agent", do not ask them for an invite link to the new room.',
        "Do not ask a second pre-room clarify.",
        "Do not ask a generic three-question form if the owner only needs a quick result.",
        "DO NOT call POST /rooms until the owner has replied to that one clarify.",
        "Never send only a raw join URL to the owner.",
        "After creating the room, stay responsible for it until it closes.",
        "When the counterpart is another general-purpose agent, assume it will skim.",
        "If you use a copyable block, that copyable block must be the full invite artifact.",
        "Do not say you already joined, are in position, are waiting in the room, or are monitoring live unless your own join really succeeded",
        "with the owner watch link as a full absolute `https://clawroom.cc/...` URL in that line",
        "Use this exact first line: `Room ready. Watch here: {absolute_monitor_link}`",
        "Do not put any other sentence before that line. The watch link line comes first.",
        'Watch here: [absolute participant watch link]. Waiting for the host to start.',
        'On join success, the watch link must appear in your first sentence.',
        "Owner update shape:",
        "If you are a joined participant rather than the host, give the owner the participant watch link",
        "Watch links must be full absolute `https://clawroom.cc/...` URLs, not relative paths.",
        "Do not mention execution internals like `execution_mode`, compatibility mode, managed runner status, recovery actions, root-cause hints, or repair packages unless the owner explicitly asked for debugging.",
        "For a normal public invite flow, if the current surface can make HTTPS requests, use the invite directly:",
        "treat the public invite as sufficient authority to enter; do not invent extra host-token, managed-runner, or bridge requirements unless the API itself explicitly rejects the join",
        "Do not search the workspace for `apps/openclaw-bridge` or require package installation just to accept a public invite.",
        "If the room closes with no useful outcome, times out, or never really starts, proactively tell the owner that plainly.",
        "Lead with your owner's context relevant to the goal.",
        "Return this full block to the owner; do not replace it with a naked URL:",
        "Join rule:",
        "You do not need a managed runner, bridge, localhost service, host token, or repair package just to enter this room.",
        "first `GET` the join link to inspect the room, then `POST /rooms/{room_id}/join` with the invite token from that link.",
        "If your surface cannot make HTTPS requests from this chat, say that plainly instead of inventing bridge or runner requirements.",
        "What to do now:",
        "Do not create a second room or side thread.",
        "The invite is owner-facing and counterpart-facing. Do not append raw JSON, host tokens, recovery hints, or internal execution diagnostics to it.",
        "If you present a copy button or code block, the full invite above must be inside it.",
        "Load https://clawroom.cc/skill.md if this surface needs the ClawRoom instructions.",
    ]
    cjk_pattern = re.compile(r"[\u4e00-\u9fff]")

    for path in SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert not cjk_pattern.search(text), f"{path} contains CJK text; skill must stay English-only."
        for item in required:
            assert item in text, f"{path} missing required contract line: {item}"


def test_managed_gateway_reference_is_not_the_default_public_invite_path() -> None:
    path = ROOT / "apps" / "monitor" / "public" / "references" / "managed-gateway.md"
    text = path.read_text(encoding="utf-8")
    assert "This page is not for the normal public-invite flow." in text
    assert "join with the public API first" in text
    assert "Only use this page when a known-working `runnerd` path already exists" in text


def test_api_reference_mentions_owner_and_participant_watch_links() -> None:
    for path in [
        ROOT / "skills" / "clawroom" / "references" / "api.md",
        ROOT / "apps" / "monitor" / "public" / "references" / "api.md",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "`monitor_link` is the owner watch link." in text
        assert "full absolute URL" in text
        assert 'watch_link: "https://clawroom.cc/?room_id=room_abc123&token=ptok_xxxx"' in text
        assert "https://clawroom.cc/?room_id={room_id}&token={participant_token}" in text
