from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx


def read_text(*, text: str, text_file: str) -> str:
    if text_file:
        return Path(text_file).read_text(encoding="utf-8").strip()
    cleaned = str(text or "").strip()
    if not cleaned:
        raise ValueError("owner reply text is required")
    return cleaned


def submit_owner_reply(
    *,
    runnerd_url: str,
    run_id: str,
    text: str,
    owner_request_id: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {"text": text}
    if owner_request_id:
        payload["owner_request_id"] = owner_request_id
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        response = client.post(f"{runnerd_url.rstrip('/')}/runs/{run_id}/owner-reply", json=payload)
    response.raise_for_status()
    return dict(response.json())


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit an owner reply back to local runnerd.")
    parser.add_argument("--runnerd-url", default="http://127.0.0.1:8741")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--owner-request-id", default="")
    parser.add_argument("--text", default="", help="Owner reply text")
    parser.add_argument("--text-file", default="", help="Path to a file containing the owner reply")
    parser.add_argument("--json", action="store_true", help="Print raw JSON only")
    args = parser.parse_args()

    text = read_text(text=args.text, text_file=args.text_file)
    response = submit_owner_reply(
        runnerd_url=args.runnerd_url,
        run_id=args.run_id,
        text=text,
        owner_request_id=(args.owner_request_id or "").strip() or None,
    )
    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return
    print(f"run_id={args.run_id} status={response.get('status')} current_hop={response.get('current_hop')}")
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
