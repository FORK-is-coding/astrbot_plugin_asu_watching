from __future__ import annotations

import json

import pytest

from astrbot_plugin_creator_feed.features.xhs_login import XhsLoginError, XhsLoginManager


def test_save_cookie_string(tmp_path):
    manager = XhsLoginManager(tmp_path / "xhs_auth.json")
    manager.save_cookie_string("web_session=abc; a1=def; webId=123")

    payload = json.loads((tmp_path / "xhs_auth.json").read_text(encoding="utf-8"))
    assert payload["cookies"]["web_session"] == "abc"
    assert payload["cookies"]["a1"] == "def"
    assert manager.logged_in() is True


def test_save_cookie_requires_web_session(tmp_path):
    manager = XhsLoginManager(tmp_path / "xhs_auth.json")
    with pytest.raises(XhsLoginError):
        manager.save_cookie_string("a1=def; webId=123")
