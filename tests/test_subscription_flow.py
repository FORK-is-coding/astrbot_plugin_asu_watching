from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.core.models import FeedItem, Subscription, Target
from astrbot_plugin_creator_feed.core.scheduler import Scheduler
from astrbot_plugin_creator_feed.core.store import JsonStore
from astrbot_plugin_creator_feed.features.feed_service import FeedService


class FakeConfig(dict):
    def save_config(self):
        pass


class FakeAdapter:
    def __init__(self, items):
        self.items = items

    async def fetch(self, target, limit=20):
        return list(self.items)

    async def resolve(self, target):
        from astrbot_plugin_creator_feed.platforms.base import TargetInfo

        return TargetInfo(platform="github", target_id=target, display_name=target)

    async def close(self):
        pass


class FakePusher:
    def __init__(self):
        self.pushed = []

    async def push(self, subscription, target, items):
        self.pushed.append((subscription.umo, target.key, [item.id for item in items]))
        return True

    async def push_batch(self, umo, entries):
        for subscription, target, items in entries:
            self.pushed.append(
                (umo, target.key, [item.id for item in items])
            )
        return True


@pytest.mark.asyncio
async def test_tick_polls_command_subscription_target(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    await store.add_subscription(
        Subscription(
            id="1",
            umo="umo-1",
            target_key="github:LofiSu",
            baseline_done=True,
            seen_ids=["1"],
        )
    )
    adapter = FakeAdapter(
        [FeedItem(id="1", title="old", text="", url="u1"), FeedItem(id="2", title="new", text="", url="u2")]
    )
    pusher = FakePusher()
    scheduler = Scheduler(
        context=None,
        store=store,
        adapters={"github": adapter},
        config=FakeConfig(watch_targets=[], push_sessions=[]),
        pusher=pusher,
    )

    await scheduler.tick()

    assert pusher.pushed == [("umo-1", "github:LofiSu", ["2"])]


@pytest.mark.asyncio
async def test_suppressed_config_subscription_is_not_recreated(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    await store.suppress("umo-1", "github:LofiSu")
    pusher = FakePusher()
    scheduler = Scheduler(
        context=None,
        store=store,
        adapters={"github": FakeAdapter([])},
        config=FakeConfig(
            watch_targets=[
                {
                    "platform": "github",
                    "target": "LofiSu",
                    "display_name": "LofiSu",
                    "enabled": True,
                    "interval_min": 10,
                }
            ],
            push_sessions=["umo-1"],
        ),
        pusher=pusher,
    )

    await scheduler.tick()

    assert await store.list_subscriptions("umo-1") == []


@pytest.mark.asyncio
async def test_check_does_not_write_state(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    service = FeedService(
        store,
        {"github": FakeAdapter([FeedItem(id="1", title="x", text="", url="u")])},
        FakeConfig(),
    )

    await service.check("github", "LofiSu")

    assert await store.list_targets() == []
