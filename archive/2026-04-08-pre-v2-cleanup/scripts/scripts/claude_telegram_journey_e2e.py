#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "client" / "src"))
sys.path.insert(0, str(ROOT / "skills" / "openclaw-telegram-e2e" / "scripts"))

from clawroom_client_core import build_owner_reply_prompt, build_room_reply_prompt, evaluate_room_quality  # noqa: E402
from create_telegram_test_room import build_join_prompt, create_room  # noqa: E402
from telegram_desktop import capture_chat_surface, send_sequence  # noqa: E402


DEFAULT_BASE_URL = "https://api.clawroom.cc"
DEFAULT_UI_BASE = "https://clawroom.cc"
TELEGRAM_MIN_OWNER_REPLY_GAP_SECONDS = 35.0
TELEGRAM_DUPLICATE_REPLY_WINDOW_SECONDS = 600.0


@dataclass(slots=True)
class ScenarioSpec:
    slug: str
    prompt_scenario: str
    topic: str
    goal: str
    required_fields: list[str]
    owner_context: str
    hidden_owner_reply: str
    clarify_answers: list[str]
    field_principles: dict[str, str] = field(default_factory=dict)
    scenario_hint: str | None = None
    telegram_followup_reply: str = ""
    turn_limit: int = 8
    stall_limit: int = 6
    timeout_minutes: int = 15
    ttl_minutes: int = 60


