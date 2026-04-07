from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[4]
for candidate in (
    ROOT / "apps" / "runnerd" / "src",
    ROOT / "packages" / "client" / "src",
    ROOT / "packages" / "core" / "src",
    ROOT / "packages" / "store" / "src",
):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local ClawRoom runnerd")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8741)
    args = parser.parse_args()
    uvicorn.run("runnerd.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
