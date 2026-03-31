#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def run_command(command: list[str], *, timeout: int = 8) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def check_exec_enabled() -> tuple[bool, str]:
    command = [sys.executable, "-c", "print('exec-ok')"]
    return run_command(command, timeout=6)


def check_python3() -> tuple[bool, str]:
    python3_path = shutil.which("python3") or sys.executable
    if not python3_path:
        return False, "python3 not found"
    return run_command([python3_path, "--version"], timeout=6)


def check_writable_workspace() -> tuple[bool, str]:
    root = Path.home() / ".clawroom"
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        return True, str(root)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def check_openclaw_agent_help() -> tuple[bool, str]:
    openclaw_path = shutil.which("openclaw")
    if not openclaw_path:
        return False, "openclaw not found"
    return run_command([openclaw_path, "agent", "--help"], timeout=8)


def help_supports(flag: str, help_text: str) -> bool:
    return flag in (help_text or "")


def build_report() -> dict[str, Any]:
    exec_ok, exec_detail = check_exec_enabled()
    python_ok, python_detail = check_python3()
    workspace_ok, workspace_detail = check_writable_workspace()
    openclaw_ok, openclaw_help = check_openclaw_agent_help()
    session_ok = openclaw_ok and help_supports("--session-id", openclaw_help)
    deliver_ok = openclaw_ok and help_supports("--deliver", openclaw_help)

    checks = {
        "exec_enabled": exec_ok,
        "python3": python_ok,
        "writable_workspace": workspace_ok,
        "openclaw_agent_cli": openclaw_ok,
        "openclaw_session_id": session_ok,
        "openclaw_deliver": deliver_ok,
    }
    details = {
        "exec_enabled": exec_detail,
        "python3": python_detail,
        "writable_workspace": workspace_detail,
        "openclaw_agent_cli": openclaw_help if openclaw_ok else openclaw_help,
    }
    missing = [name for name, ok in checks.items() if not ok]
    status = "ready" if not missing else "not_ready"
    return {
        "status": status,
        "checks": checks,
        "missing": missing,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether this OpenClaw runtime can run the ClawRoom mini-bridge.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    args = parser.parse_args()

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
        return

    print(f"status: {report['status']}")
    if report["missing"]:
        print("missing:", ", ".join(report["missing"]))


if __name__ == "__main__":
    main()
