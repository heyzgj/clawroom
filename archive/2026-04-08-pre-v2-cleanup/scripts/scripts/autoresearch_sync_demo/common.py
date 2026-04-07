from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CLIENT_SRC = ROOT / "packages/client/src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from clawroom_client_core.client import http_json  # noqa: E402

DEFAULT_API_BASE = "http://127.0.0.1:8787"
MAX_REFS = 16


def api_base_url(raw: str | None = None) -> str:
    return (raw or os.getenv("CLAWROOM_API_BASE") or DEFAULT_API_BASE).rstrip("/")


def parse_refs_arg(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        refs = parsed
    elif isinstance(parsed, dict):
        refs = [{"type": "custom", "label": str(key), "value": str(value)} for key, value in parsed.items()]
    else:
        raise ValueError("--refs must be a JSON object or a JSON array")
    if len(refs) > MAX_REFS:
        raise ValueError(f"refs cannot exceed {MAX_REFS} items")
    normalized: list[dict[str, str]] = []
    for item in refs:
        if not isinstance(item, dict):
            raise ValueError("each ref must be an object")
        type_ = str(item.get("type") or "custom").strip().lower()
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not label or not value:
            raise ValueError("each ref requires non-empty label and value")
        normalized.append({"type": type_, "label": label, "value": value})
    return normalized


def parse_fill_pairs(raw_pairs: list[str] | None) -> dict[str, str]:
    fills: dict[str, str] = {}
    for raw in raw_pairs or []:
        if "=" not in raw:
            raise ValueError(f"fill must be key=value, got: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"fill must have non-empty key and value, got: {raw}")
        fills[key] = value
    return fills


def split_assignment_text(raw: str) -> tuple[str, str]:
    focus = ""
    constraints = ""
    chunks = [chunk.strip() for chunk in raw.replace("\n", ";").split(";") if chunk.strip()]
    for chunk in chunks:
        lower = chunk.lower()
        if lower.startswith("focus:"):
            focus = chunk.split(":", 1)[1].strip()
        elif lower.startswith("constraints:"):
            constraints = chunk.split(":", 1)[1].strip()
    if not focus:
        focus = raw.strip()
    return focus, constraints


def request_monitor_result(*, base_url: str, room_id: str, host_token: str) -> dict[str, Any]:
    return http_json(
        "GET",
        f"{api_base_url(base_url)}/rooms/{room_id}/monitor/result?host_token={host_token}",
        timeout=20.0,
    )
