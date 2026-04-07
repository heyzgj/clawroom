from __future__ import annotations

import pytest

from clawroom_client_core import parse_join_url


def test_parse_join_url_api_join_form() -> None:
    parsed = parse_join_url("https://api.clawroom.cc/join/room_abc123?token=inv_foo")
    assert parsed["base_url"] == "https://api.clawroom.cc"
    assert parsed["room_id"] == "room_abc123"
    assert parsed["token"] == "inv_foo"


def test_parse_join_url_join_info_form() -> None:
    parsed = parse_join_url("https://api.clawroom.cc/rooms/room_xyz/join_info?token=inv_bar")
    assert parsed["base_url"] == "https://api.clawroom.cc"
    assert parsed["room_id"] == "room_xyz"
    assert parsed["token"] == "inv_bar"


def test_parse_join_url_rewrites_ui_host() -> None:
    parsed = parse_join_url("https://clawroom.cc/join/room_ui?token=inv_ui")
    assert parsed["base_url"] == "https://api.clawroom.cc"
    assert parsed["room_id"] == "room_ui"
    assert parsed["token"] == "inv_ui"


def test_parse_join_url_invalid_raises() -> None:
    with pytest.raises(ValueError):
        parse_join_url("https://api.clawroom.cc/join/room_missing_token")
