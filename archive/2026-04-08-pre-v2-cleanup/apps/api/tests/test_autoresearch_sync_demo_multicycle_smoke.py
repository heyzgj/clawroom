from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[3]
RUN_FAKE_CHAIN = ROOT / "scripts/autoresearch_sync_demo/run_fake_chain.py"


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


def test_autoresearch_sync_demo_multicycle_fake_chain_smoke(tmp_path: Path) -> None:
    db_path = tmp_path / "clawroom_fake_chain.db"
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
        proc = subprocess.run(
            [
                "python3",
                str(RUN_FAKE_CHAIN),
                "--base-url",
                base_url,
                "--workspace-root",
                str(workspace_root),
                "--cycles",
                "3",
            ],
            cwd=str(ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
        payload = json.loads(proc.stdout)
        cycles = payload["cycles"]
        assert len(cycles) == 3
        assert [c["status"] for c in cycles] == ["closed", "closed", "closed"]
        assert [c["stop_reason"] for c in cycles] == ["mutual_done", "mutual_done", "mutual_done"]
        assert cycles[0]["parent_room_id"] is None
        assert cycles[1]["parent_room_id"] == cycles[0]["room_id"]
        assert cycles[2]["parent_room_id"] == cycles[1]["room_id"]
        assert "Previous cycle best:" in cycles[1]["prior_outcome_summary"]
        assert "Previous cycle best:" in cycles[2]["prior_outcome_summary"]
        assert "0.9398" in cycles[1]["fields"]["best_result_summary"]["value"]
        assert "0.9389" in cycles[2]["fields"]["best_result_summary"]["value"]

        program_a1 = (workspace_root / "a1/program.md").read_text()
        program_a2 = (workspace_root / "a2/program.md").read_text()
        assert "tiny optimizer-only refinement" in program_a1
        assert "validate nearby dropout and moderate weight decay settings" in program_a2
        assert "warmup > 16 is wasteful" in program_a1
        assert "val_bpb 0.9389" in program_a2
    finally:
        server.terminate()
        try:
            server.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate(timeout=10)
