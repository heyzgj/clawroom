from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[4]
    runnerd_src = ROOT / "apps" / "runnerd" / "src"
    if str(runnerd_src) not in sys.path:
        sys.path.insert(0, str(runnerd_src))
    from runnerd.models import WakePackage, parse_wake_package_text
else:
    from .models import WakePackage, parse_wake_package_text


def read_text(*, text: str, text_file: str) -> str:
    if text_file:
        return Path(text_file).read_text(encoding="utf-8").strip()
    return str(text or "").strip()


def parse_package_input(raw: str) -> WakePackage:
    cleaned = str(raw or "").strip()
    if not cleaned:
        raise ValueError("wake package text is required")
    if cleaned.startswith("{"):
        return WakePackage.model_validate_json(cleaned)
    return parse_wake_package_text(cleaned)


def submit_package(*, runnerd_url: str, package: WakePackage) -> dict[str, object]:
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        response = client.post(f"{runnerd_url.rstrip('/')}/wake", json=package.model_dump(mode="json"))
    response.raise_for_status()
    return dict(response.json())


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit a ClawRoom wake package to local runnerd.")
    parser.add_argument("--runnerd-url", default="http://127.0.0.1:8741")
    parser.add_argument("--text", default="", help="Wake package text or raw JSON")
    parser.add_argument("--text-file", default="", help="Path to a file containing the wake package")
    parser.add_argument("--json", action="store_true", help="Print raw JSON only")
    args = parser.parse_args()

    raw = read_text(text=args.text, text_file=args.text_file)
    package = parse_package_input(raw)
    response = submit_package(runnerd_url=args.runnerd_url, package=package)
    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return
    status = response.get("status")
    run_id = response.get("run_id")
    runner_kind = response.get("runner_kind")
    reason = response.get("reason")
    print(f"accepted={response.get('accepted')} run_id={run_id} runner_kind={runner_kind} status={status} reason={reason}")
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
