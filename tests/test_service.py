from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.core.models import FeedItem
from astrbot_plugin_creator_feed.core.store import JsonStore
from astrbot_plugin_creator_feed.features.feed_service import FeedService, ServiceError
from astrbot_plugin_creator_feed.platforms.base import PlatformAdapter, TargetInfo


class FakeConfig(dict):
    def save_config(self):
        pass


class FakeAdapter(PlatformAdapter):
    name = "github"

    def __init__(self, items: list[FeedItem]):
        self.items = items

    async def resolve(self, target: str) -> TargetInfo:
        return TargetInfo(
            platform=self.name,
            target_id="LofiSu",
            display_name="LofiSu",
            avatar_url="https://example.com/a.png",
        )

    async def fetch(self, target, limit: int = 20) -> list[FeedItem]:
        return list(self.items)


@pytest.mark.asyncio
async def test_add_baselines_and_remembers_session(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    config = FakeConfig(push_sessions=[])
    service = FeedService(store, {"github": FakeAdapter(
        [
            FeedItem(id="1", title="old", text="", url="u1"),
            FeedItem(id="2", title="new", text="", url="u2"),
        ]
    )}, config)

    target, subscription, sample = await service.add(
        "umo-1", "github", "LofiSu", alias=""
    )

    assert target.key == "github:LofiSu"
    assert subscription.seen_ids == ["1", "2"]
    assert subscription.baseline_done is True
    assert sample.id == "2"
    assert config.get("push_sessions") == []

    with pytest.raises(ServiceError):
        await service.add("umo-1", "github", "LofiSu")


@pytest.mark.asyncio
async def test_run_now_returns_only_unseen(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    adapter = FakeAdapter([FeedItem(id="1", title="old", text="", url="u1")])
    service = FeedService(store, {"github": adapter}, FakeConfig())

    _target, subscription, _sample = await service.add("umo-1", "github", "LofiSu")
    results, errors = await service.run_now("umo-1")
    assert errors == []
    assert results[0][2] == []

    adapter.items.append(FeedItem(id="2", title="new", text="", url="u2"))
    results, errors = await service.run_now("umo-1")
    fresh = results[0][2]
    assert [item.id for item in fresh] == ["2"]

    await service.mark_pushed(subscription, fresh)
    saved = (await store.list_subscriptions("umo-1"))[0]
    assert saved.seen_ids == ["1", "2"]
    results, errors = await service.run_now("umo-1")
    assert results[0][2] == []


@pytest.mark.asyncio
async def test_toggle_and_remove(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    service = FeedService(
        store,
        {"github": FakeAdapter([FeedItem(id="1", title="t", text="", url="u")])},
        FakeConfig(),
    )
    await service.add("umo-1", "github", "LofiSu")

    await service.toggle("umo-1", "1", False)
    assert (await service.list_subscriptions("umo-1"))[0][1].enabled is False

    await service.remove("umo-1", "1")
    assert await service.list_subscriptions("umo-1") == []

    with pytest.raises(ServiceError):
        await service.remove("umo-1", "1")
