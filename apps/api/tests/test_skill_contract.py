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
        "Keep this skill file in English.",
        "When replying to humans, match the user's language.",
        "Never print raw planning JSON to the user.",
        "Treat Telegram/Slack/OpenClaw chat as a gateway, not the long-running worker.",
        "Prefer a local or sidecar `runnerd` at `http://127.0.0.1:8741` whenever available.",
        "Use a forwardable wake package for cross-owner handoff in V0.",
        "Owner-to-owner manual forwarding is the default V0 wake flow.",
        "If the gateway cannot reliably call local `runnerd` itself, keep the wake package intact and let the owner or a local helper submit it.",
        "If `runnerd` is unavailable, fall back to the managed shell relay runner; shell remains candidate/fallback, not the release-grade main path.",
        "The gateway should not directly post `OWNER_REPLY` into the room when `runnerd` is active; the runner should post it.",
        "submit_cli.py",
        "owner_reply_cli.py",
        "ClawRoom wake package.",
        "If your gateway can reliably reach local runnerd, pass the JSON block below to POST /wake.",
        "`GET http://127.0.0.1:8741/healthz`",
        "`POST http://127.0.0.1:8741/wake`",
        "role `initiator`",
        "role `responder`",
        "the runner should send `OWNER_REPLY`, not the gateway",
        "Shell path remains:",
        "managed_attached",
        "runner_certification=candidate",
        "automatic_recovery_eligible=false",
        "https://clawroom.cc/openclaw-shell-bridge.sh",
        "Do not send kickoff before the guest has joined",
        "When the final decision is clear and no further reply is needed, send `DONE` instead of `ANSWER` with `expect_reply=false`.",
        "Do not include host invite token, raw planning JSON, markdown tables, or a room summary at create time.",
        "If neither `runnerd` nor shell fallback is possible:",
        "If the join request already includes explicit defaults, preferences, or constraints, do not ask for them again before joining.",
    ]
    cjk_pattern = re.compile(r"[\u4e00-\u9fff]")

    for path in SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert not cjk_pattern.search(text), f"{path} contains CJK text; skill must stay English-only."
        for item in required:
            assert item in text, f"{path} missing required contract line: {item}"
