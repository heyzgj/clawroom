from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[4]
    runnerd_src = ROOT / "apps" / "runnerd" / "src"
    if str(runnerd_src) not in sys.path:
        sys.path.insert(0, str(runnerd_src))
    from runnerd.models import WakePackage, render_wake_package
else:
    from .models import WakePackage, render_wake_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a ClawRoom runnerd wake package")
    parser.add_argument("--coordination-id", required=True)
    parser.add_argument("--wake-request-id", required=True)
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--join-link", required=True)
    parser.add_argument("--role", choices=["initiator", "responder", "auto"], default="auto")
    parser.add_argument("--task-summary", required=True)
    parser.add_argument("--owner-context", default="")
    parser.add_argument("--expected-output", default="")
    parser.add_argument("--deadline-at", default=None)
    parser.add_argument("--preferred-runner-kind", choices=["openclaw_bridge", "codex_bridge"], default="openclaw_bridge")
    parser.add_argument("--sender-owner-label", required=True)
    parser.add_argument("--sender-gateway-label", required=True)
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    package = WakePackage(
        coordination_id=args.coordination_id,
        wake_request_id=args.wake_request_id,
        room_id=args.room_id,
        join_link=args.join_link,
        role=args.role,
        task_summary=args.task_summary,
        owner_context=args.owner_context,
        expected_output=args.expected_output,
        deadline_at=args.deadline_at,
        preferred_runner_kind=args.preferred_runner_kind,
        sender_owner_label=args.sender_owner_label,
        sender_gateway_label=args.sender_gateway_label,
    )
    if args.json_only:
        print(json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(render_wake_package(package))


if __name__ == "__main__":
    main()
