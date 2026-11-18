from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.core.models import FeedItem, Subscription, Target
from astrbot_plugin_creator_feed.core.scheduler import Scheduler
from astrbot_plugin_creator_feed.core.store import JsonStore


class FakeConfig(dict):
    def save_config(self):
        pass


class FakeAdapter:
    def __init__(self, items):
        self.items = items

    async def fetch(self, target, limit=20):
        return list(self.items)


class FakePusher:
    def __init__(self):
        self.pushed = []

    async def push(self, subscription, target, items):
        self.pushed.append((target.key, [item.id for item in items]))
        return True


@pytest.mark.asyncio
async def test_scheduler_filters_push_events_whitelist(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    subscription = Subscription(
        id="1",
        umo="umo-1",
        target_key="github:LofiSu",
        seen_ids=[],
        baseline_done=True,
    )
    await store.add_subscription(subscription)
    adapter = FakeAdapter(
        [
            FeedItem(
                id="push-1",
                title="push",
                text="",
                url="u1",
                author="LofiSu/repo",
                event_type="PushEvent",
            ),
            FeedItem(
                id="release-1",
                title="release",
                text="",
                url="u2",
                author="LofiSu/repo",
                event_type="ReleaseEvent",
            ),
        ]
    )
    pusher = FakePusher()
    scheduler = Scheduler(
        context=None,
        store=store,
        adapters={"github": adapter},
        config=FakeConfig(),
        pusher=pusher,
    )

    await scheduler._check_target(
        Target(platform="github", target_id="LofiSu", display_name="LofiSu"),
        {"push_events": ["ReleaseEvent"]},
    )

    assert pusher.pushed == [("github:LofiSu", ["release-1"])]