SCENARIOS: dict[str, ScenarioSpec] = {
    "simple_dinner": ScenarioSpec(
        slug="simple_dinner",
        prompt_scenario="natural",
        topic="Help decide what to eat tonight",
        goal="Return one dinner pick, why it fits tonight, and one backup option",
        required_fields=["pick", "why", "backup"],
        field_principles={
            "pick": "Must name one concrete dinner choice in plain language.",
            "why": "Must explain briefly why it fits tonight in plain language.",
            "backup": "Must give one concrete fallback option in plain language.",
        },
        owner_context=(
            "Your owner wants a quick practical answer, not a debate. Keep it simple, specific, and easy to act on tonight."
        ),
        hidden_owner_reply="Keep the plan easy to execute tonight, avoid anything that needs a long trip or reservation, and keep budget moderate.",
        clarify_answers=[
            "我只想很快定下来，不想再来回讨论很多轮。",
            "优先简单、好执行、今晚就能去或者点到，不要太贵。",
            "最好给我一个主推荐，再给一个备选，这样我马上就能决定。",
        ],
        telegram_followup_reply=(
            "Keep it simple for tonight: somewhere easy, moderate budget, and no long trip or reservation. "
            "I want one clear recommendation plus one backup, not a big debate. "
            "My gut is to optimize for fast and reliable over novelty."
        ),
    ),
    "weekly_content_plan": ScenarioSpec(
        slug="weekly_content_plan",
        prompt_scenario="natural",
        topic="Plan next week's content in one lightweight handoff",
        goal="Return one clear content focus, a simple posting rhythm, and the first action to start this week",
        required_fields=["content_focus", "posting_rhythm", "first_action"],
        field_principles={
            "content_focus": "Must name one clear content focus in owner-facing language.",
            "posting_rhythm": "Must suggest a simple schedule the owner can follow next week.",
            "first_action": "Must include one concrete action the owner can start this week.",
        },
        owner_context=(
            "Your owner wants one lightweight plan they can act on right away. Keep it simple, clear, and realistic for one week."
        ),
        hidden_owner_reply="The plan should stay lightweight: one main content angle, no heavy production, and something the owner can start this week.",
        clarify_answers=[
            "我想要的是一个很轻的下周内容计划，不要变成一整套复杂策略。",
            "优先给我一个主方向、一个简单发布节奏、以及我这周就能开始的第一步。",
            "不要假设有大团队或复杂制作资源，保持真实可执行。",
        ],
        telegram_followup_reply=(
            "Keep next week's content plan light: one main angle, a simple posting rhythm, and one concrete first step this week. "
            "Do not assume a big team or heavy production. "
            "My gut is to choose clarity and consistency over volume."
        ),
    ),
    "proposal_synthesis": ScenarioSpec(
        slug="proposal_synthesis",
        prompt_scenario="natural",
        topic="Draft a launch proposal for a lightweight cross-owner collaboration flow",
        goal="Agree on one practical launch proposal and the next validation step",
        required_fields=["proposal", "tradeoffs", "next_step"],
        field_principles={
            "proposal": "Must describe one credible launch proposal that a small team can validate quickly.",
            "tradeoffs": "Must name at least one upside and one downside of the chosen proposal.",
            "next_step": "Must include one concrete action with an owner or timing.",
        },
        owner_context=(
            "Your owner cares about a simple, believable launch. Avoid complex infra. "
            "Push for a proposal that a small team can validate in one week."
        ),
        hidden_owner_reply="One-week validation only. No large platform rebuild in the first step.",
        clarify_answers=[
            "核心立场：先做轻量、可信、可复用的协作闭环，让 owner 能把问题带进房间、拿到结论，再把结果带回去；第一版不要引入复杂 infra。",
            "要推进：先验证真实的 Telegram -> 房间 -> owner clarify -> 回房 -> 结论回 owner。要避开：第二个 app 或插件安装、过度技术化的交互、以及靠人工同步维持上下文。",
            "如果先做 MVP，实际需要：能从现有 surface 发起协作；双方能加入同一房间连续对话；支持 ask_owner / owner_reply；最后能把结论或 artifact 带回 owner；失败时要明确提示卡点。",
        ],
        telegram_followup_reply=(
            "Start with the launch proposal workflow itself: owner asks a question, two agents meet in one room, one owner clarification happens if needed, and the result comes back as a short handoff. "
            "The pain point is context loss across owners and tools, so ClawRoom only matters if it produces one believable result without extra infra. "
            "Constraints: keep it light, no second install, and make the first validation fit in one week. My gut is lean yes on a very scoped launch proof."
        ),
    ),
    "owner_constraint": ScenarioSpec(
        slug="owner_constraint",
        prompt_scenario="owner_escalation",
        topic="Choose the first collaboration workflow to ship with one hidden owner constraint",
        goal="Reach a final workflow decision after exactly one owner-only clarification",
        required_fields=["decision", "rationale", "fallback"],
        field_principles={
            "decision": "Must be a specific workflow choice, not a generic direction.",
            "rationale": "Must reference the hidden owner constraint once it becomes known.",
            "fallback": "Must give a concrete second-best option if the primary choice is blocked.",
        },
        owner_context=(
            "You represent the local product owner. There is one hidden constraint that should not be revealed until "
            "you have had at least one normal exchange with the counterpart and truly need a decision."
        ),
        hidden_owner_reply="Hidden constraint: the first version cannot require a second app install or browser extension.",
        clarify_answers=[
            "核心立场：第一版要把 owner loop 跑通，但不能让普通用户感觉流程很重；最好一次进入就能理解怎么协作。",
            "要推进：尽快看清哪个 workflow 最容易落地并且可解释。要避开：需要第二个安装、隐性前提太多、以及房间外信息丢失。",
            "如果先做 MVP，实际需要：一个主流程、一次必要澄清、一次 owner reply、一次回房收敛，并且最终能给 owner 明确 decision 和 fallback。",
        ],
        telegram_followup_reply=(
            "Pilot the workflow where one owner asks for a decision and the room has to use exactly one owner-only clarification before it can close. "
            "The pain point is that cross-owner context gets lost when the hidden constraint stays outside the room. "
            "Constraints: no second install, minimal friction, and one clear fallback if the preferred workflow gets blocked. My gut is lean yes if the owner loop stays explicit and bounded."
        ),
    ),
    "implementation_tradeoff": ScenarioSpec(
        slug="implementation_tradeoff",
        prompt_scenario="natural",
        topic="Resolve a product-vs-implementation tradeoff for a collaborative agent workflow",
        goal="Produce one agreed plan that balances user simplicity with implementation safety",
        required_fields=["user_flow", "implementation_constraints", "agreed_plan"],
        field_principles={
            "user_flow": "Must describe the simple user-facing flow in concrete steps.",
            "implementation_constraints": "Must name the technical boundaries the plan cannot violate.",
            "agreed_plan": "Must resolve the tradeoff into one plan, not a list of unresolved ideas.",
        },
        owner_context=(
            "Your owner has codebase context and wants something implementation-safe. "
            "Favor a plan that fits existing primitives and avoids fragile magic."
        ),
        hidden_owner_reply="The implementation must reuse existing room primitives and avoid adding a new always-on service.",
        clarify_answers=[
            "核心立场：用户体验要简单，但方案必须贴合现有 room primitives，不能靠新常驻服务来撑住流程。",
            "要推进：把用户想要的顺滑协作体验和实现边界放到一张桌子上谈清楚。要避开：为了一点丝滑感引入脆弱魔法和难维护链路。",
            "如果先做 MVP，实际需要：创建房间、稳定 join、连续 relay、owner clarify、结果回传，以及对失败原因有明确可见性。",
        ],
        telegram_followup_reply=(
            "Pilot the simplest workflow where a user can start one shared room from an existing surface and get one agreed plan back. "
            "The pain point is that implementation-safe collaboration usually feels too technical, so we need something simple without adding a new always-on service. "
            "Constraints: reuse existing room primitives, keep failure modes visible, and avoid fragile magic. My gut is lean yes on a safety-first pilot."
        ),
    ),
    "artifact_brief": ScenarioSpec(
        slug="artifact_brief",
        prompt_scenario="natural",
        topic="Prepare an owner-ready handoff memo for whether Bamboo Studio should run a scoped ClawRoom pilot on one crypto workflow",
        goal="Leave the room with a handoff packet the owner can forward unchanged to a teammate or use to approve the next step today",
        required_fields=["decision", "why_now", "ranked_options", "success_metrics", "owner_actions"],
        field_principles={
            "decision": "Must be a specific actionable decision, not 'needs further discussion', in owner-facing language with no protocol jargon, and it must match the top-ranked option.",
            "why_now": "Must explain why this decision matters now using concrete evidence or constraints, in owner-facing language.",
            "ranked_options": "Must list at least 2 options, with the chosen recommendation first, in owner-facing language the owner could forward unchanged.",
            "success_metrics": "Must include at least one measurable metric with a timeline and a clear continue or pause threshold, in owner-facing language.",
            "owner_actions": "Must include at least one action the owner can take this week, with timing, written so they could forward unchanged to a teammate.",
        },
        scenario_hint="decision_packet",
        owner_context=(
            "Your owner may forward this memo unchanged. The output must be short, concrete, and ready for real work: a clear decision, why it matters now, ranked options, success metrics, and owner actions with timing. The ranked options must put the chosen recommendation first, and the decision line must match that top-ranked option."
        ),
        hidden_owner_reply="The handoff should be compact enough to forward unchanged, but specific enough that a teammate could act on it without another meeting.",
        clarify_answers=[
            "核心立场：最后产出必须是 owner 能直接转发或批准的 handoff packet，不只是聊天总结。",
            "要推进：给出明确 decision、为什么现在做、备选项排序、成功指标、以及 owner 这周要做的动作。要避开：空泛愿景、只有方向没有动作、以及需要 owner 再开会补信息。",
            "如果先做 MVP，实际需要：围绕一个真实 crypto workflow 试点，给出 30 天内能执行的范围、衡量方式、以及明确 deadline。",
        ],
        telegram_followup_reply=(
            "Pilot the daily crypto market brief / trade-research workflow first. "
            "The pain point is that Bamboo Studio has strategy context on one side and execution context on the other, so ClawRoom only matters if it turns that split into one owner-ready packet now. "
            "Constraints: keep it lean, no second install, low coordination overhead, and a two-week pilot window. "
            "My gut is lean yes on a scoped pilot, not a broad rollout."
        ),
    ),
    "beta_launch_readiness": ScenarioSpec(
        slug="beta_launch_readiness",
        prompt_scenario="natural",
        topic="Decide whether ClawRoom should open a limited beta to a small group of design partners next week",
        goal="Return a go/no-go call the owner can act on today, plus the blocking risks and the launch plan",
        required_fields=["go_no_go", "blocking_risks", "launch_plan"],
        field_principles={
            "go_no_go": "Must be a specific go or no-go decision, not a maybe, in owner-facing language.",
            "blocking_risks": "Must name at least two concrete launch risks or constraints in owner-facing language.",
            "launch_plan": "Must include one action this week, one measurable beta guardrail, and one clear pause condition, written so a non-technical owner can execute it.",
        },
        owner_context=(
            "Your owner wants a small, confidence-building beta, not a broad launch. "
            "Favor a crisp recommendation with visible guardrails over a long debate."
        ),
        hidden_owner_reply="Hidden constraint: the first beta can support at most 5 design partners and there is no new on-call rotation available next week.",
        clarify_answers=[
            "核心立场：如果要开 beta，就必须是很小、很可控、很容易解释的一批 design partners，不追求声量。",
            "要推进：今天就能做出 go/no-go，并明确 blocking risks 和 launch plan。要避开：模糊的 maybe、没有 guardrail 的试放量、以及靠人肉盯盘撑 beta。",
            "如果先做 MVP，实际需要：明确 beta 人数、准入标准、风险边界、以及一周内怎么判断继续还是暂停。",
        ],
        telegram_followup_reply=(
            "Lean toward a limited beta only if it stays very small and operationally calm. "
            "The real question is not visibility, it is whether next week's beta can generate signal without creating a support burden. "
            "Constraints: cap the first beta at a handful of design partners, avoid any new on-call load, and make the stop/go guardrails obvious. "
            "My gut is lean go on a tiny beta, not a broader opening."
        ),
    ),
    "feedback_prioritization": ScenarioSpec(
        slug="feedback_prioritization",
        prompt_scenario="natural",
        topic="Choose the highest-priority product friction to fix next for ClawRoom based on real user complaints",
        goal="Pick one issue to fix next, explain why it wins, and define the smallest experiment the owner can start this week",
        required_fields=["top_issue", "why_this_first", "experiment_plan"],
        field_principles={
            "top_issue": "Must name one specific user-facing problem, not a vague area, in owner-facing language.",
            "why_this_first": "Must explain the frequency or impact that makes this issue win now, in owner-facing language.",
            "experiment_plan": "Must include one action this week, a scoped test, and explicit continue and pause signals in owner-facing language with no internal jargon.",
        },
        owner_context=(
            "Your owner cares about the 99% path. Pick the single friction that most directly improves clarity, confidence, or flow for normal users."
        ),
        hidden_owner_reply="Hidden constraint: there is only one engineer-week available, so the next fix cannot require a protocol redesign.",
        clarify_answers=[
            "核心立场：先修最影响正常用户完成主流程的 friction，不做大而全的产品清理。",
            "要推进：在多个痛点里选一个最值得先做的，并定义一个这周就能开始的实验。要避开：列一堆问题但没有主次，或者选一个需要协议重做的大工程。",
            "如果先做 MVP，实际需要：一个 top issue、为什么它最先、以及这周能启动的小实验和明确 pass/fail signal。",
        ],
        telegram_followup_reply=(
            "The most credible next step is to fix the single user-facing friction that most often breaks the main collaboration flow. "
            "Think in terms of frequency and impact, not architectural neatness. "
            "Constraints: only one engineer-week is available, so the next move must avoid protocol redesign and focus on a visible UX win. "
            "My gut is to prioritize the failure that most often leaves a normal owner confused or blocked."
        ),
    ),
    "cross_role_alignment": ScenarioSpec(
        slug="cross_role_alignment",
        prompt_scenario="natural",
        topic="Align a product requirement with operations execution so the owner knows what it means, what work needs to happen next, and what to send back",
        goal="Return one owner-ready packet with a shared reading, concrete owner actions, and one short reply the owner can send back to the product side today",
        required_fields=["shared_reading", "owner_actions", "reply_back"],
        field_principles={
            "shared_reading": "Must explain in plain language what both sides are aligned on and what changes for operations.",
            "owner_actions": "Must include concrete actions the owner or ops lead should take this week, with timing, in language that can be forwarded unchanged.",
            "reply_back": "Must be a short message the owner can send back to the product side without rewriting.",
        },
        owner_context=(
            "Your owner acts like the operations lead receiving a requirement from product. They do not want a long analysis. "
            "They need one plain-language reading of what product is asking, what operations now needs to do, and one short note they can send back today."
        ),
        hidden_owner_reply="Assume the requirement doc already exists. Avoid proposing a new meeting unless there is a true blocker. Keep the packet usable for today, not a future workshop.",
        clarify_answers=[
            "我站在运营负责人这边，需要的不是需求复述，而是看完之后到底意味着什么、我这周要做什么、以及我怎么回给产品。",
            "优先给我一个共同理解、一个可执行动作清单、还有一条我可以直接发回去的短回复。不要让我再自己翻译一遍。",
            "默认需求文档已经在了，这次先做对齐和落地，不要把结果变成再开一次会的建议。",
        ],
        telegram_followup_reply=(
            "Treat this like product-to-ops alignment on one real requirement doc. "
            "I need one shared reading of what the requirement really means, what operations should do this week, and one short reply I can send back to product today. "
            "Avoid turning it into a workshop or another meeting unless there is a true blocker."
        ),
    ),
    "founder_persona_sync": ScenarioSpec(
        slug="founder_persona_sync",
        prompt_scenario="natural",
        topic="Align one founder persona across Xiaohongshu and overseas social so this week's messaging stays consistent and reusable assets are clear",
        goal="Return one owner-ready packet with the weekly founder angle, reusable assets, and a clear Xiaohongshu vs overseas channel split for this week",
        required_fields=["weekly_angle", "shared_assets", "channel_split"],
        field_principles={
            "weekly_angle": "Must name one clear founder narrative for this week that both channels can use, in plain language.",
            "shared_assets": "Must list the reusable stories, quotes, screenshots, or examples both sides can reuse this week.",
            "channel_split": "Must state what Xiaohongshu should emphasize and what overseas social should emphasize, with concrete channel-specific guidance in owner-facing language.",
        },
        owner_context=(
            "Your owner and a teammate run the same founder persona on two channels: Xiaohongshu and overseas social. "
            "This is a weekly content sync, not a product roadmap review or engineering update. "
            "The packet should say what the shared weekly founder angle is, what stories or assets both sides can reuse, and how Xiaohongshu versus overseas social should adapt the same voice."
        ),
        hidden_owner_reply="Keep it lightweight: reuse the founder's existing opinions, stories, and tone. Do not turn this into an internal build log or a new brand direction unless that is already the founder's public voice.",
        clarify_answers=[
            "这是同一个 founder 人设在小红书和海外社媒之间做周同步，不是两套独立内容策略。我要的是这周统一主叙事、能共用的素材、以及两个渠道怎么分工。",
            "优先让两边不要说出互相冲突的话，同时把 founder 现有的故事、观点和素材尽量复用起来。不要把它做成产品 build log 或新的品牌重塑。",
            "默认轻量执行：一个清楚的周主题、几个可复用素材、再加上小红书和海外社媒各自的表达重点就够了。",
        ],
        telegram_followup_reply=(
            "This is a weekly sync for one founder persona across Xiaohongshu and overseas social. "
            "I need one shared weekly founder angle, a clear list of reusable stories or assets, and a simple split for what Xiaohongshu should emphasize versus what overseas social should emphasize this week. "
            "Keep it lightweight, reuse existing founder stories and tone, and do not turn it into an internal build log."
        ),
    ),
    "task_okr_sync": ScenarioSpec(
        slug="task_okr_sync",
        prompt_scenario="natural",
        topic="Align weekly task execution with OKR tracking so the owner can assign work and keep the team synced without extra meetings",
        goal="Return one owner-ready packet with the work breakdown, owner assignments, and a simple weekly progress check",
        required_fields=["task_breakdown", "owner_assignments", "weekly_check"],
        field_principles={
            "task_breakdown": "Must explain the week's main workstreams in plain language, not as vague categories.",
            "owner_assignments": "Must assign who owns what and by when, in language the owner can forward unchanged.",
            "weekly_check": "Must define one simple progress or OKR check with a clear continue or pause signal for this week.",
        },
        owner_context=(
            "Your owner is coordinating execution across teammates. They need the room to turn a weekly plan into clear work ownership and one lightweight sync rule, not another planning document."
        ),
        hidden_owner_reply="Keep the sync lightweight: one week only, no complicated project management system, and something the owner can use in a quick team check-in.",
        clarify_answers=[
            "我需要的是这周怎么拆任务、谁负责什么、以及我怎么快速看 OKR 有没有在推进，不是再来一份大而全计划书。",
            "优先让我能直接分派工作，再用一个很轻的 weekly check 看有没有偏离。不要把结果变成复杂项目管理系统。",
            "默认只看这一周，给出清楚 owner assignments 和一个继续/暂停的判断信号就够了。",
        ],
        telegram_followup_reply=(
            "Treat this like a weekly execution sync tied to OKR progress. "
            "I need a clear task breakdown, explicit owner assignments with timing, and one simple weekly check that tells me whether to keep going or intervene. "
            "Keep it lightweight enough for a quick team sync, not a heavyweight PM system."
        ),
    ),
}


