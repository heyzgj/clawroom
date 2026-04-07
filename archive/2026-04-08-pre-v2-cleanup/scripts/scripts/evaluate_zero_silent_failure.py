#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

CURRENT_FOUNDATION_CONTRACT_VERSION = "foundation-certified-v1"
CURRENT_PATH_FAMILIES = {
    "runnerd_gateway_local_v1",
    "telegram_helper_submitted_runnerd_v1",
    "telegram_only_cross_owner_v1",
    "telegram_gateway_owner_forward_v1",
    "bridge_pair_direct_v1",
}
CURRENT_PATH_FAMILY_EPOCHS = {
    "runnerd_gateway_local_v1": "2026-03-11T04:27:09.929885+00:00",
    "telegram_helper_submitted_runnerd_v1": "2026-03-11T07:29:42.403642+00:00",
    "telegram_only_cross_owner_v1": "2026-03-14T14:45:51.729000+00:00",
    "telegram_gateway_owner_forward_v1": "2026-03-11T07:29:42.403642+00:00",
    "bridge_pair_direct_v1": "2026-03-10T04:43:13.995279+00:00",
}


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def is_product_owned(record: dict[str, Any]) -> bool:
    return bool(record.get("product_owned")) or bool(record.get("last_live_product_owned"))


def derive_path_family(record: dict[str, Any]) -> str | None:
    explicit = str(record.get("path_family") or "").strip()
    if explicit:
        return explicit
    scenario = str(record.get("scenario") or "").strip()
    host_bot = str(record.get("host_bot") or "").strip()
    guest_bot = str(record.get("guest_bot") or "").strip()
    helper_participants = list(record.get("helper_submitted_participants") or [])
    if helper_participants:
        return "telegram_helper_submitted_runnerd_v1"
    if scenario == "runnerd_gateway_local" and host_bot == "runnerd-openclaw-gateway" and guest_bot == "runnerd-codex-gateway":
        return "runnerd_gateway_local_v1"
    if scenario == "certified_local" and host_bot == "local-openclaw-bridge" and guest_bot == "local-codex-bridge":
        return "bridge_pair_direct_v1"
    if (
        host_bot == "@singularitygz_bot"
        and guest_bot == "@link_clawd_bot"
        and float(record.get("wait_after_new_seconds") or 0.0) >= 30.0
    ):
        return "telegram_only_cross_owner_v1"
    return None


def is_current_contract_record(record: dict[str, Any]) -> bool:
    explicit = str(record.get("foundation_contract_version") or "").strip()
    if explicit:
        return explicit == CURRENT_FOUNDATION_CONTRACT_VERSION
    path_family = derive_path_family(record)
    if path_family not in CURRENT_PATH_FAMILIES:
        return False
    epoch = CURRENT_PATH_FAMILY_EPOCHS.get(path_family)
    timestamp = str(record.get("timestamp") or "").strip()
    if epoch and timestamp and timestamp < epoch:
        return False
    return True


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
    history: list[dict[str, Any]], *, e2e_window: int, certified_window: int, current_contract_only: bool = True
) -> dict[str, Any]:
    scoped_history = [record for record in history if is_current_contract_record(record)] if current_contract_only else list(history)
    product_owned = [record for record in scoped_history if is_product_owned(record)]
    latest_product_owned = trailing(product_owned, e2e_window)
    latest_certified = trailing(product_owned, certified_window)

    product_silent_failures = sum(1 for record in latest_product_owned if bool(record.get("silent_failure")))
    product_takeovers = sum(1 for record in latest_product_owned if str(record.get("outcome_class") or "") == "takeover_required")
    product_successes = sum(1 for record in latest_product_owned if bool(record.get("pass")))
    product_unclassified = sum(1 for record in latest_product_owned if str(record.get("outcome_class") or "") == "failed_unclassified")

    certified_silent_failures = sum(1 for record in latest_certified if bool(record.get("silent_failure")))
    certified_successes = sum(1 for record in latest_certified if bool(record.get("pass")))

    product_gate = len(latest_product_owned) >= e2e_window and product_silent_failures == 0 and product_unclassified == 0
    certified_gate = len(latest_certified) >= certified_window and certified_silent_failures == 0 and certified_successes >= max(0, certified_window - 1)

    return {
        "history_records": len(history),
        "current_contract_records": len(scoped_history),
        "product_owned_records": len(product_owned),
        "latest_product_owned_window": len(latest_product_owned),
        "latest_certified_window": len(latest_certified),
        "current_contract_only": current_contract_only,
        "foundation_contract_version": CURRENT_FOUNDATION_CONTRACT_VERSION,
        "product_owned_gate_pass": product_gate,
        "certified_runtime_gate_pass": certified_gate,
        "latest_product_owned": {
            "successes": product_successes,
            "takeover_required": product_takeovers,
            "silent_failures": product_silent_failures,
            "failed_unclassified": product_unclassified,
            "outcome_counts": count_outcomes(latest_product_owned),
        },
        "latest_certified": {
            "successes": certified_successes,
            "silent_failures": certified_silent_failures,
            "outcome_counts": count_outcomes(latest_certified),
        },
        "dod_pass": product_gate and certified_gate,
    }


def render_text(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"history_records={summary['history_records']}",
            f"current_contract_records={summary['current_contract_records']}",
            f"product_owned_records={summary['product_owned_records']}",
            f"latest_product_owned_window={summary['latest_product_owned_window']}",
            f"latest_certified_window={summary['latest_certified_window']}",
            f"current_contract_only={str(summary['current_contract_only']).lower()}",
            f"foundation_contract_version={summary['foundation_contract_version']}",
            f"product_owned_gate_pass={str(summary['product_owned_gate_pass']).lower()}",
            f"certified_runtime_gate_pass={str(summary['certified_runtime_gate_pass']).lower()}",
            f"dod_pass={str(summary['dod_pass']).lower()}",
            "latest_product_owned="
            + json.dumps(summary["latest_product_owned"], ensure_ascii=False, sort_keys=True),
            "latest_certified="
            + json.dumps(summary["latest_certified"], ensure_ascii=False, sort_keys=True),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate current zero-silent-failure DoD from Telegram E2E history.")
    parser.add_argument(
        "--history-path",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "progress" / "TELEGRAM_E2E_HISTORY.jsonl"),
    )
    parser.add_argument("--product-owned-window", type=int, default=10)
    parser.add_argument("--certified-window", type=int, default=20)
    parser.add_argument("--format", choices=["json", "text"], default="text")
    parser.add_argument("--include-legacy", action="store_true", help="Include legacy/pre-contract history in evaluation.")
    args = parser.parse_args()

    history = load_history(Path(args.history_path))
    summary = evaluate(
        history,
        e2e_window=args.product_owned_window,
        certified_window=args.certified_window,
        current_contract_only=not args.include_legacy,
    )
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_text(summary))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[evaluate-zero-silent-failure] error: {exc}", file=sys.stderr)
        raise
