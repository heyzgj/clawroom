from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
FAKE_CHAIN = ROOT / 'scripts/autoresearch_sync_demo/fake_chain.py'


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f'{base_url}/healthz', timeout=1.0)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise AssertionError('api server did not become healthy')


def test_fake_chain_runs_three_closed_cycles_with_lineage(tmp_path: Path) -> None:
    db_path = tmp_path / 'clawroom_fake_chain.db'
    workspace_root = tmp_path / 'workspace'
    port = _free_port()
    base_url = f'http://127.0.0.1:{port}'

    env = os.environ.copy()
    env['CLAWROOM_DB_DSN'] = f'sqlite+pysqlite:///{db_path}'
    env['ROOMBRIDGE_DB_DSN'] = env['CLAWROOM_DB_DSN']
    env['PYTHONPATH'] = ':'.join([
        str(ROOT),
        str(ROOT / 'apps/api/src'),
        str(ROOT / 'packages/client/src'),
        str(ROOT / 'packages/core/src'),
        str(ROOT / 'packages/store/src'),
        env.get('PYTHONPATH', ''),
    ]).strip(':')

    server = subprocess.Popen(
        [
            'python3', '-m', 'uvicorn', 'roombridge_api.main:app',
            '--host', '127.0.0.1', '--port', str(port), '--log-level', 'warning'
        ],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_for_health(base_url)
        proc = subprocess.run(
            ['python3', str(FAKE_CHAIN), '--base-url', base_url, '--workspace-root', str(workspace_root), '--cycles', '3'],
            cwd=str(ROOT), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        cycles = payload['cycles']
        assert len(cycles) == 3
        assert cycles[0]['parent_room_id'] is None
        assert cycles[1]['parent_room_id'] == cycles[0]['room_id']
        assert cycles[2]['parent_room_id'] == cycles[1]['room_id']
        for item in cycles:
            result = item['result']
            assert result['status'] == 'closed'
            assert result['stop_reason'] == 'mutual_done'
            assert result['required_filled'] == 4
        a1_program = (workspace_root / 'a1' / 'program.md').read_text()
        a2_program = (workspace_root / 'a2' / 'program.md').read_text()
        assert 'cycle 3' in a1_program.lower()
        assert 'cycle 3' in a2_program.lower()
        assert 'Known Dead Ends' in a1_program
        assert 'Known Dead Ends' in a2_program
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