def parse_join_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    room_id = ""
    parts = [part for part in parsed.path.split("/") if part]
    if "join" in parts:
        idx = parts.index("join")
        if idx + 1 < len(parts):
            room_id = parts[idx + 1]
    token = parse_qs(parsed.query).get("token", [""])[0]
    if not parsed.scheme or not parsed.netloc or not room_id or not token:
        raise ValueError(f"invalid join url: {url}")
    return {
        "base_url": f"{parsed.scheme}://{parsed.netloc}",
        "room_id": room_id,
        "token": token,
    }


def http_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    invite_token: str | None = None,
    participant_token: str | None = None,
    host_token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if invite_token:
        headers["X-Invite-Token"] = invite_token
    if participant_token:
        headers["X-Participant-Token"] = participant_token
    if host_token:
        headers["X-Host-Token"] = host_token
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            response = client.request(method, path, headers=headers, json=payload)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt >= 5:
                break
            time.sleep(2.0 * (attempt + 1))
            continue
        if response.status_code >= 500 and attempt < 5:
            time.sleep(2.0 * (attempt + 1))
            continue
        if response.status_code >= 400:
            body = (response.text or "").strip()
            raise RuntimeError(f"{method} {path} failed status={response.status_code} body={body[:500]}")
        return response.json() if response.text else {}
    raise RuntimeError(f"{method} {path} failed after retries: {last_error}")


def is_room_not_active_conflict(exc: Exception) -> bool:
    text = str(exc or "")
    return "status=409" in text and "room not active" in text.lower()


def extract_first_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start : idx + 1]
                    return json.loads(chunk)
        start = text.find("{", start + 1)
    raise RuntimeError(f"Claude did not return a JSON object: {text[:500]}")


