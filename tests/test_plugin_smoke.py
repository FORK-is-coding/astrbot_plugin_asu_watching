from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.main import CreatorFeedPlugin


class FakeConfig(dict):
    def save_config(self):
        pass


class FakeContext:
    def __init__(self):
        self.routes = []
        self.provider = None

    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, tuple(methods)))

    def get_using_provider(self):
        return self.provider

    def get_config(self, umo=None):
        return {"provider_settings": {"web_search": False}}


@pytest.mark.asyncio
async def test_panel_routes_are_registered(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path))
    context = FakeContext()
    plugin = CreatorFeedPlugin(context, FakeConfig())

    routes = {route for route, _methods in context.routes}

    assert "/astrbot_plugin_creator_feed/panel/status" in routes
    assert "/astrbot_plugin_creator_feed/panel/xhs/login/start" in routes
    assert "/astrbot_plugin_creator_feed/panel/xhs/cookie" in routes
    assert "/astrbot_plugin_creator_feed/panel/subscriptions" in routes
