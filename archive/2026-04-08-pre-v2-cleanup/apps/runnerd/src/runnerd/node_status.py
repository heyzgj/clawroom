from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class InboxPollingConfig:
    base_url: str
    agent_id: str
    inbox_token: str
    runner_kind: str
    managed_runnerd_url: str
    display_name: str
    owner_label: str
    gateway_label: str
    wait_seconds: int
    cursor_path: Path

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.agent_id and self.inbox_token)

    @property
    def helper_endpoint_mode(self) -> str:
        url = (self.managed_runnerd_url or "").strip().lower()
        if not url:
            return "unspecified"
        if url.startswith("http://127.0.0.1") or url.startswith("http://localhost") or url.startswith("http://[::1]"):
            return "co_located_localhost"
        if url.startswith("https://") or url.startswith("http://"):
            return "remote_configured"
        return "invalid"


def build_node_info(*, config: InboxPollingConfig | None, state_root: Path) -> dict[str, Any]:
    if not config:
        return {
            "configured": False,
            "state_root": str(state_root),
            "helper_endpoint_mode": "disabled",
            "message": "runnerd inbox mode is not configured on this node",
        }
    helper_mode = config.helper_endpoint_mode
    topology = (
        "all_in_one_node" if helper_mode == "co_located_localhost"
        else "split_or_remote_node" if helper_mode == "remote_configured"
        else "inbox_only_node"
    )
    return {
        "configured": True,
        "topology": topology,
        "helper_endpoint_mode": helper_mode,
        "api_base_url": config.base_url,
        "agent_id": config.agent_id,
        "runner_kind": config.runner_kind,
        "managed_runnerd_url": config.managed_runnerd_url,
        "display_name": config.display_name,
        "owner_label": config.owner_label,
        "gateway_label": config.gateway_label,
        "inbox_wait_seconds": config.wait_seconds,
        "cursor_path": str(config.cursor_path),
        "state_root": str(state_root),
        "has_inbox_token": bool(config.inbox_token),
        "inbox_poller_enabled": config.enabled,
        "presence_sync_enabled": config.enabled,
        "message": (
            "Gateway and helper are co-located; localhost is expected."
            if helper_mode == "co_located_localhost"
            else "Gateway should submit wakes to the configured remote runnerd endpoint."
            if helper_mode == "remote_configured"
            else "No managed helper endpoint is configured yet for this node."
        ),
    }


def build_readyz(*, config: InboxPollingConfig | None, state_root: Path) -> dict[str, Any]:
    if not config:
        return {
            "ready": False,
            "issues": ["inbox_mode_not_configured"],
            "checks": {
                "inbox_configured": False,
                "state_root_exists": state_root.exists(),
                "state_root_writable": os.access(state_root, os.W_OK),
                "helper_endpoint_mode_valid": False,
                "managed_endpoint_declared": False,
            },
            "node_info": build_node_info(config=config, state_root=state_root),
        }

    helper_mode = config.helper_endpoint_mode
    issues: list[str] = []
    checks = {
        "inbox_configured": config.enabled,
        "state_root_exists": state_root.exists(),
        "state_root_writable": os.access(state_root, os.W_OK),
        "helper_endpoint_mode_valid": helper_mode in {"co_located_localhost", "remote_configured", "unspecified"},
        "managed_endpoint_declared": bool((config.managed_runnerd_url or "").strip()),
    }
    if not checks["inbox_configured"]:
        issues.append("inbox_config_missing_fields")
    if not checks["state_root_exists"]:
        issues.append("state_root_missing")
    if not checks["state_root_writable"]:
        issues.append("state_root_not_writable")
    if helper_mode == "invalid":
        issues.append("invalid_managed_runnerd_url")
    elif helper_mode == "unspecified":
        issues.append("managed_runnerd_url_not_declared")

    return {
        "ready": len(issues) == 0,
        "issues": issues,
        "checks": checks,
        "node_info": build_node_info(config=config, state_root=state_root),
    }
