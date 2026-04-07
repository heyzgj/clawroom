from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RUNNERD_URL = "http://127.0.0.1:8741"
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 8.0
DEFAULT_STARTUP_TIMEOUT_SECONDS = 12.0
DEFAULT_LEGACY_QUIESCENT_SECONDS = 120.0


def _normalize_url(url: str) -> str:
    value = (url or DEFAULT_RUNNERD_URL).strip().rstrip("/")
    return value or DEFAULT_RUNNERD_URL


def _is_local_url(url: str) -> bool:
    host = (urlparse(_normalize_url(url)).hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _local_port(url: str) -> int:
    parsed = urlparse(_normalize_url(url))
    if parsed.port:
        return int(parsed.port)
    return 443 if parsed.scheme == "https" else 80


def _local_host(url: str) -> str:
    parsed = urlparse(_normalize_url(url))
    host = (parsed.hostname or "127.0.0.1").strip()
    return "127.0.0.1" if host in {"localhost", "::1"} else host


def _parse_clawroom_env_from_ps_line(line: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for token in shlex.split((line or "").strip()):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key.startswith("CLAWROOM_"):
            env[key] = value
    return env


def _run_text(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    return result.stdout.strip()


def _detect_listener_pid(url: str) -> int | None:
    if not _is_local_url(url):
        return None
    port = _local_port(url)
    output = _run_text(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"])
    for line in output.splitlines():
        if line.startswith("p"):
            raw = line[1:].strip()
            if raw.isdigit():
                return int(raw)
    return None


def _detect_child_processes(pid: int) -> list[dict[str, Any]]:
    output = _run_text(["pgrep", "-P", str(pid), "-a"])
    children_by_pid: dict[int, dict[str, Any]] = {}
    children: list[dict[str, Any]] = []
    for line in output.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split(maxsplit=1)
        if not parts[0].isdigit():
            continue
        command = parts[1] if len(parts) > 1 else ""
        child_pid = int(parts[0])
        if not command:
            command = _ps_process_command(child_pid)
        bridge_state = _summarize_bridge_state_from_command(command)
        children_by_pid[child_pid] = {
            "pid": child_pid,
            "command": command,
            "bridge_state": bridge_state,
            "status": _pid_status(child_pid),
        }
    ps_output = _run_text(["ps", "-ax", "-o", "pid=,ppid=,stat=,command="])
    for line in ps_output.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split(None, 3)
        if len(parts) < 3:
            continue
        raw_pid, raw_ppid, raw_stat = parts[:3]
        command = parts[3] if len(parts) > 3 else ""
        if not raw_pid.isdigit() or not raw_ppid.isdigit():
            continue
        child_pid = int(raw_pid)
        if int(raw_ppid) != pid or child_pid in children_by_pid:
            continue
        children_by_pid[child_pid] = {
            "pid": child_pid,
            "command": command,
            "bridge_state": _summarize_bridge_state_from_command(command),
            "status": "zombie" if "Z" in raw_stat.upper() else raw_stat,
        }
    children.extend(children_by_pid.values())
    children.sort(key=lambda item: int(item.get("pid") or 0))
    return children


def _ps_command_line(pid: int) -> str:
    return _run_text(["ps", "eww", "-p", str(pid), "-o", "command="])


def _ps_process_command(pid: int) -> str:
    return _run_text(["ps", "-p", str(pid), "-o", "command="])


def _pid_status(pid: int) -> str:
    output = _run_text(["ps", "-p", str(pid), "-o", "stat="]).strip().upper()
    if not output:
        return "missing"
    if "Z" in output:
        return "zombie"
    return "alive"


def _clawroom_env_from_pid(pid: int) -> dict[str, str]:
    return _parse_clawroom_env_from_ps_line(_ps_command_line(pid))


def _summarize_bridge_state_from_command(command: str) -> dict[str, Any] | None:
    try:
        argv = shlex.split(command or "")
    except ValueError:
        return None
    state_path: Path | None = None
    for index, token in enumerate(argv):
        if token == "--state-path" and index + 1 < len(argv):
            state_path = Path(argv[index + 1]).expanduser()
            break
    if state_path is None or not state_path.exists():
        return None
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"state_path": str(state_path), "read_error": True}
    if not isinstance(raw, dict):
        return {"state_path": str(state_path), "read_error": True}
    conversation = raw.get("conversation") if isinstance(raw.get("conversation"), dict) else {}
    health = raw.get("health") if isinstance(raw.get("health"), dict) else {}
    return {
        "state_path": str(state_path),
        "room_id": str(raw.get("room_id") or "").strip() or None,
        "participant": str(raw.get("participant") or "").strip() or None,
        "runner_id": str(raw.get("runner_id") or "").strip() or None,
        "attempt_id": str(raw.get("attempt_id") or "").strip() or None,
        "health_status": str(health.get("status") or "").strip() or None,
        "health_note": str(health.get("recent_note") or "").strip() or None,
        "pending_owner_req_id": str(conversation.get("pending_owner_req_id") or "").strip() or None,
    }


def _probe_endpoint(url: str, path: str, *, timeout_seconds: float) -> dict[str, Any]:
    target = f"{_normalize_url(url)}{path}"
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(target)
    except Exception as exc:
        return {
            "ok": False,
            "url": target,
            "status_code": None,
            "error": str(exc)[:300],
            "json": None,
            "text": None,
        }
    payload_json: Any = None
    payload_text: str | None = None
    try:
        payload_json = response.json()
    except Exception:
        payload_text = response.text[:500]
    return {
        "ok": response.status_code == 200,
        "url": target,
        "status_code": response.status_code,
        "error": None,
        "json": payload_json,
        "text": payload_text,
    }


def _default_state_root() -> Path:
    return Path(os.getenv("CLAWROOM_RUNNERD_STATE_ROOT", Path.home() / ".clawroom" / "runnerd")).expanduser()


def _state_root_for_local_pid(pid: int | None) -> Path:
    if pid is None:
        return _default_state_root()
    env = _clawroom_env_from_pid(pid)
    raw = (env.get("CLAWROOM_RUNNERD_STATE_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _default_state_root()


def _latest_state_activity(state_root: Path) -> dict[str, Any]:
    latest_ts = 0.0
    latest_path: str | None = None
    if not state_root.exists():
        return {
            "state_root": str(state_root),
            "exists": False,
            "latest_activity_at": None,
            "latest_activity_age_seconds": None,
            "latest_activity_path": None,
        }
    for pattern in ("runs/*/run.json", "runs/*/bridge_state.json", "pending_owner_gates.json"):
        for path in state_root.glob(pattern):
            try:
                mtime = path.stat().st_mtime
            except Exception:
                continue
            if mtime >= latest_ts:
                latest_ts = mtime
                latest_path = str(path)
    age_seconds: float | None = None
    latest_at: str | None = None
    if latest_ts > 0:
        age_seconds = max(0.0, time.time() - latest_ts)
        latest_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(latest_ts))
    return {
        "state_root": str(state_root),
        "exists": True,
        "latest_activity_at": latest_at,
        "latest_activity_age_seconds": age_seconds,
        "latest_activity_path": latest_path,
    }


def _inspect_disk_runs(state_root: Path) -> dict[str, Any]:
    summary = {
        "nonterminal_runs": 0,
        "alive_pid_runs": 0,
        "zombie_pid_runs": 0,
        "dead_pid_runs": 0,
        "missing_pid_runs": 0,
    }
    if not state_root.exists():
        return summary
    for run_json in state_root.glob("runs/*/run.json"):
        try:
            payload = json.loads(run_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or "").strip()
        if status in {"exited", "abandoned", "replaced"}:
            continue
        summary["nonterminal_runs"] += 1
        pid = payload.get("pid")
        if not isinstance(pid, int):
            summary["missing_pid_runs"] += 1
            continue
        pid_status = _pid_status(pid)
        if pid_status == "alive":
            summary["alive_pid_runs"] += 1
        elif pid_status == "zombie":
            summary["zombie_pid_runs"] += 1
        else:
            summary["dead_pid_runs"] += 1
    return summary


def _activity_payload_status(latest_activity_path: str | None) -> dict[str, Any] | None:
    if not latest_activity_path:
        return None
    path = Path(latest_activity_path)
    if path.name != "run.json" or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"path": str(path), "read_error": True}
    if not isinstance(payload, dict):
        return {"path": str(path), "read_error": True}
    pid = payload.get("pid")
    pid_status = _pid_status(pid) if isinstance(pid, int) else "missing"
    return {
        "path": str(path),
        "run_id": str(payload.get("run_id") or "").strip() or None,
        "status": str(payload.get("status") or "").strip() or None,
        "reason": str(payload.get("reason") or "").strip() or None,
        "pid": pid if isinstance(pid, int) else None,
        "pid_status": pid_status,
        "attempt_id": str(payload.get("attempt_id") or "").strip() or None,
        "participant": str(payload.get("participant") or "").strip() or None,
    }


def _inspect_runs_payload(runs_payload: dict[str, Any] | None) -> dict[str, Any]:
    summary = {
        "live_runs": 0,
        "live_process_backed_runs": 0,
        "stale_live_runs": 0,
    }
    if not isinstance(runs_payload, dict):
        return summary
    runs = runs_payload.get("runs")
    if not isinstance(runs, list):
        return summary
    for item in runs:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip()
        if status in {"exited", "abandoned", "replaced"}:
            continue
        summary["live_runs"] += 1
        pid = item.get("pid")
        pid_status = _pid_status(pid) if isinstance(pid, int) else "missing"
        if pid_status == "alive":
            summary["live_process_backed_runs"] += 1
        else:
            summary["stale_live_runs"] += 1
    return summary


def _legacy_quiescent_status(
    *,
    local_target: bool,
    pid: int | None,
    children: list[dict[str, Any]],
    probes: dict[str, dict[str, Any]],
    min_quiescent_seconds: float = DEFAULT_LEGACY_QUIESCENT_SECONDS,
) -> dict[str, Any]:
    state_root = _state_root_for_local_pid(pid) if local_target else _default_state_root()
    activity = _latest_state_activity(state_root)
    disk_runs = _inspect_disk_runs(state_root)
    latest_activity_run = _activity_payload_status(activity.get("latest_activity_path"))
    contract_missing = (
        probes["node_info"].get("status_code") != 200
        or probes["readyz"].get("status_code") != 200
    )
    age_seconds = activity.get("latest_activity_age_seconds")
    latest_activity_is_zombie_only = bool(
        isinstance(latest_activity_run, dict)
        and latest_activity_run.get("pid_status") in {"zombie", "dead", "missing"}
    )
    quiescent = bool(
        local_target
        and pid is not None
        and not [child for child in children if child.get("status") not in {"zombie", "missing"}]
        and contract_missing
        and (
            disk_runs["alive_pid_runs"] == 0
            and latest_activity_is_zombie_only
            or age_seconds is None
            or float(age_seconds) >= float(min_quiescent_seconds)
        )
    )
    return {
        "confirmed": quiescent,
        "min_quiescent_seconds": float(min_quiescent_seconds),
        "state_activity": activity,
        "disk_runs": disk_runs,
        "latest_activity_run": latest_activity_run,
    }


def inspect_runnerd(
    *,
    runnerd_url: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    min_legacy_quiescent_seconds: float = DEFAULT_LEGACY_QUIESCENT_SECONDS,
) -> dict[str, Any]:
    normalized_url = _normalize_url(runnerd_url)
    local_target = _is_local_url(normalized_url)
    pid = _detect_listener_pid(normalized_url)
    children = _detect_child_processes(pid) if pid is not None else []
    probes = {
        "healthz": _probe_endpoint(normalized_url, "/healthz", timeout_seconds=timeout_seconds),
        "node_info": _probe_endpoint(normalized_url, "/node-info", timeout_seconds=timeout_seconds),
        "readyz": _probe_endpoint(normalized_url, "/readyz", timeout_seconds=timeout_seconds),
        "runs": _probe_endpoint(normalized_url, "/runs", timeout_seconds=timeout_seconds),
    }
    health_json = probes["healthz"]["json"] if isinstance(probes["healthz"]["json"], dict) else {}
    runs_json = probes["runs"]["json"] if isinstance(probes["runs"]["json"], dict) else {}
    active_runs = int(health_json.get("active_runs") or 0) if probes["healthz"]["ok"] else None
    waiting_owner_runs = int(health_json.get("waiting_owner_runs") or 0) if probes["healthz"]["ok"] else None
    runs_summary = _inspect_runs_payload(runs_json if probes["runs"]["ok"] else None)
    live_runs = runs_summary["live_runs"] if probes["runs"]["ok"] else None
    live_process_backed_runs = runs_summary["live_process_backed_runs"] if probes["runs"]["ok"] else None
    idle_confirmed = False
    if active_runs is not None and waiting_owner_runs is not None:
        idle_confirmed = active_runs == 0 and waiting_owner_runs == 0
    elif live_runs is not None:
        idle_confirmed = live_runs == 0
    legacy_quiescent = _legacy_quiescent_status(
        local_target=local_target,
        pid=pid,
        children=children,
        probes=probes,
        min_quiescent_seconds=min_legacy_quiescent_seconds,
    )
    issues: list[str] = []
    blocking_issues: list[str] = []
    if not local_target:
        issues.append("non_local_target_requires_manual_remote_upgrade")
        blocking_issues.append("non_local_target_requires_manual_remote_upgrade")
    if pid is None:
        issues.append("runnerd_listener_pid_not_found")
        blocking_issues.append("runnerd_listener_pid_not_found")
    live_children = [child for child in children if child.get("status") not in {"zombie", "missing"}]
    zombie_children = [child for child in children if child.get("status") == "zombie"]
    if live_children:
        issues.append("active_child_processes_present")
        blocking_issues.append("active_child_processes_present")
    if zombie_children:
        issues.append("zombie_child_processes_present")
    if active_runs not in (None, 0):
        issues.append("active_runs_present")
        if not legacy_quiescent["confirmed"] and live_process_backed_runs not in (0, None):
            blocking_issues.append("active_runs_present")
    if waiting_owner_runs not in (None, 0):
        issues.append("waiting_owner_runs_present")
        if not legacy_quiescent["confirmed"] and live_process_backed_runs not in (0, None):
            blocking_issues.append("waiting_owner_runs_present")
    if not idle_confirmed and not legacy_quiescent["confirmed"] and live_process_backed_runs not in (0, None):
        issues.append("idle_state_not_confirmed")
        blocking_issues.append("idle_state_not_confirmed")
    if probes["node_info"]["status_code"] != 200:
        issues.append("node_info_endpoint_missing")
    if probes["readyz"]["status_code"] != 200:
        issues.append("readyz_endpoint_missing")
    safe_to_restart = len(blocking_issues) == 0
    return {
        "runnerd_url": normalized_url,
        "local_target": local_target,
        "pid": pid,
        "child_processes": children,
        "probes": probes,
        "inferred": {
            "active_runs": active_runs,
            "waiting_owner_runs": waiting_owner_runs,
            "live_runs": live_runs,
            "live_process_backed_runs": live_process_backed_runs,
            "idle_confirmed": idle_confirmed,
            "legacy_quiescent_confirmed": legacy_quiescent["confirmed"],
            "safe_to_restart": safe_to_restart,
        },
        "issues": issues,
        "blocking_issues": blocking_issues,
        "runs_summary": runs_summary,
        "legacy_quiescent": legacy_quiescent,
    }


def _wait_for_pid_exit(pid: int, *, timeout_seconds: float) -> bool:
    deadline = time.time() + max(0.0, timeout_seconds)
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


def _launch_runnerd(*, url: str, extra_env: dict[str, str]) -> dict[str, Any]:
    host = _local_host(url)
    port = _local_port(url)
    script = ROOT / "apps" / "runnerd" / "src" / "runnerd" / "cli.py"
    env = os.environ.copy()
    env.update(extra_env)
    state_root = Path(env.get("CLAWROOM_RUNNERD_STATE_ROOT", Path.home() / ".clawroom" / "runnerd")).expanduser()
    state_root.mkdir(parents=True, exist_ok=True)
    log_path = state_root / "runnerd-daemon.log"
    with log_path.open("a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--host", host, "--port", str(port)],
            cwd=str(ROOT),
            env=env,
            stdout=log_handle,
            stderr=log_handle,
            text=True,
            start_new_session=True,
        )
    return {"pid": proc.pid, "log_path": str(log_path)}


def _wait_for_contract(url: str, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.time() + max(0.0, timeout_seconds)
    last_report: dict[str, Any] | None = None
    while time.time() < deadline:
        report = inspect_runnerd(runnerd_url=url)
        last_report = report
        if report["probes"]["node_info"]["status_code"] == 200 and report["probes"]["readyz"]["status_code"] == 200:
            return report
        time.sleep(0.25)
    return last_report or inspect_runnerd(runnerd_url=url)


def restart_runnerd_if_safe(
    *,
    runnerd_url: str,
    shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    report = inspect_runnerd(runnerd_url=runnerd_url)
    result: dict[str, Any] = {
        "attempted": False,
        "restarted": False,
        "reason": None,
        "new_pid": None,
        "log_path": None,
        "post_restart_report": None,
    }
    if not report["inferred"]["safe_to_restart"]:
        result["reason"] = "unsafe_to_restart"
        result["post_restart_report"] = report
        return result
    pid = report["pid"]
    if pid is None:
        result["reason"] = "runnerd_listener_pid_not_found"
        result["post_restart_report"] = report
        return result
    result["attempted"] = True
    extra_env = _clawroom_env_from_pid(pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        result["reason"] = f"terminate_failed:{str(exc)[:200]}"
        result["post_restart_report"] = report
        return result
    if not _wait_for_pid_exit(pid, timeout_seconds=shutdown_timeout_seconds):
        result["reason"] = "runnerd_did_not_exit_after_sigterm"
        result["post_restart_report"] = inspect_runnerd(runnerd_url=runnerd_url)
        return result
    launch = _launch_runnerd(url=runnerd_url, extra_env=extra_env)
    post_restart = _wait_for_contract(runnerd_url, timeout_seconds=startup_timeout_seconds)
    result["new_pid"] = launch["pid"]
    result["log_path"] = launch["log_path"]
    result["post_restart_report"] = post_restart
    result["restarted"] = (
        post_restart["probes"]["node_info"]["status_code"] == 200
        and post_restart["probes"]["readyz"]["status_code"] == 200
    )
    result["reason"] = "runnerd_restarted" if result["restarted"] else "new_runnerd_missing_contract_endpoints"
    return result


def _render_human(report: dict[str, Any], restart: dict[str, Any] | None = None) -> str:
    inferred = report.get("inferred", {})
    child_lines: list[str] = []
    for child in report.get("child_processes") or []:
        bridge_state = child.get("bridge_state") if isinstance(child, dict) else None
        if isinstance(bridge_state, dict) and bridge_state.get("room_id"):
            child_lines.append(
                f"child[{child.get('pid')}]: room={bridge_state.get('room_id')} participant={bridge_state.get('participant')} status={bridge_state.get('health_status')} owner_req={bridge_state.get('pending_owner_req_id')}"
            )
        elif isinstance(child, dict):
            child_lines.append(f"child[{child.get('pid')}]: {child.get('command')}")
    lines = [
        f"runnerd_url: {report.get('runnerd_url')}",
        f"local_target: {report.get('local_target')}",
        f"pid: {report.get('pid')}",
        f"child_processes: {len(report.get('child_processes') or [])}",
        f"active_runs: {inferred.get('active_runs')}",
        f"waiting_owner_runs: {inferred.get('waiting_owner_runs')}",
        f"idle_confirmed: {inferred.get('idle_confirmed')}",
        f"live_process_backed_runs: {inferred.get('live_process_backed_runs')}",
        f"legacy_quiescent_confirmed: {inferred.get('legacy_quiescent_confirmed')}",
        f"safe_to_restart: {inferred.get('safe_to_restart')}",
        f"issues: {', '.join(report.get('issues') or []) or 'none'}",
        f"blocking_issues: {', '.join(report.get('blocking_issues') or []) or 'none'}",
        f"node_info_status: {report['probes']['node_info']['status_code']}",
        f"readyz_status: {report['probes']['readyz']['status_code']}",
    ]
    lines.extend(child_lines)
    if restart is not None:
        lines.extend(
            [
                f"restart_attempted: {restart.get('attempted')}",
                f"restart_result: {restart.get('reason')}",
                f"restart_new_pid: {restart.get('new_pid')}",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and safely upgrade a local runnerd daemon.")
    parser.add_argument("--runnerd-url", default=DEFAULT_RUNNERD_URL)
    parser.add_argument("--restart-if-safe", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = inspect_runnerd(runnerd_url=args.runnerd_url)
    restart_payload = restart_runnerd_if_safe(runnerd_url=args.runnerd_url) if args.restart_if_safe else None
    payload = {"report": report, "restart": restart_payload}

    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print(_render_human(report, restart_payload))

    if restart_payload and not restart_payload.get("restarted"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
