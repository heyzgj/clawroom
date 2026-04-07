#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Small ClawRoom probe used to verify OpenClaw background exec + process support.")
    parser.add_argument("--sleep-seconds", type=int, default=12)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    print(json.dumps({"status": "probe_started", "sleep_seconds": int(args.sleep_seconds)}), flush=True)
    time.sleep(max(1, int(args.sleep_seconds)))
    print(json.dumps({"status": "probe_finished"}), flush=True)


if __name__ == "__main__":
    main()
