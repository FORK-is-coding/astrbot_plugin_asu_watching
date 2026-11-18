from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.core.models import FeedItem
from astrbot_plugin_creator_feed.features.summary_service import SummaryService


class FakeResponse:
    def __init__(self, text):
        self.completion_text = text


class FakeProvider:
    def __init__(self):
        self.prompts = []

    async def text_chat(self, prompt=None, system_prompt=None, **_kwargs):
        self.prompts.append(prompt)
        return FakeResponse("这是一段 AI 摘要。")


class FakeContext:
    def __init__(self, provider):
        self.provider = provider

    async def get_using_provider_async(self, umo=None):
        return self.provider


@pytest.mark.asyncio
async def test_summary_uses_astrbot_provider():
    provider = FakeProvider()
    service = SummaryService(
        FakeContext(provider), {"summary_enabled": True, "summary_max_chars": 100}
    )
    items = [FeedItem(id="1", title="标题", text="正文", url="u")]

    result = await service.summarize(items, umo="umo-1")

    assert result == "这是一段 AI 摘要。"
    assert provider.prompts


@pytest.mark.asyncio
async def test_summary_disabled_returns_empty():
    service = SummaryService(FakeContext(FakeProvider()), {"summary_enabled": False})

    result = await service.summarize(
        [FeedItem(id="1", title="标题", text="", url="u")]
    )

    assert result == ""
