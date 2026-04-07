from __future__ import annotations

import re
from typing import Any


PLACEHOLDER_MARKERS = (
    "tbd",
    "todo",
    "placeholder",
    "unknown",
    "n/a",
    "to decide",
    "needs discussion",
    "待定",
    "待补",
    "待确认",
    "稍后补充",
)

ACTION_MARKERS = (
    "today",
    "tomorrow",
    "this week",
    "next week",
    "deadline",
    "owner",
    "assign",
    "schedule",
    "send",
    "review",
    "approve",
    "今天",
    "本周",
    "下周",
    "截止",
    "指派",
    "安排",
    "发送",
    "复盘",
    "批准",
)

ACTION_VERBS = (
    "confirm",
    "assign",
    "schedule",
    "send",
    "review",
    "approve",
    "launch",
    "pilot",
    "ship",
    "start",
    "prepare",
    "decide",
    "draft",
    "share",
    "update",
    "confirm",
    "安排",
    "确认",
    "发送",
    "评审",
    "批准",
    "启动",
    "准备",
    "决定",
    "同步",
    "更新",
)

METRIC_MARKERS = (
    "%",
    "metric",
    "measure",
    "target",
    "within",
    "day",
    "days",
    "week",
    "weeks",
    "month",
    "months",
    "小时",
    "天",
    "周",
    "月",
    "完成率",
    "指标",
    "目标",
)

JARGON_MARKERS = (
    "runnerd",
    "room_id",
    "participant_token",
    "mutual_done",
    "goal_done",
    "compatibility mode",
    "compatibility-mode",
    "ptok_",
    "inv_",
    "json",
    "payload",
    "wake package",
    "api join",
    "required_fields",
    "outcome_contract",
    "counterpart",
    "ask_owner",
    "owner_reply",
    "skill.md",
    "post /rooms",
    "watch here",
    "join link",
    "room ready",
)

HEDGE_MARKERS = (
    "maybe",
    "might",
    "could",
    "probably",
    "likely",
    "lean",
    "tentative",
    "depends",
    "further discussion",
    "needs discussion",
    "待讨论",
    "可能",
    "也许",
)

PASS_MARKERS = (
    "continue if",
    "continue signal",
    "ship if",
    "go if",
    "pass if",
    "success if",
    "green if",
    "keep if",
    "keep going if",
    "继续条件",
    "通过条件",
)

FAIL_MARKERS = (
    "pause if",
    "pause signal",
    "stop if",
    "fail if",
    "rollback if",
    "reject if",
    "hold if",
    "red if",
    "暂停条件",
    "失败条件",
    "停止条件",
)

STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "no",
    "not",
    "of",
    "on",
    "or",
    "our",
    "the",
    "their",
    "this",
    "to",
    "we",
    "with",
    "week",
    "weeks",
    "day",
    "days",
    "today",
    "tomorrow",
    "this week",
    "next week",
    "run",
    "pilot",
    "recommended",
    "option",
}


def _normalize_fields(fields: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value or "").strip() for key, value in (fields or {}).items()}


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return not value or any(marker in lowered or marker in value for marker in PLACEHOLDER_MARKERS)


def _has_ranked_list(value: str) -> bool:
    normalized = value.replace("[", " ").replace("]", " ")
    has_first = bool(re.search(r"(?:^|[\n;])\s*(?:1\.|option 1\b)", normalized, re.IGNORECASE)) or bool(
        re.search(r"\b1\.\s+\S", normalized, re.IGNORECASE)
    )
    has_second = bool(re.search(r"(?:^|[\n;])\s*(?:2\.|option 2\b)", normalized, re.IGNORECASE)) or bool(
        re.search(r"\b2\.\s+\S", normalized, re.IGNORECASE)
    )
    return has_first and has_second


def _has_metric_or_timeline(value: str) -> bool:
    lowered = value.lower()
    return bool(re.search(r"\d", value)) and any(marker in lowered or marker in value for marker in METRIC_MARKERS)


def _has_action_and_timing(value: str) -> bool:
    lowered = value.lower()
    has_action = any(marker in lowered or marker in value for marker in ACTION_MARKERS) or any(
        verb in lowered or verb in value for verb in ACTION_VERBS
    )
    has_timing = any(marker in lowered or marker in value for marker in ACTION_MARKERS) or bool(
        re.search(r"\b\d{4}-\d{2}-\d{2}\b|\b\d+\s*(?:day|days|week|weeks|month|months|min|mins|minute|minutes)\b", lowered)
    )
    return len(value) >= 24 and has_action and has_timing


def _has_pass_fail_signal(value: str) -> bool:
    lowered = value.lower()
    if "pass signal" in lowered or "fail signal" in lowered or "pass/fail" in lowered:
        return True
    has_pass = any(marker in lowered for marker in PASS_MARKERS)
    has_fail = any(marker in lowered for marker in FAIL_MARKERS)
    if has_pass and has_fail:
        return True
    has_conditional_continue = "if " in lowered and any(
        marker in lowered for marker in ("continue", "keep going", "stay on track", "go ahead", "proceed")
    )
    has_conditional_pause = "if " in lowered and any(
        marker in lowered for marker in ("pause", "stop", "hold", "escalate", "intervene")
    )
    return has_conditional_continue and has_conditional_pause


def _uses_owner_friendly_language(value: str) -> bool:
    lowered = value.lower()
    return not any(marker in lowered for marker in JARGON_MARKERS)


