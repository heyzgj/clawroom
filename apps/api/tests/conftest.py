from __future__ import annotations

import os
import sys
from importlib import reload
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api/src"))
sys.path.insert(0, str(ROOT / "apps/runnerd/src"))
sys.path.insert(0, str(ROOT / "packages/client/src"))
sys.path.insert(0, str(ROOT / "packages/core/src"))
sys.path.insert(0, str(ROOT / "packages/store/src"))


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    dsn = f"sqlite+pysqlite:///{tmp_path / 'clawroom_test.db'}"
    os.environ["CLAWROOM_DB_DSN"] = dsn
    os.environ["ROOMBRIDGE_DB_DSN"] = dsn

    from roombridge_api import main as api_main

    reload(api_main)
    with TestClient(api_main.app) as c:
        yield c