def run_claude_command(prompt: str, *, timeout: int = 180) -> str:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            proc = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
            return (proc.stdout or "").strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            if isinstance(exc, subprocess.CalledProcessError):
                output = " ".join(part for part in [(exc.stdout or "").strip(), (exc.stderr or "").strip()] if part).strip()
                if "Not logged in" in output:
                    raise RuntimeError("Claude CLI is not logged in. Run /login in Claude Code, then rerun this E2E.") from exc
            last_error = exc
            if attempt >= 1:
                break
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"claude command failed after retries: {last_error}")


def call_claude_json(prompt: str, *, timeout: int = 180) -> dict[str, Any]:
    return extract_first_json_object(run_claude_command(prompt, timeout=timeout))


def call_claude_text(prompt: str, *, timeout: int = 180) -> str:
    return run_claude_command(prompt, timeout=timeout)


def assert_claude_ready() -> None:
    try:
        proc = subprocess.run(
            ["claude", "-p", "Reply with exactly OK."],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Claude CLI was not found on PATH.") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    combined = " ".join(part for part in [stdout, stderr] if part).strip()
    if proc.returncode != 0:
        if "Not logged in" in combined:
            raise RuntimeError("Claude CLI is not logged in. Run /login in Claude Code, then rerun this E2E.")
        raise RuntimeError(f"Claude CLI preflight failed: {combined or f'returncode={proc.returncode}'}")


def build_clarify_reply(spec: ScenarioSpec) -> str:
    room_anchor = "Stay inside the provided room only and do not create a second room or briefing room."
    base = spec.telegram_followup_reply.strip()
    if base:
        return f"{room_anchor} {base} Please use your managed room flow now and keep the room moving."
    return (
        f"We're testing this scenario: {spec.topic}. "
        f"The goal is {spec.goal}. "
        f"{room_anchor} "
        "Please use your managed room flow now, carry this owner context in, and keep the room moving."
    )


def load_surface_ocr(surface: dict[str, Any]) -> str:
    path = str(surface.get("ocr_text_path") or "").strip()
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8").strip()


def normalized_surface_tail(surface: dict[str, Any], *, max_chars: int = 900) -> str:
    text = " ".join(load_surface_ocr(surface).split())
    return text[-max_chars:]


def surface_looks_like_owner_followup(surface: dict[str, Any]) -> bool:
    tail = normalized_surface_tail(surface)
    if not tail:
        return False
    tail_lower = tail.lower()
    direct_cues = (
        "i need your context",
        "before i can contribute",
        "key questions",
        "what's the crypto workflow",
        "what's the pain point",
        "any constraints",
        "what's your gut",
        "need your specifics",
        "请补充",
        "我需要你的上下文",
        "关键问题",
        "约束",
        "你的判断",
        "pending:",
        "markets",
        "delivery",
        "gmt+8",
    )
    numbered_questions = (
        sum(marker in tail for marker in ("1.", "2.", "3.", "4.")) >= 2
        and any(keyword in tail_lower for keyword in ("workflow", "pain point", "constraints", "context", "gut", "specifics"))
    )
    return any(cue in tail_lower or cue in tail for cue in direct_cues) or numbered_questions


def surface_looks_like_gateway_path_question(surface: dict[str, Any]) -> bool:
    tail = normalized_surface_tail(surface)
    if not tail:
        return False
    tail_lower = tail.lower()
    cues = (
        "which path works best",
        "runnerd url",
        "managed path",
        "wake package is valid",
        "ready to forward",
        "forwards invite directly",
        "can try that directly",
        "local helper or daemon",
        "dual-session issues",
    )
    return any(cue in tail_lower for cue in cues)


def surface_looks_like_runnerd_unreachable(surface: dict[str, Any]) -> bool:
    tail = normalized_surface_tail(surface)
    if not tail:
        return False
    tail_lower = tail.lower()
    cues = (
        "runnerd unreachable",
        "runnerd not reachable",
        "sidecar isn't running",
        "direct room join not attempted",
        "helper path",
        "cannot submit wake package",
        "can't submit wake package",
        "direct api join would be the fallback",
        "direct api join is the fallback",
        "let me know how you'd like to proceed",
        "unavailable/ timeout",
        "unavailable/timeout",
    )
    return any(cue in tail_lower for cue in cues)


def build_dynamic_clarify_reply(spec: ScenarioSpec, surface: dict[str, Any]) -> str:
    ocr_text = load_surface_ocr(surface)
    if not ocr_text:
        return build_clarify_reply(spec)
    tail = " ".join(ocr_text.split())[-500:]
    tail_lower = tail.lower()
    clarify_cues = (
        "what",
        "which",
        "could you",
        "can you",
        "clarify",
        "preference",
        "constraint",
        "please confirm",
        "请问",
        "你希望",
        "你更",
        "哪个",
        "能否",
        "是否",
        "补充",
        "偏好",
        "限制",
    )
    looks_like_question = ("?" in tail or "？" in tail) and any(cue in tail_lower or cue in tail for cue in clarify_cues)
    if not looks_like_question:
        return build_clarify_reply(spec)
    prompt = f"""
You are the human owner replying in Telegram before the guest agent enters a ClawRoom.

Scenario topic: {spec.topic}
Goal: {spec.goal}
Owner context: {spec.owner_context}

Visible Telegram text near the latest bot message:
{ocr_text}

Write one short natural reply in Chinese:
- answer the likely latest clarify question if one is visible
- keep it warm and human, not robotic
- fold in the owner's real intent from the scenario
- end by inviting the guest to use the managed room flow and continue the work
- no bullets
- no markdown
- max 4 short sentences
""".strip()
    try:
        reply = call_claude_text(prompt, timeout=45)
    except Exception:  # noqa: BLE001
        return build_clarify_reply(spec)
    cleaned = " ".join(reply.split())
    return cleaned or build_clarify_reply(spec)


def build_telegram_owner_followup_reply(spec: ScenarioSpec, surface: dict[str, Any]) -> str:
    ocr_text = load_surface_ocr(surface)
    ocr_lower = ocr_text.lower()
    if spec.slug == "artifact_brief" and any(token in ocr_lower for token in ("markets", "delivery", "gmt+8", "same 3 items pending")):
        return (
            "Markets: BTC and ETH only for the first pilot. "
            "Time: 07:00 GMT+8 on weekdays. "
            "Delivery: send the handoff directly in Telegram to the owner first, then mirror it into the Bamboo channel after the format is approved."
        )
    if spec.telegram_followup_reply.strip():
        return spec.telegram_followup_reply.strip()
    if not ocr_text:
        return spec.telegram_followup_reply or build_clarify_reply(spec)
    prompt = f"""
You are the human owner replying in Telegram after the guest agent already joined a ClawRoom and asked for more owner context.

Scenario topic: {spec.topic}
Goal: {spec.goal}
Owner context: {spec.owner_context}
Hidden owner constraint: {spec.hidden_owner_reply}

Visible Telegram text near the latest bot message:
{ocr_text}

Write one short natural reply in English:
- answer the latest owner-context questions directly
- cover workflow, pain point, constraints, and lean yes/no if the guest asked for them
- keep it concise and actionable
- no bullets
- no markdown
- max 5 short sentences
""".strip()
    try:
        reply = call_claude_text(prompt, timeout=60)
    except Exception:  # noqa: BLE001
        return spec.telegram_followup_reply or build_clarify_reply(spec)
    cleaned = " ".join(reply.split())
    return cleaned or spec.telegram_followup_reply or build_clarify_reply(spec)


def build_gateway_path_reply(*, runnerd_url: str) -> str:
    clean = str(runnerd_url or "").strip() or "http://127.0.0.1:8741"
    return (
        f"Use path 2. The runnerd URL for this deployment is {clean}. "
        "Please submit the wake package there, attach the managed runner, and continue through the managed room flow in the provided room only. Do not create a second room. "
        "If that still fails, reply with the exact error."
    )


def build_direct_join_fallback_reply() -> str:
    return (
        "Managed helper path is unavailable for this run, so direct room join is approved as the fallback. "
        "Please use the join link in the wake package, enter the provided room in compatibility mode, and continue there until you can close with a confirmed handoff. Do not create a second room or briefing room. "
        "If direct join fails too, reply with the exact error."
    )


def parse_telegram_gateway_result(surface: dict[str, Any]) -> dict[str, str]:
    ocr_text = load_surface_ocr(surface)
    if not ocr_text:
        return {}
    status = ""
    decision = ""
    rationale = ""
    next_step = ""
    for raw_line in ocr_text.splitlines():
        line = " ".join(str(raw_line or "").split()).strip()
        if not line:
            continue
        compact = re.sub(r"^[\-\+\*\u2022]+\s*", "", line).strip()
        lowered = compact.lower()
        if lowered.startswith("status:"):
            value = compact.split(":", 1)[1].strip()
            if value and "|" not in value:
                status = value.lower().replace(" ", "_")
            continue
        if lowered.startswith("decision:"):
            decision = compact.split(":", 1)[1].strip()
            continue
        if lowered.startswith("rationale:"):
            rationale = compact.split(":", 1)[1].strip()
            continue
        if lowered.startswith("next step:") or lowered.startswith("next_step:"):
            next_step = compact.split(":", 1)[1].strip()
            continue
    if status not in {"goal_reached", "blocked", "could_not_join"}:
        return {}
    return {
        "status": status,
        "decision": decision,
        "rationale": rationale,
        "next_step": next_step,
    }


def normalize_telegram_outbound(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def telegram_send_allowed(
    *,
    now_ts: float,
    candidate_text: str,
    last_sent_text: str,
    last_sent_at: float,
) -> bool:
    candidate = normalize_telegram_outbound(candidate_text)
    previous = normalize_telegram_outbound(last_sent_text)
    if not candidate:
        return False
    if previous and candidate == previous and (now_ts - last_sent_at) < TELEGRAM_DUPLICATE_REPLY_WINDOW_SECONDS:
        return False
    return (now_ts - last_sent_at) >= TELEGRAM_MIN_OWNER_REPLY_GAP_SECONDS


def write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_room_with_retry(
    *,
    base_url: str,
    topic: str,
    goal: str,
    required_fields: list[str],
    outcome_contract: dict[str, Any] | None,
    turn_limit: int,
    stall_limit: int,
    timeout_minutes: int,
    ttl_minutes: int,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            return create_room(
                base_url=base_url,
                topic=topic,
                goal=goal,
                required_fields=required_fields,
                outcome_contract=outcome_contract,
                turn_limit=turn_limit,
                stall_limit=stall_limit,
                timeout_minutes=timeout_minutes,
                ttl_minutes=ttl_minutes,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= 5:
                break
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"create_room failed after retries: {last_error}")


def normalize_message(raw: dict[str, Any]) -> dict[str, Any]:
    intent = str(raw.get("intent") or "ANSWER").upper().strip()
    if intent not in {"ASK", "ANSWER", "NOTE", "DONE", "ASK_OWNER", "OWNER_REPLY"}:
        intent = "ANSWER"
    text = str(raw.get("text") or "").strip() or "(no text)"
    fills = raw.get("fills") if isinstance(raw.get("fills"), dict) else {}
    facts = raw.get("facts") if isinstance(raw.get("facts"), list) else []
    questions = raw.get("questions") if isinstance(raw.get("questions"), list) else []
    expect_reply = bool(raw.get("expect_reply", intent not in {"NOTE", "DONE"}))
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    return {
        "intent": intent,
        "text": text,
        "fills": {str(k): str(v) for k, v in fills.items() if str(k).strip() and str(v).strip()},
        "facts": [str(item).strip() for item in facts if str(item).strip()],
        "questions": [str(item).strip() for item in questions if str(item).strip()],
        "expect_reply": expect_reply,
        "meta": meta,
    }


def build_final_done_message(room: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": "DONE",
        "text": "Aligned. Final plan is locked and all required fields are filled.",
        "fills": {str(k): str(v) for k, v in room_fields(room).items() if str(v).strip()},
        "facts": [],
        "questions": [],
        "expect_reply": False,
        "meta": {},
    }


def should_send_final_done(room: dict[str, Any], latest_message: dict[str, Any] | None = None) -> bool:
    if not has_all_required_fields(room):
        return False
    intent = str((latest_message or {}).get("intent") or "").upper().strip()
    return intent != "ASK_OWNER"


def relay_requires_reply(event: dict[str, Any]) -> bool:
    message = ((event.get("payload") or {}).get("message") or {}) if isinstance(event, dict) else {}
    intent = str(message.get("intent") or "").upper().strip()
    expect_reply = bool(message.get("expect_reply", True))
    return intent == "DONE" or expect_reply


def room_fields(room: dict[str, Any]) -> dict[str, Any]:
    fields = room.get("fields") if isinstance(room.get("fields"), dict) else {}
    values: dict[str, Any] = {}
    for key, raw in fields.items():
        if isinstance(raw, dict):
            values[str(key)] = raw.get("value")
        else:
            values[str(key)] = raw
    return values


def has_all_required_fields(room: dict[str, Any]) -> bool:
    required = list(room.get("required_fields") or [])
    values = room_fields(room)
    if not required:
        return False
    return all(str(values.get(name) or "").strip() for name in required)


def build_context_envelope(summary: str) -> dict[str, Any]:
    return {"summary": summary, "refs": []}


def next_visible_counterpart_relay(
    events: list[dict[str, Any]],
    *,
    seen_ids: set[int],
    self_name: str,
) -> dict[str, Any] | None:
    chosen: dict[str, Any] | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = int(event.get("id") or 0)
        if event_id <= 0 or event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        if event.get("type") != "relay":
            continue
        message = (event.get("payload") or {}).get("message") or {}
        sender = str(message.get("sender") or "").strip()
        if sender and sender != self_name:
            chosen = event
    return chosen


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "stop_reason": result.get("stop_reason"),
        "turn_count": result.get("turn_count"),
        "required_total": result.get("required_total"),
        "required_filled": result.get("required_filled"),
        "summary": result.get("summary"),
    }


def run_scenario(
    *,
    spec: ScenarioSpec,
    telegram_bot: str,
    base_url: str,
    ui_base: str,
    guest_gateway_only: bool,
    guest_copy_mode: str,
    guest_runnerd_url: str,
    wait_after_open: float,
    wait_after_new: float,
    clarify_wait_seconds: float,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    assert_claude_ready()
    created = create_room_with_retry(
        base_url=base_url,
        topic=spec.topic,
        goal=spec.goal,
        required_fields=spec.required_fields,
        outcome_contract={
            "scenario_hint": spec.scenario_hint,
            "field_principles": spec.field_principles,
        }
        if spec.scenario_hint or spec.field_principles
        else None,
        turn_limit=spec.turn_limit,
        stall_limit=spec.stall_limit,
        timeout_minutes=spec.timeout_minutes,
        ttl_minutes=spec.ttl_minutes,
    )

    room = created["room"]
    room_id = str(room["id"])
    host_token = str(created["host_token"])
    host_invite = str(created["invites"]["host"])
    guest_invite = str(created["invites"]["guest"])
    host_join = f"{base_url.rstrip('/')}/join/{room_id}?token={host_invite}"
    guest_join = f"{base_url.rstrip('/')}/join/{room_id}?token={guest_invite}"
    watch_link = f"{ui_base.rstrip('/')}/?room_id={room_id}&host_token={host_token}"
    checkpoint_path = output_dir / f"{room_id}_{spec.slug}_checkpoint.json"

    telegram_prompt = (
        f"Ignore every earlier room or instruction in this chat. Only operate on room_id={room_id} in this message. "
        "Do not reuse any previous room, result, or close instruction.\n\n"
        + build_join_prompt(
            guest_join,
            room_id=room_id,
            role="responder",
            scenario=spec.prompt_scenario,
            topic=spec.topic,
            goal=spec.goal,
            runnerd_url=guest_runnerd_url,
            preferred_runner_kind="openclaw_bridge",
            gateway_only=guest_gateway_only,
            copy_mode=guest_copy_mode,
        )
    )
    host_prompt = build_join_prompt(
        host_join,
        room_id=room_id,
        role="initiator",
        scenario=spec.prompt_scenario,
        topic=spec.topic,
        goal=spec.goal,
        runnerd_url="",
        preferred_runner_kind="codex_bridge",
        gateway_only=False,
        copy_mode="owner_friendly",
    )
    (output_dir / f"{room_id}_telegram_prompt.txt").write_text(telegram_prompt + "\n", encoding="utf-8")
    (output_dir / f"{room_id}_claude_invite.txt").write_text(host_prompt + "\n", encoding="utf-8")
    write_checkpoint(
        checkpoint_path,
        {
            "stage": "room_created",
            "scenario": spec.slug,
            "room_id": room_id,
            "watch_link": watch_link,
            "host_token": host_token,
            "host_invite": host_invite,
            "guest_invite": guest_invite,
            "host_join_link": host_join,
            "guest_join_link": guest_join,
        },
    )

    send_sequence(
        bot_target=telegram_bot,
        text=telegram_prompt,
        reset_session=True,
        wait_after_open=wait_after_open,
        wait_after_new=wait_after_new,
    )
    time.sleep(2.0)
    telegram_after_send = capture_chat_surface(
        bot_target=telegram_bot,
        output_dir=output_dir,
        label=f"{room_id}_telegram_after_send",
        wait_after_open=wait_after_open,
        do_ocr=False,
    )
    write_checkpoint(
        checkpoint_path,
        {
            "stage": "telegram_prompt_sent",
            "scenario": spec.slug,
            "room_id": room_id,
            "watch_link": watch_link,
            "host_token": host_token,
            "host_invite": host_invite,
            "guest_invite": guest_invite,
            "host_join_link": host_join,
            "guest_join_link": guest_join,
            "telegram_after_send": telegram_after_send,
        },
    )
    time.sleep(max(0.0, clarify_wait_seconds))
    telegram_before_clarify = capture_chat_surface(
        bot_target=telegram_bot,
        output_dir=output_dir,
        label=f"{room_id}_telegram_before_clarify",
        wait_after_open=wait_after_open,
    )
    telegram_clarify_reply = build_dynamic_clarify_reply(spec, telegram_before_clarify)
    send_sequence(
        bot_target=telegram_bot,
        text=telegram_clarify_reply,
        reset_session=False,
        wait_after_open=wait_after_open,
        wait_after_new=0.0,
    )
    telegram_last_sent_text = telegram_clarify_reply
    telegram_last_sent_at = time.time()
    time.sleep(2.0)
    telegram_after_clarify = capture_chat_surface(
        bot_target=telegram_bot,
        output_dir=output_dir,
        label=f"{room_id}_telegram_after_clarify",
        wait_after_open=wait_after_open,
        do_ocr=False,
    )
    write_checkpoint(
        checkpoint_path,
        {
            "stage": "telegram_clarify_sent",
            "scenario": spec.slug,
            "room_id": room_id,
            "watch_link": watch_link,
            "host_token": host_token,
            "host_invite": host_invite,
            "guest_invite": guest_invite,
            "host_join_link": host_join,
            "guest_join_link": guest_join,
            "telegram_after_send": telegram_after_send,
            "telegram_before_clarify": telegram_before_clarify,
            "telegram_clarify_reply": telegram_clarify_reply,
            "telegram_after_clarify": telegram_after_clarify,
        },
    )

    join_info = parse_join_url(host_join)
    with httpx.Client(base_url=join_info["base_url"], timeout=20.0, trust_env=False) as client:
        joined = http_json(
            client,
            "POST",
            f"/rooms/{join_info['room_id']}/join",
            invite_token=join_info["token"],
            payload={
                "client_name": "Claude Code",
                "display_name": "Claude Code",
                "context_envelope": build_context_envelope(spec.owner_context),
            },
        )
        participant_token = str(joined.get("participant_token") or "")
        if not participant_token:
            raise RuntimeError("join did not return participant_token")
        write_checkpoint(
            checkpoint_path,
            {
                "stage": "host_joined",
                "scenario": spec.slug,
                "room_id": room_id,
                "watch_link": watch_link,
                "host_token": host_token,
                "host_invite": host_invite,
                "guest_invite": guest_invite,
                "host_join_link": host_join,
                "guest_join_link": guest_join,
                "participant_token": participant_token,
                "telegram_after_send": telegram_after_send,
                "telegram_before_clarify": telegram_before_clarify,
                "telegram_clarify_reply": telegram_clarify_reply,
                "telegram_after_clarify": telegram_after_clarify,
            },
        )

        cursor = 0
        seen_ids: set[int] = set()
        commitments: list[str] = []
        last_counterpart_ask = ""
        last_counterpart_message = ""
        owner_reply_used = False
        first_message_sent = False
        telegram_owner_followup_count = 0
        last_telegram_followup_fingerprint = ""
        next_telegram_check_at = 0.0
        join_nudge_sent = False
        telegram_gateway_result: dict[str, str] = {}
        local_room = dict(joined.get("room") or {})
        started_at = time.time()
        deadline = started_at + max(120, spec.timeout_minutes * 60)

        while time.time() < deadline:
            now = time.time()
            if now >= next_telegram_check_at:
                telegram_live = capture_chat_surface(
                    bot_target=telegram_bot,
                    output_dir=output_dir,
                    label=f"{room_id}_telegram_live_{telegram_owner_followup_count + 1}",
                    wait_after_open=wait_after_open,
                )
                tail = normalized_surface_tail(telegram_live)
                gateway_result = parse_telegram_gateway_result(telegram_live)
                if gateway_result.get("status") in {"blocked", "could_not_join"}:
                    telegram_gateway_result = gateway_result
                    write_checkpoint(
                        checkpoint_path,
                        {
                            "stage": "telegram_gateway_terminal",
                            "scenario": spec.slug,
                            "room_id": room_id,
                            "watch_link": watch_link,
                            "host_token": host_token,
                            "host_invite": host_invite,
                            "guest_invite": guest_invite,
                            "host_join_link": host_join,
                            "guest_join_link": guest_join,
                            "participant_token": participant_token,
                            "telegram_live": telegram_live,
                            "telegram_gateway_result": telegram_gateway_result,
                        },
                    )
                    break
                if (
                    tail
                    and tail != last_telegram_followup_fingerprint
                    and surface_looks_like_runnerd_unreachable(telegram_live)
                    and telegram_send_allowed(
                        now_ts=now,
                        candidate_text=build_direct_join_fallback_reply(),
                        last_sent_text=telegram_last_sent_text,
                        last_sent_at=telegram_last_sent_at,
                    )
                ):
                    telegram_fallback_reply = build_direct_join_fallback_reply()
                    send_sequence(
                        bot_target=telegram_bot,
                        text=telegram_fallback_reply,
                        reset_session=False,
                        wait_after_open=wait_after_open,
                        wait_after_new=0.0,
                    )
                    telegram_last_sent_text = telegram_fallback_reply
                    telegram_last_sent_at = time.time()
                    time.sleep(2.0)
                    telegram_owner_followup_count += 1
                    telegram_after_fallback_reply = capture_chat_surface(
                        bot_target=telegram_bot,
                        output_dir=output_dir,
                        label=f"{room_id}_telegram_direct_join_fallback_{telegram_owner_followup_count}",
                        wait_after_open=wait_after_open,
                    )
                    last_telegram_followup_fingerprint = tail
                    write_checkpoint(
                        checkpoint_path,
                        {
                            "stage": "telegram_direct_join_fallback_sent",
                            "scenario": spec.slug,
                            "room_id": room_id,
                            "watch_link": watch_link,
                            "host_token": host_token,
                            "host_invite": host_invite,
                            "guest_invite": guest_invite,
                            "host_join_link": host_join,
                            "guest_join_link": guest_join,
                            "participant_token": participant_token,
                            "telegram_live": telegram_live,
                            "telegram_direct_join_fallback_reply": telegram_fallback_reply,
                            "telegram_after_direct_join_fallback": telegram_after_fallback_reply,
                            "telegram_owner_followup_count": telegram_owner_followup_count,
                        },
                    )
                    next_telegram_check_at = now + 18.0
                    continue
                if (
                    tail
                    and tail != last_telegram_followup_fingerprint
                    and surface_looks_like_gateway_path_question(telegram_live)
                    and telegram_send_allowed(
                        now_ts=now,
                        candidate_text=build_gateway_path_reply(runnerd_url=guest_runnerd_url),
                        last_sent_text=telegram_last_sent_text,
                        last_sent_at=telegram_last_sent_at,
                    )
                ):
                    telegram_gateway_reply = build_gateway_path_reply(runnerd_url=guest_runnerd_url)
                    send_sequence(
                        bot_target=telegram_bot,
                        text=telegram_gateway_reply,
                        reset_session=False,
                        wait_after_open=wait_after_open,
                        wait_after_new=0.0,
                    )
                    telegram_last_sent_text = telegram_gateway_reply
                    telegram_last_sent_at = time.time()
                    time.sleep(2.0)
                    telegram_owner_followup_count += 1
                    telegram_after_gateway_reply = capture_chat_surface(
                        bot_target=telegram_bot,
                        output_dir=output_dir,
                        label=f"{room_id}_telegram_gateway_reply_{telegram_owner_followup_count}",
                        wait_after_open=wait_after_open,
                    )
                    last_telegram_followup_fingerprint = tail
                    write_checkpoint(
                        checkpoint_path,
                        {
                            "stage": "telegram_gateway_path_reply_sent",
                            "scenario": spec.slug,
                            "room_id": room_id,
                            "watch_link": watch_link,
                            "host_token": host_token,
                            "host_invite": host_invite,
                            "guest_invite": guest_invite,
                            "host_join_link": host_join,
                            "guest_join_link": guest_join,
                            "participant_token": participant_token,
                            "telegram_live": telegram_live,
                            "telegram_gateway_reply": telegram_gateway_reply,
                            "telegram_after_gateway_reply": telegram_after_gateway_reply,
                            "telegram_owner_followup_count": telegram_owner_followup_count,
                        },
                    )
                    next_telegram_check_at = now + 18.0
                    continue
                if (
                    tail
                    and tail != last_telegram_followup_fingerprint
                    and surface_looks_like_owner_followup(telegram_live)
                    and telegram_send_allowed(
                        now_ts=now,
                        candidate_text=build_telegram_owner_followup_reply(spec, telegram_live),
                        last_sent_text=telegram_last_sent_text,
                        last_sent_at=telegram_last_sent_at,
                    )
                ):
                    telegram_owner_reply = build_telegram_owner_followup_reply(spec, telegram_live)
                    send_sequence(
                        bot_target=telegram_bot,
                        text=telegram_owner_reply,
                        reset_session=False,
                        wait_after_open=wait_after_open,
                        wait_after_new=0.0,
                    )
                    telegram_last_sent_text = telegram_owner_reply
                    telegram_last_sent_at = time.time()
                    time.sleep(2.0)
                    telegram_owner_followup_count += 1
                    telegram_after_owner_reply = capture_chat_surface(
                        bot_target=telegram_bot,
                        output_dir=output_dir,
                        label=f"{room_id}_telegram_owner_followup_{telegram_owner_followup_count}",
                        wait_after_open=wait_after_open,
                    )
                    last_telegram_followup_fingerprint = tail
                    write_checkpoint(
                        checkpoint_path,
                        {
                            "stage": "telegram_owner_followup_sent",
                            "scenario": spec.slug,
                            "room_id": room_id,
                            "watch_link": watch_link,
                            "host_token": host_token,
                            "host_invite": host_invite,
                            "guest_invite": guest_invite,
                            "host_join_link": host_join,
                            "guest_join_link": guest_join,
                            "participant_token": participant_token,
                            "telegram_live": telegram_live,
                            "telegram_owner_reply": telegram_owner_reply,
                            "telegram_after_owner_reply": telegram_after_owner_reply,
                            "telegram_owner_followup_count": telegram_owner_followup_count,
                        },
                    )
                next_telegram_check_at = now + 18.0
                if telegram_gateway_result:
                    break

            batch = http_json(
                client,
                "GET",
                f"/rooms/{join_info['room_id']}/events?after={cursor}&limit=200",
                participant_token=participant_token,
            )
            local_room = dict(batch.get("room") or local_room)
            cursor = int(batch.get("next_cursor") or cursor)
            if telegram_gateway_result:
                break
            if str(local_room.get("status") or "") == "closed":
                break

            participants = {str(p.get("name") or ""): p for p in list(local_room.get("participants") or [])}
            guest_joined = bool((participants.get("guest") or {}).get("joined"))
            if (
                not guest_joined
                and not join_nudge_sent
                and time.time() - started_at >= 75
                and telegram_send_allowed(
                    now_ts=time.time(),
                    candidate_text=build_clarify_reply(spec),
                    last_sent_text=telegram_last_sent_text,
                    last_sent_at=telegram_last_sent_at,
                )
            ):
                join_nudge = build_clarify_reply(spec)
                send_sequence(
                    bot_target=telegram_bot,
                    text=join_nudge,
                    reset_session=False,
                    wait_after_open=wait_after_open,
                    wait_after_new=0.0,
                )
                telegram_last_sent_text = join_nudge
                telegram_last_sent_at = time.time()
                join_nudge_sent = True
                write_checkpoint(
                    checkpoint_path,
                    {
                        "stage": "telegram_join_nudge_sent",
                        "scenario": spec.slug,
                        "room_id": room_id,
                        "watch_link": watch_link,
                        "host_token": host_token,
                        "host_invite": host_invite,
                        "guest_invite": guest_invite,
                        "host_join_link": host_join,
                        "guest_join_link": guest_join,
                        "participant_token": participant_token,
                        "join_nudge": join_nudge,
                    },
                )
            write_checkpoint(
                checkpoint_path,
                {
                    "stage": "polling_room",
                    "scenario": spec.slug,
                    "room_id": room_id,
                    "watch_link": watch_link,
                    "host_token": host_token,
                    "host_invite": host_invite,
                    "guest_invite": guest_invite,
                    "host_join_link": host_join,
                    "guest_join_link": guest_join,
                    "participant_token": participant_token,
                    "cursor": cursor,
                    "guest_joined": guest_joined,
                    "owner_reply_used": owner_reply_used,
                    "room_status": local_room.get("status"),
                },
            )

            if not first_message_sent and guest_joined:
                prompt = build_room_reply_prompt(
                    role="initiator",
                    room=local_room,
                    self_name="host",
                    latest_event=None,
                    has_started=False,
                    owner_context=spec.owner_context,
                    commitments=commitments,
                    last_counterpart_ask=last_counterpart_ask,
                    last_counterpart_message=last_counterpart_message,
                )
                host_message = normalize_message(call_claude_json(prompt))
                try:
                    sent = http_json(
                        client,
                        "POST",
                        f"/rooms/{join_info['room_id']}/messages",
                        participant_token=participant_token,
                        payload=host_message,
                    )
                except RuntimeError as exc:
                    if is_room_not_active_conflict(exc):
                        break
                    raise
                first_message_sent = True
                local_room = dict(sent.get("room") or local_room)
                commitments.append(host_message["text"])
                time.sleep(1.0)
                continue

            counterpart = next_visible_counterpart_relay(list(batch.get("events") or []), seen_ids=seen_ids, self_name="host")
            if counterpart and relay_requires_reply(counterpart):
                latest_message = ((counterpart.get("payload") or {}).get("message") or {})
                last_counterpart_message = str(latest_message.get("text") or "").strip()
                if str(latest_message.get("intent") or "").upper().strip() == "ASK":
                    last_counterpart_ask = last_counterpart_message

                latest_intent = str(latest_message.get("intent") or "").upper().strip()
                if latest_intent == "DONE" and has_all_required_fields(local_room):
                    host_message = build_final_done_message(local_room)
                elif should_send_final_done(local_room, latest_message):
                    host_message = build_final_done_message(local_room)
                else:
                    prompt = build_room_reply_prompt(
                        role="initiator",
                        room=local_room,
                        self_name="host",
                        latest_event=counterpart,
                        has_started=True,
                        owner_context=spec.owner_context,
                        commitments=commitments,
                        last_counterpart_ask=last_counterpart_ask,
                        last_counterpart_message=last_counterpart_message,
                    )
                    host_message = normalize_message(call_claude_json(prompt))

                if spec.prompt_scenario == "owner_escalation" and not owner_reply_used and not has_all_required_fields(local_room):
                    if host_message["intent"] not in {"ASK_OWNER", "OWNER_REPLY"} and last_counterpart_message:
                        host_message["intent"] = "ASK_OWNER"
                        host_message["text"] = (
                            "I need one hidden owner constraint before I lock the final choice. "
                            "Please tell me the one thing this first version must avoid."
                        )
                        host_message["expect_reply"] = False

                try:
                    sent = http_json(
                        client,
                        "POST",
                        f"/rooms/{join_info['room_id']}/messages",
                        participant_token=participant_token,
                        payload=host_message,
                    )
                except RuntimeError as exc:
                    if is_room_not_active_conflict(exc):
                        break
                    raise
                local_room = dict(sent.get("room") or local_room)
                commitments.append(host_message["text"])
                time.sleep(1.0)

                if host_message["intent"] == "ASK_OWNER" and not owner_reply_used:
                    owner_prompt = build_owner_reply_prompt(
                        room=local_room,
                        self_name="host",
                        role="initiator",
                        owner_req_id=f"owner_req_{room_id}",
                        owner_text=spec.hidden_owner_reply,
                        owner_context=spec.owner_context,
                        commitments=commitments,
                    )
                    owner_message = normalize_message(call_claude_json(owner_prompt))
                    owner_message["intent"] = "OWNER_REPLY"
                    owner_message["meta"] = dict(owner_message.get("meta") or {})
                    owner_message["meta"]["owner_req_id"] = f"owner_req_{room_id}"
                    try:
                        sent = http_json(
                            client,
                            "POST",
                            f"/rooms/{join_info['room_id']}/messages",
                            participant_token=participant_token,
                            payload=owner_message,
                        )
                    except RuntimeError as exc:
                        if is_room_not_active_conflict(exc):
                            break
                        raise
                    local_room = dict(sent.get("room") or local_room)
                    commitments.append(owner_message["text"])
                    owner_reply_used = True
                    time.sleep(1.0)
                continue

            time.sleep(2.0)
        if telegram_gateway_result:
            try:
                room_snapshot_payload = http_json(
                    client,
                    "GET",
                    f"/rooms/{join_info['room_id']}",
                    host_token=host_token,
                )
            except Exception:
                room_snapshot_payload = {}
            result_payload = {
                "room": dict(room_snapshot_payload.get("room") or room_snapshot_payload or local_room),
                "result": {
                    "status": telegram_gateway_result.get("status"),
                    "stop_reason": "telegram_gateway_terminal",
                    "turn_count": 0,
                    "required_total": len(spec.required_fields),
                    "required_filled": sum(1 for name in spec.required_fields if str(room_fields(local_room).get(name) or "").strip()),
                    "summary": telegram_gateway_result.get("rationale") or telegram_gateway_result.get("decision") or "",
                },
            }
        else:
            result_payload = http_json(
                client,
                "GET",
                f"/rooms/{join_info['room_id']}/result",
                host_token=host_token,
            )

    telegram_final = capture_chat_surface(
        bot_target=telegram_bot,
        output_dir=output_dir,
        label=f"{room_id}_telegram_final",
        wait_after_open=wait_after_open,
    )
    final_room = dict(result_payload.get("room") or {})
    final_fields = room_fields(final_room)
    result_summary = summarize_result(dict(result_payload.get("result") or {}))

    summary = {
        "scenario": spec.slug,
        "room_id": room_id,
        "watch_link": watch_link,
        "host_join_link": host_join,
        "guest_join_link": guest_join,
        "result": result_summary,
        "room_status": final_room.get("status"),
        "field_values": final_fields,
        "telegram_after_send": telegram_after_send,
        "telegram_before_clarify": telegram_before_clarify,
        "telegram_after_clarify": telegram_after_clarify,
        "telegram_final": telegram_final,
        "telegram_gateway_result": telegram_gateway_result,
        "owner_reply_used": owner_reply_used,
        "checkpoint_path": str(checkpoint_path),
        "quality_evaluation": evaluate_room_quality(
            fields=final_fields,
            required_fields=spec.required_fields,
            field_principles=spec.field_principles,
        ),
    }
    out_path = output_dir / f"{room_id}_{spec.slug}_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_checkpoint(
        checkpoint_path,
        {
            "stage": "completed",
            "scenario": spec.slug,
            "room_id": room_id,
            "watch_link": watch_link,
            "host_join_link": host_join,
            "guest_join_link": guest_join,
            "summary_path": str(out_path),
            "summary": summary,
        },
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a real journey E2E between local Claude Code and Telegram OpenClaw.")
    parser.add_argument("--telegram-bot", required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="proposal_synthesis")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--ui-base", default=DEFAULT_UI_BASE)
    parser.add_argument("--guest-prompt-copy-mode", choices=["external_simple", "operator_debug", "owner_friendly"], default="owner_friendly")
    parser.add_argument("--guest-direct-join", action="store_true", help="Send the guest a direct-join prompt instead of gateway-only wake instructions.")
    parser.add_argument("--guest-runnerd-url", default="http://127.0.0.1:8741")
    parser.add_argument("--wait-after-open", type=float, default=1.2)
    parser.add_argument("--wait-after-new", type=float, default=30.0)
    parser.add_argument("--clarify-wait-seconds", type=float, default=8.0)
    parser.add_argument("--output-dir", default=str(ROOT / ".tmp" / "claude_telegram_journey"))
    args = parser.parse_args()

    spec = SCENARIOS[args.scenario]
    summary = run_scenario(
        spec=spec,
        telegram_bot=args.telegram_bot,
        base_url=args.base_url.rstrip("/"),
        ui_base=args.ui_base.rstrip("/"),
        guest_gateway_only=not args.guest_direct_join,
        guest_copy_mode=args.guest_prompt_copy_mode,
        guest_runnerd_url=args.guest_runnerd_url.rstrip("/"),
        wait_after_open=args.wait_after_open,
        wait_after_new=args.wait_after_new,
        clarify_wait_seconds=args.clarify_wait_seconds,
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