def _is_confident_and_final(value: str) -> bool:
    lowered = value.lower()
    return not any(marker in lowered for marker in HEDGE_MARKERS)


def _tokenize_content(value: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 3 and token not in STOP_WORDS
    }
    return tokens


def _extract_ranked_items(value: str) -> list[str]:
    text = " ".join(value.split())
    matches = list(re.finditer(r"(?:^|\s)(\d+)\.\s+", text))
    if len(matches) < 2:
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        numbered = [line for line in lines if re.match(r"^\d+\.\s+", line)]
        if len(numbered) >= 2:
            return [re.sub(r"^\d+\.\s*", "", line).strip() for line in numbered]
        return []

    items: list[str] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        items.append(text[start:end].strip(" -;"))
    return items


def _extract_first_ranked_option(value: str) -> str:
    items = _extract_ranked_items(value)
    return items[0] if items else ""


def _decision_matches_top_option(decision: str, ranked_options: str) -> bool:
    first_option = _extract_first_ranked_option(ranked_options)
    if not decision or not first_option:
        return False

    decision_lower = decision.lower()
    option_lower = first_option.lower()
    if decision_lower in option_lower or option_lower in decision_lower:
        return True

    decision_tokens = _tokenize_content(decision)
    option_tokens = _tokenize_content(first_option)
    if not decision_tokens or not option_tokens:
        return False

    overlap = decision_tokens & option_tokens
    shared_count = len(overlap)
    smallest = min(len(decision_tokens), len(option_tokens))
    return shared_count >= 2 and (shared_count / smallest) >= 0.4


def _passes_principle(value: str, principle: str) -> tuple[bool, list[str]]:
    if not value:
        return False, ["missing_value"]

    lowered = principle.lower()
    checks: list[str] = []
    passed = True

    if any(token in lowered for token in ("specific", "actionable", "concrete", "must name")):
        checks.append("specific_content")
        passed = passed and len(value) >= 20 and not _is_placeholder(value) and _is_confident_and_final(value)

    if "2 options" in lowered or "two options" in lowered or "recommendation first" in lowered or "list at least" in lowered:
        checks.append("ranked_options")
        passed = passed and _has_ranked_list(value)

    if any(token in lowered for token in ("metric", "measurable", "timeline", "deadline")):
        checks.append("metric_or_timeline")
        passed = passed and _has_metric_or_timeline(value)

    if any(
        token in lowered
        for token in ("owner can take", "owner action", "this week", "next step", "next_steps", "deadline")
    ):
        checks.append("actionable_owner_handoff")
        passed = passed and _has_action_and_timing(value)

    if any(
        token in lowered
        for token in (
            "pass/fail signal",
            "pass signal",
            "fail signal",
            "continue or pause",
            "continue/pause",
            "pause threshold",
            "continue threshold",
        )
    ):
        checks.append("pass_fail_signal")
        passed = passed and _has_pass_fail_signal(value)

    if any(
        token in lowered
        for token in ("owner-facing", "owner facing", "owner language", "non-technical", "avoid jargon", "forward unchanged")
    ):
        checks.append("owner_friendly_language")
        passed = passed and _uses_owner_friendly_language(value)

    if any(token in lowered for token in ("forward unchanged", "forwardable", "forward unchanged to a teammate")):
        checks.append("forwardable_packet")
        passed = passed and _uses_owner_friendly_language(value) and _is_confident_and_final(value)

    if not checks:
        checks.append("non_placeholder")
        passed = not _is_placeholder(value)

    return passed, checks


def evaluate_room_quality(
    *,
    fields: dict[str, Any],
    required_fields: list[str],
    field_principles: dict[str, str] | None = None,
) -> dict[str, Any]:
    normalized_fields = _normalize_fields(fields)
    required = [str(item).strip() for item in required_fields if str(item).strip()]
    principles = {str(key).strip(): str(value).strip() for key, value in (field_principles or {}).items() if str(key).strip() and str(value).strip()}

    missing_fields = [field for field in required if not normalized_fields.get(field)]
    placeholder_fields = [field for field in required if _is_placeholder(normalized_fields.get(field, ""))]
    truncated_fields = [field for field in required if len(normalized_fields.get(field, "")) >= 1900]

    principle_checks: dict[str, dict[str, Any]] = {}
    for field_name, principle in principles.items():
        passed, matched_checks = _passes_principle(normalized_fields.get(field_name, ""), principle)
        principle_checks[field_name] = {
            "passed": passed,
            "principle": principle,
            "matched_checks": matched_checks,
        }

    cross_field_checks: dict[str, dict[str, Any]] = {}
    decision = normalized_fields.get("decision", "")
    ranked_options = normalized_fields.get("ranked_options", "")
    if decision and ranked_options:
        cross_field_checks["decision_matches_top_option"] = {
            "passed": _decision_matches_top_option(decision, ranked_options),
            "fields": ["decision", "ranked_options"],
        }

    checks = {
        "fields_complete": not missing_fields,
        "fields_not_placeholder": not placeholder_fields,
        "fields_not_truncated": not truncated_fields,
        "principles_passed": all(item["passed"] for item in principle_checks.values()),
        "cross_field_consistent": all(item["passed"] for item in cross_field_checks.values()),
    }

    return {
        "usable": all(checks.values()),
        "checks": checks,
        "details": {
            "missing_fields": missing_fields,
            "placeholder_fields": placeholder_fields,
            "truncated_fields": truncated_fields,
            "principle_checks": principle_checks,
            "cross_field_checks": cross_field_checks,
        },
    }
