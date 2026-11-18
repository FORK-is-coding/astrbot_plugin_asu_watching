from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.core.models import FeedItem, Subscription, Target
from astrbot_plugin_creator_feed.core.store import JsonStore


@pytest.mark.asyncio
async def test_store_roundtrip(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()

    target = Target(platform="github", target_id="LofiSu", display_name="LofiSu")
    await store.upsert_target(target)
    subscription = Subscription(id="abc", umo="umo-1", target_key=target.key)
    await store.add_subscription(subscription)
    await store.cache_items(
        target.key,
        [FeedItem(id="1", title="t", text="x", url="https://example.com")],
    )

    reloaded = JsonStore(tmp_path / "state.json")
    await reloaded.load()

    assert (await reloaded.get_target(target.key)).display_name == "LofiSu"
    assert [item.id for item in await reloaded.list_subscriptions("umo-1")] == ["abc"]
    assert [item.id for item in await reloaded.get_cached_items(target.key)] == ["1"]


@pytest.mark.asyncio
async def test_subscription_update_and_remove(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    subscription = Subscription(id="abc", umo="umo-1", target_key="github:LofiSu")
    await store.add_subscription(subscription)

    subscription.enabled = False
    subscription.seen_ids = ["1", "2"]
    await store.update_subscription(subscription)

    saved = (await store.list_subscriptions("umo-1"))[0]
    assert saved.enabled is False
    assert saved.seen_ids == ["1", "2"]

    assert await store.remove_subscription("abc") is True
    assert await store.remove_subscription("abc") is False
    assert await store.list_subscriptions("umo-1") == []
