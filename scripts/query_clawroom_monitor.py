#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import urlencode

import httpx


def resolve_admin_token(cli_token: str | None, environ: dict[str, str] | None = None) -> str:
    env = environ or os.environ
    return str(
        (cli_token or "").strip()
        or str(env.get("MONITOR_ADMIN_TOKEN") or "").strip()
        or str(env.get("CLAWROOM_MONITOR_ADMIN_TOKEN") or "").strip()
    )


def build_monitor_url(base_url: str, *, view: str, limit: int, text_format: bool) -> str:
    endpoint = "/monitor/summary" if view == "summary" else "/monitor/overview"
    params: dict[str, str | int] = {"limit": max(1, int(limit))}
    if text_format and view == "summary":
        params["format"] = "text"
    query = urlencode(params)
    return f"{base_url.rstrip('/')}{endpoint}?{query}"


def fetch_monitor_payload(*, base_url: str, admin_token: str, view: str, limit: int, text_format: bool) -> str | dict[str, Any]:
    url = build_monitor_url(base_url, view=view, limit=limit, text_format=text_format)
    with httpx.Client(timeout=20.0, trust_env=False) as client:
        resp = client.get(url, headers={"X-Monitor-Token": admin_token})
    if resp.status_code >= 400:
        body = resp.text[:500]
        try:
            parsed = resp.json()
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and str(parsed.get("error") or "").strip() == "capacity_exhausted":
            subsystem = str(parsed.get("subsystem") or "").strip()
            detail = str(parsed.get("detail") or parsed.get("message") or "").strip()
            raise RuntimeError(
                "monitor request failed "
                f"status={resp.status_code} capacity_exhausted"
                + (f" subsystem={subsystem}" if subsystem else "")
                + (f" detail={detail}" if detail else "")
            )
        raise RuntimeError(f"monitor request failed status={resp.status_code} body={body}")
    if text_format and view == "summary":
        return resp.text
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Query ClawRoom monitor APIs for operator/agent-friendly status.")
    parser.add_argument("--base-url", default="https://api.clawroom.cc")
    parser.add_argument("--admin-token", default="", help="Monitor admin token; falls back to MONITOR_ADMIN_TOKEN env")
    parser.add_argument("--view", choices=["summary", "overview"], default="summary")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    admin_token = resolve_admin_token(args.admin_token)
    if not admin_token:
        raise SystemExit("monitor admin token required via --admin-token or MONITOR_ADMIN_TOKEN env")

    payload = fetch_monitor_payload(
        base_url=args.base_url,
        admin_token=admin_token,
        view=args.view,
        limit=args.limit,
        text_format=args.format == "text",
    )
    if isinstance(payload, str):
        print(payload.rstrip())
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[query-clawroom-monitor] error: {exc}", file=sys.stderr)
        raise
