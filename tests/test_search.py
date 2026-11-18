from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.features.search_service import SearchService


class FakeResponse:
    def __init__(self, text: str):
        self.completion_text = text


class FakeProvider:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    async def text_chat(self, **_kwargs):
        self.calls += 1
        return FakeResponse(self.text)


class FakeEvent:
    unified_msg_origin = "umo-test"


class FakeContext:
    def __init__(self, provider):
        self.provider = provider

    async def get_using_provider_async(self, umo=None):
        return self.provider

    def get_config(self, umo=None):
        return {"provider_settings": {"web_search": True, "websearch_provider": "tavily"}}


@pytest.mark.asyncio
async def test_builtin_search_is_preferred(monkeypatch):
    provider = FakeProvider("model answer")
    service = SearchService(FakeContext(provider), {})

    async def fake_builtin(keyword, limit, event):
        return "- builtin result\n  https://example.com"

    monkeypatch.setattr(service, "_builtin_search", fake_builtin)

    result = await service.search("关键词", event=FakeEvent())

    assert "builtin result" in result
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_model_used_when_builtin_returns_nothing(monkeypatch):
    provider = FakeProvider("model answer https://example.com")
    service = SearchService(FakeContext(provider), {})

    async def fake_builtin(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(service, "_builtin_search", fake_builtin)

    result = await service.search("关键词", event=FakeEvent())

    assert "model answer https://example.com" in result
    assert "未使用内置搜索" in result
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_builtin_can_be_disabled(monkeypatch):
    provider = FakeProvider("model answer")
    service = SearchService(
        FakeContext(provider), {"search_builtin_fallback": False}
    )

    async def fake_builtin(*_args, **_kwargs):
        raise AssertionError("builtin should not run")

    monkeypatch.setattr(service, "_builtin_search", fake_builtin)

    result = await service.search("关键词", event=FakeEvent())

    assert "model answer" in result
    assert "未使用内置搜索" in result
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_model_failure_reports_error(monkeypatch):
    class BrokenProvider:
        async def text_chat(self, **_kwargs):
            raise RuntimeError("provider down")

    service = SearchService(FakeContext(BrokenProvider()), {})

    async def fake_builtin(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(service, "_builtin_search", fake_builtin)

    result = await service.search("关键词", event=FakeEvent())

    assert "搜索失败" in result
    assert "provider down" in result
