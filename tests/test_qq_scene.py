from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.core.models import Subscription
from astrbot_plugin_creator_feed.main import CreatorFeedPlugin


class FakeConfig(dict):
    def save_config(self):
        pass


class FakePlatform:
    def __init__(self):
        self.scenes = []

    def remember_session_scene(self, session_id, scene):
        self.scenes.append((session_id, scene))


class FakeContext:
    def __init__(self):
        self.platform = FakePlatform()

    def register_web_api(self, *_args):
        return None

    def get_platform_inst(self, platform_id):
        return self.platform if platform_id == "qq_official" else None


@pytest.mark.asyncio
async def test_remember_qq_scene_for_command_subscription(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path))
    context = FakeContext()
    plugin = CreatorFeedPlugin(context, FakeConfig(push_sessions=[]))
    await plugin.store.load()
    await plugin.store.add_subscription(
        Subscription(
            id="1",
            umo="qq_official:GroupMessage:group-1",
            target_key="github:LofiSu",
            source="command",
        )
    )

    await plugin._remember_qq_sessions()

    assert context.platform.scenes == [("group-1", "group")]
