#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def is_matching_telegram_certified_record(
    record: dict[str, Any],
    *,
    host_bot: str,
    guest_bot: str,
    execution_mode: str,
    runner_certification: str,
    path_family: str,
) -> bool:
    return (
        record.get("host_bot") == host_bot
        and record.get("guest_bot") == guest_bot
        and bool(record.get("product_owned"))
        and record.get("execution_mode") == execution_mode
        and record.get("runner_certification") == runner_certification
        and (not path_family or record.get("path_family") == path_family)
    )


def trailing(records: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0:
        return []
    return records[-count:]


def count_outcomes(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        key = str(record.get("outcome_class") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def evaluate(
    history: list[dict[str, Any]],
    *,
    host_bot: str,
    guest_bot: str,
    execution_mode: str,
    runner_certification: str,
    path_family: str,
    window: int,
    min_owner_escalation_successes: int,
) -> dict[str, Any]:
    matching = [
        record
        for record in history
        if is_matching_telegram_certified_record(
            record,
            host_bot=host_bot,
            guest_bot=guest_bot,
            execution_mode=execution_mode,
            runner_certification=runner_certification,
            path_family=path_family,
        )
    ]
    latest = trailing(matching, window)
    silent_failures = sum(1 for record in latest if bool(record.get("silent_failure")))
    failures = sum(1 for record in latest if not bool(record.get("pass")))
    successes = sum(1 for record in latest if bool(record.get("pass")))
    owner_escalation_successes = sum(
        1
        for record in latest
        if bool(record.get("pass"))
        and str(record.get("scenario") or "") == "owner_escalation"
    )
    gate = (
        len(latest) >= window
        and silent_failures == 0
        and failures == 0
        and successes == window
        and owner_escalation_successes >= min_owner_escalation_successes
    )
    return {
        "history_records": len(history),
        "matching_records": len(matching),
        "window": len(latest),
        "host_bot": host_bot,
        "guest_bot": guest_bot,
        "execution_mode": execution_mode,
        "runner_certification": runner_certification,
        "path_family": path_family,
        "owner_escalation_minimum": min_owner_escalation_successes,
        "gate_pass": gate,
        "latest_window": {
            "successes": successes,
            "failures": failures,
            "silent_failures": silent_failures,
            "owner_escalation_successes": owner_escalation_successes,
            "outcome_counts": count_outcomes(latest),
        },
        "latest_rooms": [
            {
                "timestamp": record.get("timestamp"),
                "scenario": record.get("scenario"),
                "pass": bool(record.get("pass")),
                "outcome_class": record.get("outcome_class"),
                "room_id": record.get("room_id"),
            }
            for record in latest
        ],
    }


def render_text(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"history_records={summary['history_records']}",
            f"matching_records={summary['matching_records']}",
            f"window={summary['window']}",
            f"host_bot={summary['host_bot']}",
            f"guest_bot={summary['guest_bot']}",
            f"execution_mode={summary['execution_mode']}",
            f"runner_certification={summary['runner_certification']}",
            f"path_family={summary['path_family']}",
            f"owner_escalation_minimum={summary['owner_escalation_minimum']}",
            f"gate_pass={str(summary['gate_pass']).lower()}",
            "latest_window=" + json.dumps(summary["latest_window"], ensure_ascii=False, sort_keys=True),
            "latest_rooms=" + json.dumps(summary["latest_rooms"], ensure_ascii=False),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Telegram certified-path DoD for a specific bot pair.")
    parser.add_argument(
        "--history-path",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "progress" / "TELEGRAM_E2E_HISTORY.jsonl"),
    )
    parser.add_argument("--host-bot", default="@singularitygz_bot")
    parser.add_argument("--guest-bot", default="@link_clawd_bot")
    parser.add_argument("--execution-mode", default="managed_attached")
    parser.add_argument("--runner-certification", default="certified")
    parser.add_argument("--path-family", default="")
    parser.add_argument("--window", type=int, default=5)
    parser.add_argument("--min-owner-escalation-successes", type=int, default=3)
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    history = load_history(Path(args.history_path))
    summary = evaluate(
        history,
        host_bot=args.host_bot,
        guest_bot=args.guest_bot,
        execution_mode=args.execution_mode,
        runner_certification=args.runner_certification,
        path_family=args.path_family,
        window=args.window,
        min_owner_escalation_successes=args.min_owner_escalation_successes,
    )
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_text(summary))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[evaluate-telegram-certified-path] error: {exc}", file=sys.stderr)
        raise
