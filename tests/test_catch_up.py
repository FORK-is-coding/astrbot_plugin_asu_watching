from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.core.models import FeedItem
from astrbot_plugin_creator_feed.main import CreatorFeedPlugin


class FakeConfig(dict):
    def save_config(self):
        pass


class FakeContext:
    def register_web_api(self, *_args):
        return None

    def get_config(self, umo=None):
        return {"provider_settings": {"web_search": False}}

    async def get_using_provider_async(self, umo=None):
        return None


class FakeAdapter:
    def __init__(self, platform, items):
        self.platform = platform
        self.items = items

    async def fetch(self, target, limit=20):
        return list(self.items)

    async def close(self):
        pass


class FakeSummary:
    async def summarize(self, items, umo=None, labels=None):
        return "整合摘要"


class FakeEvent:
    unified_msg_origin = "aiocqhttp:GroupMessage:1"

    def is_admin(self):
        return True

    def get_sender_id(self):
        return "1"

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)


@pytest.mark.asyncio
async def test_catch_up_command_combines_all_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path))
    plugin = CreatorFeedPlugin(
        FakeContext(),
        FakeConfig(
            card_enabled=False,
            watch_targets=[
                {"platform": "github", "target": "LofiSu", "display_name": "LofiSu"},
                {"platform": "xhs", "target": "620103f00000000021029b87", "display_name": "阿酥在coding"},
            ],
        ),
    )
    plugin.adapters = {
        "github": FakeAdapter(
            "github", [FeedItem(id="1", title="GitHub 动态", text="", url="u1")]
        ),
        "xhs": FakeAdapter(
            "xhs", [FeedItem(id="2", title="小红书笔记", text="", url="u2")]
        ),
    }
    plugin.summary = FakeSummary()

    results = [result async for result in plugin.catch_up_all(FakeEvent())]

    assert len(results) == 1
    kind, chain = results[0]
    assert kind == "chain"
    text = str(chain[-1].text)
    assert "整合摘要" in text
    assert "GitHub 动态" in text
    assert "小红书笔记" in text
    assert "LofiSu" in text
    assert "阿酥在coding" in text
