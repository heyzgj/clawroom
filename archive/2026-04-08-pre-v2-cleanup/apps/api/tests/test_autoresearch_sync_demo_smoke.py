from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[3]
RUN_COORDINATED = ROOT / "scripts/autoresearch_sync_demo/run_coordinated.sh"
SYNC = ROOT / "scripts/autoresearch_sync_demo/sync.py"
ORCH = ROOT / "scripts/autoresearch_sync_demo/orchestrator.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    last_error: str | None = None
    client = httpx.Client(timeout=1.0, trust_env=False)
    while time.time() < deadline:
        try:
            resp = client.get(f"{base_url}/healthz")
            if resp.status_code == 200:
                client.close()
                return
            last_error = f"status={resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(0.1)
    client.close()
    raise AssertionError(f"api server did not become healthy: {last_error}")


def _run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"cmd={cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return proc


def test_autoresearch_sync_demo_fake_cycle_smoke(tmp_path: Path) -> None:
    db_path = tmp_path / "clawroom_fake_cycle.db"
    workspace_root = tmp_path / "workspace"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["CLAWROOM_DB_DSN"] = f"sqlite+pysqlite:///{db_path}"
    env["ROOMBRIDGE_DB_DSN"] = env["CLAWROOM_DB_DSN"]
    env["PYTHONPATH"] = ":".join(
        [
            str(ROOT),
            str(ROOT / "apps/api/src"),
            str(ROOT / "packages/client/src"),
            str(ROOT / "packages/core/src"),
            str(ROOT / "packages/store/src"),
            env.get("PYTHONPATH", ""),
        ]
    ).strip(":")

    server = subprocess.Popen(
        [
            "python3",
            "-m",
            "uvicorn",
            "roombridge_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_health(base_url)

        prep = _run(
            [str(RUN_COORDINATED)],
            env={
                **env,
                "CLAWROOM_API_BASE": base_url,
                "WORKSPACE_ROOT": str(workspace_root),
            },
        )
        assert "Prepared one fake coordinated sync cycle." in prep.stdout

        room_payload = json.loads((workspace_root / "state/room_create_cycle1.json").read_text())
        room_id = room_payload["room"]["id"]
        host_token = room_payload["host_token"]
        token_a1 = room_payload["invites"]["agent_a1"]
        token_a2 = room_payload["invites"]["agent_a2"]

        a1_summary = (ROOT / "scripts/autoresearch_sync_demo/fixtures/a1_cycle1_summary.txt").read_text().strip()
        a2_summary = (ROOT / "scripts/autoresearch_sync_demo/fixtures/a2_cycle1_summary.txt").read_text().strip()

        _run(
            [
                "python3",
                str(SYNC),
                "--base-url",
                base_url,
                "join",
                "--room-id",
                room_id,
                "--token",
                token_a1,
                "--client-name",
                "a1-sync",
                "--summary",
                a1_summary,
            ],
            env=env,
        )
        _run(
            [
                "python3",
                str(SYNC),
                "--base-url",
                base_url,
                "join",
                "--room-id",
                room_id,
                "--token",
                token_a2,
                "--client-name",
                "a2-sync",
                "--summary",
                a2_summary,
            ],
            env=env,
        )
        _run(
            [
                "python3",
                str(SYNC),
                "--base-url",
                base_url,
                "send",
                "--room-id",
                room_id,
                "--token",
                token_a1,
                "--intent",
                "ASK",
                "--expect-reply",
                "--text",
                "I explored lr and warmup. What did your regularization runs show, and how should we split the next cycle?",
            ],
            env=env,
        )
        fills = [
            "best_result_summary=val_bpb=0.9412, lr=6e-4, heads=8, commit=abc123",
            "dead_ends_summary=lr>=1e-3 diverges; batch>=128 worse",
            "assignment_a1=focus: fine-tune lr in [4e-4, 8e-4]; constraints: keep heads=8 fixed",
            "assignment_a2=focus: explore dropout + weight_decay; constraints: keep lr=6e-4 fixed",
        ]
        _run(
            [
                "python3",
                str(SYNC),
                "--base-url",
                base_url,
                "send",
                "--room-id",
                room_id,
                "--token",
                token_a2,
                "--intent",
                "ANSWER",
                "--text",
                "My dropout and weight-decay runs support that split. Let's carry those dead ends forward.",
                "--fill",
                fills[0],
                "--fill",
                fills[1],
                "--fill",
                fills[2],
                "--fill",
                fills[3],
            ],
            env=env,
        )
        _run(
            [
                "python3",
                str(SYNC),
                "--base-url",
                base_url,
                "send",
                "--room-id",
                room_id,
                "--token",
                token_a1,
                "--intent",
                "DONE",
                "--text",
                "Agreed. Those fields capture the split clearly.",
                "--fill",
                fills[0],
                "--fill",
                fills[1],
                "--fill",
                fills[2],
                "--fill",
                fills[3],
            ],
            env=env,
        )
        _run(
            [
                "python3",
                str(SYNC),
                "--base-url",
                base_url,
                "send",
                "--room-id",
                room_id,
                "--token",
                token_a2,
                "--intent",
                "DONE",
                "--text",
                "Confirmed. We can close and carry this into the next cycle.",
                "--fill",
                fills[0],
                "--fill",
                fills[1],
                "--fill",
                fills[2],
                "--fill",
                fills[3],
            ],
            env=env,
        )

        wait = _run(
            [
                "python3",
                str(ORCH),
                "--base-url",
                base_url,
                "wait-close",
                "--room-id",
                room_id,
                f"--host-token={host_token}",
                "--timeout",
                "10",
                "--poll-seconds",
                "0.1",
            ],
            env=env,
        )
        wait_payload = json.loads(wait.stdout)
        assert wait_payload["result"]["status"] == "closed"
        assert wait_payload["result"]["stop_reason"] == "mutual_done"

        apply = _run(
            [
                "python3",
                str(ORCH),
                "--base-url",
                base_url,
                "apply-assignments",
                "--room-id",
                room_id,
                f"--host-token={host_token}",
                "--a1-dir",
                str(workspace_root / "a1"),
                "--a2-dir",
                str(workspace_root / "a2"),
            ],
            env=env,
        )
        apply_payload = json.loads(apply.stdout)
        assert sorted(apply_payload["fields"].keys()) == [
            "assignment_a1",
            "assignment_a2",
            "best_result_summary",
            "dead_ends_summary",
        ]

        program_a1 = (workspace_root / "a1/program.md").read_text()
        program_a2 = (workspace_root / "a2/program.md").read_text()
        assert "## Current Focus" in program_a1
        assert "fine-tune lr in [4e-4, 8e-4]" in program_a1
        assert "- lr>=1e-3 diverges" in program_a1
        assert "explore dropout + weight_decay" in program_a2
        assert "keep lr=6e-4 fixed" in program_a2
    finally:
        server.terminate()
        try:
            server.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate(timeout=10)
