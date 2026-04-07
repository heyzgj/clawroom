from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "query_clawroom_monitor.py"
SPEC = importlib.util.spec_from_file_location("query_clawroom_monitor", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_resolve_admin_token_prefers_cli_then_env() -> None:
    assert MODULE.resolve_admin_token("cli-token", {"MONITOR_ADMIN_TOKEN": "env-token"}) == "cli-token"
    assert MODULE.resolve_admin_token("", {"MONITOR_ADMIN_TOKEN": "env-token"}) == "env-token"
    assert MODULE.resolve_admin_token("", {"CLAWROOM_MONITOR_ADMIN_TOKEN": "legacy-token"}) == "legacy-token"


def test_build_monitor_url_defaults_to_summary_text_query() -> None:
    url = MODULE.build_monitor_url(
        "https://api.clawroom.cc",
        view="summary",
        limit=25,
        text_format=True,
    )
    assert url == "https://api.clawroom.cc/monitor/summary?limit=25&format=text"


def test_build_monitor_url_overview_stays_json() -> None:
    url = MODULE.build_monitor_url(
        "https://api.clawroom.cc/",
        view="overview",
        limit=10,
        text_format=False,
    )
    assert url == "https://api.clawroom.cc/monitor/overview?limit=10"
