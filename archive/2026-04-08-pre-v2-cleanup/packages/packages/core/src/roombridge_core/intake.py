from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, model_validator


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_NON_SLUG_RE = re.compile(r"[^a-z0-9_]+")


class IntakeResolveIn(BaseModel):
    owner_request: str = Field(..., min_length=1, max_length=4000)
    owner_reply: str | None = Field(default=None, max_length=4000)
    counterpart_hint: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _normalize(self) -> "IntakeResolveIn":
        self.owner_request = self.owner_request.strip()
        if self.owner_reply is not None:
            self.owner_reply = self.owner_reply.strip() or None
        if self.counterpart_hint is not None:
            self.counterpart_hint = self.counterpart_hint.strip() or None
        return self


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _has_url(text: str) -> bool:
    return bool(_URL_RE.search(text))


def _normalized(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _keyword_score(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for item in keywords if item and item in text)


def _slugify_counterpart_hint(value: str) -> str:
    raw = _normalized(value).lstrip("@")
    if not raw:
        return ""
    slug = _NON_SLUG_RE.sub("_", raw).strip("_")
    if not slug or slug == "host":
        return ""
    return slug[:60]


def _infer_counterpart_slot(*, combined_text: str, counterpart_hint: str | None) -> str:
    hint = counterpart_hint or ""
    slug = _slugify_counterpart_hint(hint)
    if slug:
        return slug
    normalized_text = _normalized(combined_text)
    if any(marker in normalized_text for marker in ("another openclaw", "另一个 openclaw", "另一个openclaw")):
        return "counterpart_openclaw"
    if any(marker in normalized_text for marker in ("another agent", "另一个 agent", "另一个agent")):
        return "peer_agent"
    return "counterpart"


def _infer_scenario_key(text: str) -> str:
    scores = {
        "feedback_prioritization": _keyword_score(
            text,
            ("feedback", "priority", "prioritize", "用户反馈", "优先级", "先做哪类反馈", "优先做"),
        ),
        "weekly_content_plan": _keyword_score(
            text,
            ("content", "calendar", "social", "post", "小红书", "内容", "内容日历", "下周内容", "发什么"),
        ),
        "beta_launch_readiness": _keyword_score(
            text,
            ("beta", "launch", "ship", "release", "上线", "发布", "能不能发", "ship this"),
        ),
        "cross_role_alignment": _keyword_score(
            text,
            ("需求文档", "prd", "requirement", "product", "研发", "运营", "align", "对齐", "产品经理"),
        ),
        "founder_persona_sync": _keyword_score(
            text,
            ("创始人", "persona", "人设", "海外社媒", "小红书账号", "founder", "voice"),
        ),
        "task_okr_sync": _keyword_score(
            text,
            ("okr", "工作计划", "任务拆解", "认领", "同步", "进度", "owner_actions", "执行"),
        ),
    }
    best = max(scores.items(), key=lambda item: item[1])
    return best[0] if best[1] > 0 else "generic_decision"


def _scenario_shape(key: str, lang: str) -> dict[str, Any]:
    zh = lang == "zh"
    if key == "feedback_prioritization":
        return {
            "topic": "用户反馈优先级排序" if zh else "User feedback prioritization",
            "goal": "一起判断先做哪类用户反馈，并带回优先级结论、理由和第一周实验方案。" if zh else "Decide which user feedback to prioritize first and return a priority call, rationale, and a week-one experiment plan.",
            "required_fields": ["priority_call", "rationale", "experiment_plan"],
            "field_principles": {
                "priority_call": "Must name the first feedback category to tackle.",
                "rationale": "Must explain why that category should come first in concrete terms.",
                "experiment_plan": "Must include one action this week and a clear pass/fail signal.",
            },
        }
    if key == "weekly_content_plan":
        return {
            "topic": "下周内容规划" if zh else "Weekly content planning",
            "goal": "一起排出下周内容安排，并带回内容日历、核心角度和接下来的动作。" if zh else "Plan next week's content and return a content calendar, core angles, and next steps.",
            "required_fields": ["content_plan", "core_angles", "next_steps"],
            "field_principles": {
                "content_plan": "Must outline the actual posts or deliverables for the week.",
                "core_angles": "Must explain the main angle or hook for each item.",
                "next_steps": "Must include one action the owner can take this week.",
            },
        }
    if key == "beta_launch_readiness":
        return {
            "topic": "Beta 发布判断" if zh else "Beta launch readiness",
            "goal": "一起判断这个 beta 现在能不能发，并带回发布结论、主要风险和下一步。" if zh else "Judge whether the beta is ready to launch and return a launch call, top risks, and next steps.",
            "required_fields": ["launch_decision", "blocking_risks", "next_steps"],
            "field_principles": {
                "launch_decision": "Must be a concrete launch or no-launch call.",
                "blocking_risks": "Must name the risks that actually matter for launch.",
                "next_steps": "Must include one concrete next action.",
            },
        }
    if key == "cross_role_alignment":
        return {
            "topic": "跨角色对齐" if zh else "Cross-role alignment",
            "goal": "一起对齐产品、研发和运营的理解，并带回共同理解、待澄清问题和 owner 需要处理的动作。" if zh else "Align product, engineering, and ops and return shared understanding, open questions, and owner actions.",
            "required_fields": ["shared_understanding", "open_questions", "owner_actions"],
            "field_principles": {
                "shared_understanding": "Must summarize what the teams are actually trying to do.",
                "open_questions": "Must list the questions that still need a human answer.",
                "owner_actions": "Must include concrete actions for the owner.",
            },
        }
    if key == "founder_persona_sync":
        return {
            "topic": "创始人人设周同步" if zh else "Founder persona weekly sync",
            "goal": "一起对齐本周创始人人设的内容方向，并带回本周主题、可复用素材和需要避免的表达。" if zh else "Align the founder persona for the week and return themes, reusable assets, and do-not-say guidance.",
            "required_fields": ["this_week_themes", "shared_assets", "do_not_say"],
            "field_principles": {
                "this_week_themes": "Must name the concrete themes for this week.",
                "shared_assets": "Must list reusable assets both channels can use.",
                "do_not_say": "Must list phrases or positions to avoid.",
            },
        }
    if key == "task_okr_sync":
        return {
            "topic": "任务拆解与 OKR 同步" if zh else "Task and OKR sync",
            "goal": "一起把任务拆解同步清楚，并带回工作计划、负责人和检查指标。" if zh else "Align task breakdown and OKR progress and return workplan, owners, and check-in metrics.",
            "required_fields": ["workplan", "owners", "check_in_metric"],
            "field_principles": {
                "workplan": "Must list the concrete work to be done next.",
                "owners": "Must map work items to owners.",
                "check_in_metric": "Must define how progress will be checked.",
            },
        }
    return {
        "topic": "协作决策" if zh else "Collaborative decision",
        "goal": "一起把问题收敛成一个可执行结论，并带回决定、理由和下一步。" if zh else "Converge on an executable answer and return a decision, rationale, and next steps.",
        "required_fields": ["decision", "rationale", "next_steps"],
        "field_principles": {},
    }


def _needs_source_material(scenario_key: str, combined_text: str) -> bool:
    if scenario_key != "cross_role_alignment":
        return False
    if _has_url(combined_text):
        return False
    if len(combined_text.strip()) >= 180:
        return False
    return True


def _build_question(scenario_key: str, lang: str, draft: dict[str, Any], needs_source: bool) -> str:
    topic = draft["topic"]
    fields = draft["required_fields"]
    if lang == "zh":
        if needs_source:
            return f"我可以开一个房间来做{topic}，并带回{fields[0]}、{fields[1]}和{fields[2]}。把需求文档贴给我，或者发链接，我就继续。"
        if scenario_key == "feedback_prioritization":
            return "我可以开一个房间来排反馈优先级，并带回优先级结论、理由和第一周实验方案。要我按这个形状开吗？"
        if scenario_key == "weekly_content_plan":
            return "我可以开一个房间来排下周内容，并带回内容日历、核心角度和下一步。要我按这个形状开吗？"
        if scenario_key == "beta_launch_readiness":
            return "我可以开一个房间来判断这个 beta 能不能发，并带回发布结论、主要风险和下一步。要我按这个形状开吗？"
        if scenario_key == "founder_persona_sync":
            return "我可以开一个房间来做创始人人设周同步，并带回本周主题、可复用素材和需要避免的表达。要我按这个形状开吗？"
        if scenario_key == "task_okr_sync":
            return "我可以开一个房间来同步任务拆解和 OKR，并带回工作计划、负责人和检查指标。要我按这个形状开吗？"
        return f"我可以开一个房间来处理{topic}，并带回{fields[0]}、{fields[1]}和{fields[2]}。要我按这个形状开吗？"
    if needs_source:
        return f"I can open a room for {topic} and bring back {fields[0]}, {fields[1]}, and {fields[2]}. Paste the requirement doc or send the link, and I will continue."
    return f"I can open a room for {topic} and bring back {fields[0]}, {fields[1]}, and {fields[2]}. Want me to proceed with that shape?"


def _build_clarify_guidance(
    scenario_key: str,
    lang: str,
    blockers: list[str],
    draft: dict[str, Any],
    needs_source: bool,
) -> dict[str, Any] | None:
    if not blockers:
        return None
    fields = draft["required_fields"]
    ask_for: list[str] = []
    blocker_details: list[dict[str, str]] = []
    if "owner_confirmation" in blockers:
        ask_for.append("confirmation")
        blocker_details.append(
            {
                "code": "owner_confirmation",
                "reason": "Need one explicit owner reply before creating the room.",
            }
        )
    if "source_material" in blockers:
        ask_for.append("source_material")
        blocker_details.append(
            {
                "code": "source_material",
                "reason": "Need the requirement doc or source material to avoid guessing the real work.",
            }
        )
    return {
        "style": "brief_human",
        "language": lang,
        "question_goal": (
            f"Confirm the room shape for {draft['topic']} and unblock creation."
            if lang == "en"
            else f"确认{draft['topic']}的房间形状，并解除开房阻塞。"
        ),
        "ask_for": ask_for,
        "blockers": blocker_details,
        "suggested_outcomes": fields,
        "example_question": _build_question(scenario_key, lang, draft, needs_source),
        "do_not_copy_verbatim": True,
    }


def resolve_intake(payload: IntakeResolveIn) -> dict[str, Any]:
    owner_request = payload.owner_request
    owner_reply = payload.owner_reply or ""
    combined_text = "\n".join(part for part in [owner_request, owner_reply, payload.counterpart_hint or ""] if part).strip()
    normalized = _normalized(combined_text)
    lang = "zh" if _contains_cjk(combined_text) else "en"
    scenario_key = _infer_scenario_key(normalized)
    shape = _scenario_shape(scenario_key, lang)
    needs_source = _needs_source_material(scenario_key, combined_text)
    counterpart_slot = _infer_counterpart_slot(combined_text=combined_text, counterpart_hint=payload.counterpart_hint)

    blockers: list[str] = []
    if not owner_reply:
        blockers.append("owner_confirmation")
    if needs_source:
        blockers.append("source_material")

    status = "ready" if not blockers else "input_required"
    guidance = _build_clarify_guidance(scenario_key, lang, blockers, shape, needs_source)
    question = guidance["example_question"] if guidance else None
    draft_payload = {
        "topic": shape["topic"],
        "goal": shape["goal"],
        "participants": ["host", counterpart_slot],
        "required_fields": list(shape["required_fields"]),
        "outcome_contract": {
            "field_principles": dict(shape["field_principles"]),
        },
    }

    return {
        "status": status,
        "ready_to_create": status == "ready",
        "draft_payload": draft_payload,
        "missing_blockers": blockers,
        "clarify_guidance": guidance,
        "one_question": question,
        "one_question_mode": "example_only" if question else None,
        "soft_notes": [
            "Use reasonable defaults for minor room config.",
            "Only blockers should stop room creation.",
            "Rewrite the clarify naturally instead of copying the example verbatim.",
        ],
        "inferred": {
            "scenario_key": scenario_key,
            "language": lang,
            "counterpart_slot": counterpart_slot,
        },
    }
