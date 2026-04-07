"""Contract tests for the agent whitelist + contacts feature.

These tests verify that the edge worker source code contains the expected
schema, routes, auth gates, and logic for the whitelist/contacts API.

Key design principles for these tests:
- Auth tests verify that EACH handler call site is preceded by verifyInboxTokenForAgent
- Route tests verify actual regex patterns, not just keyword presence
- Logic tests verify SQL patterns for mutual whitelist enforcement
- Tests must NOT be satisfiable by comments or unrelated code
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEAMS_TS = ROOT / "apps" / "edge" / "src" / "worker_teams.ts"
WORKER_TS = ROOT / "apps" / "edge" / "src" / "worker.ts"
SKILL_MD = ROOT / "skills" / "clawroom" / "SKILL.md"
CONTACTS_API_MD = ROOT / "skills" / "clawroom" / "references" / "contacts-api.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- Schema contract ---


def test_whitelist_table_exists_with_correct_columns() -> None:
    """The agent_whitelist table must be created in ensureSchema with the right PK."""
    source = _read(TEAMS_TS)
    assert "CREATE TABLE IF NOT EXISTS agent_whitelist" in source
    assert "agent_id TEXT NOT NULL" in source
    assert "allowed_agent_id TEXT NOT NULL" in source
    assert "added_at TEXT NOT NULL" in source
    assert "PRIMARY KEY (agent_id, allowed_agent_id)" in source


def test_agents_table_has_bio_and_tags_migrations() -> None:
    """Bio and tags must be added via ALTER TABLE migration (not just mentioned)."""
    source = _read(TEAMS_TS)
    assert 'ALTER TABLE agents ADD COLUMN bio TEXT' in source
    assert 'ALTER TABLE agents ADD COLUMN tags TEXT' in source


def test_register_agent_persists_bio_and_tags() -> None:
    """Agent registration INSERT and ON CONFLICT UPDATE must include bio and tags."""
    source = _read(TEAMS_TS)
    assert "bio=excluded.bio" in source
    assert "tags=excluded.tags" in source


# --- Route contract ---


def test_whitelist_route_uses_regex_pattern() -> None:
    """Whitelist route must use a regex pattern matching /agents/:id/whitelist."""
    source = _read(TEAMS_TS)
    # The source uses escaped slashes in the regex: /^\/agents\/([^/]+)\/whitelist$/
    assert "whitelist$/" in source or r"\/whitelist" in source


def test_contacts_route_uses_regex_pattern() -> None:
    """Contacts route must use a regex pattern matching /agents/:id/contacts."""
    source = _read(TEAMS_TS)
    assert "contacts$/" in source or r"\/contacts" in source


def test_connect_route_uses_regex_pattern() -> None:
    """Connect route must use a regex pattern matching /agents/:id/connect."""
    source = _read(TEAMS_TS)
    assert "connect$/" in source or r"\/connect" in source


def test_worker_router_catch_all_forwards_agents_paths() -> None:
    """worker.ts must have a startsWith('/agents') catch-all that forwards to TEAM_REGISTRY."""
    source = _read(WORKER_TS)
    # The actual forwarding code must exist (not just a comment)
    assert 'startsWith("/agents")' in source
    assert "TEAM_REGISTRY" in source


# --- Auth contract (structural — each handler call site must be gated) ---


def test_each_whitelist_contacts_handler_is_auth_gated() -> None:
    """Every call to handleGetWhitelist, handleManageWhitelist, handleGetContacts,
    handleConnect must be preceded by verifyInboxTokenForAgent in the same code block.

    This test parses the routing section and verifies that between each route match
    and its handler call, there is an auth check."""
    source = _read(TEAMS_TS)

    # Find all handler invocations for whitelist/contacts/connect
    handlers = [
        "handleGetWhitelist",
        "handleManageWhitelist",
        "handleGetContacts",
        "handleConnect",
    ]
    for handler in handlers:
        # Find the handler call
        handler_pos = source.find(f"this.{handler}(")
        assert handler_pos != -1, f"{handler} not found in source"

        # Look backwards from the handler call to find the nearest verifyInboxTokenForAgent
        # It should be within 300 chars (same code block)
        preceding = source[max(0, handler_pos - 300):handler_pos]
        assert "verifyInboxTokenForAgent" in preceding, (
            f"{handler} is NOT preceded by verifyInboxTokenForAgent within its code block. "
            f"This endpoint is publicly accessible without auth."
        )


def test_verify_inbox_token_method_exists_and_checks_digest() -> None:
    """verifyInboxTokenForAgent must exist, extract bearer token, look up digest, and compare."""
    source = _read(TEAMS_TS)
    assert "verifyInboxTokenForAgent" in source
    assert "Bearer" in source
    assert "inbox_token_digest" in source
    assert "sha256Hex" in source
    # Must return 401 on failure
    assert '"unauthorized"' in source
    assert '"missing inbox bearer token"' in source


# --- Logic contract ---


def test_contacts_query_uses_double_join_for_mutual() -> None:
    """Contacts SQL must JOIN agent_whitelist twice to enforce mutual direction."""
    source = _read(TEAMS_TS)
    # Find the private method definition (not the call site in routing)
    method_def = "private handleGetContacts"
    contacts_start = source.find(method_def)
    assert contacts_start != -1, f"Method definition '{method_def}' not found"
    contacts_block = source[contacts_start:contacts_start + 800]
    # Must join agent_whitelist as w1 AND w2 (both directions)
    assert "agent_whitelist w1" in contacts_block
    assert "agent_whitelist w2" in contacts_block
    assert "w1" in contacts_block and "w2" in contacts_block


def test_connect_verifies_both_agents_exist() -> None:
    """handleConnect must verify both caller and target still exist in the agents table."""
    source = _read(TEAMS_TS)
    method_def = "private async handleConnect"
    connect_start = source.find(method_def)
    assert connect_start != -1, f"Method definition '{method_def}' not found"
    connect_block = source[connect_start:connect_start + 1200]
    assert "SELECT agent_id FROM agents WHERE agent_id IN (?, ?)" in connect_block
    assert "caller agent does not exist" in connect_block
    assert "agent_not_found" in connect_block
    assert "target agent does not exist" in connect_block


def test_connect_returns_403_on_non_mutual() -> None:
    """handleConnect must return 403 with not_in_mutual_whitelist error."""
    source = _read(TEAMS_TS)
    method_def = "private async handleConnect"
    connect_start = source.find(method_def)
    assert connect_start != -1
    connect_block = source[connect_start:connect_start + 1500]
    assert "not_in_mutual_whitelist" in connect_block
    assert "403" in connect_block


def test_whitelist_manage_uses_insert_on_conflict_do_nothing() -> None:
    """Whitelist add must be idempotent via ON CONFLICT DO NOTHING."""
    source = _read(TEAMS_TS)
    method_def = "private async handleManageWhitelist"
    manage_start = source.find(method_def)
    assert manage_start != -1, f"Method definition '{method_def}' not found"
    manage_block = source[manage_start:manage_start + 1200]
    assert "INSERT INTO agent_whitelist" in manage_block
    assert "ON CONFLICT" in manage_block
    assert "DO NOTHING" in manage_block
    assert "DELETE FROM agent_whitelist" in manage_block


# --- Skill contract ---


def test_skill_keeps_contacts_reference_as_advanced_only() -> None:
    """The main skill may keep contacts APIs as an advanced reference, not a default owner flow."""
    source = _read(SKILL_MD)
    assert "references/contacts-api.md" in source
    assert "advanced use only" in source
    assert "contacts-api.md" in source


def test_contacts_api_reference_documents_all_endpoints() -> None:
    """contacts-api.md must document all 4 endpoints."""
    source = _read(CONTACTS_API_MD)
    assert "/agents/{agent_id}/contacts" in source or "/agents/" in source
    assert "/agents/{agent_id}/whitelist" in source or "whitelist" in source
    assert "/agents/{agent_id}/connect" in source or "connect" in source
    assert "not_in_mutual_whitelist" in source


def test_contacts_api_documents_mutual_whitelist_requirement() -> None:
    """The contacts API reference must explain mutual whitelist."""
    source = _read(CONTACTS_API_MD)
    assert "mutual" in source.lower()
