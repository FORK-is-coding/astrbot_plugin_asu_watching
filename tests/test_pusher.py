from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.core.models import FeedItem, Subscription, Target
from astrbot_plugin_creator_feed.core.pusher import Pusher
from astrbot_plugin_creator_feed.core.store import JsonStore


class FakeConfig(dict):
    def save_config(self):
        pass


class FakeCard:
    enabled = False

    def render(self, target, items):
        return None


class FakeContext:
    def __init__(self, result=True):
        self.result = result
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))
        return self.result


@pytest.mark.asyncio
async def test_push_marks_seen_on_success(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    subscription = Subscription(id="1", umo="umo-1", target_key="github:LofiSu")
    await store.add_subscription(subscription)
    context = FakeContext(result=True)
    pusher = Pusher(context, store, FakeConfig(), FakeCard())
    target = Target(platform="github", target_id="LofiSu", display_name="LofiSu")

    ok = await pusher.push(
        subscription,
        target,
        [FeedItem(id="a", title="t", text="", url="u")],
    )

    assert ok is True
    assert (await store.list_subscriptions("umo-1"))[0].seen_ids == ["a"]


@pytest.mark.asyncio
async def test_push_keeps_cursor_when_send_fails(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    subscription = Subscription(id="1", umo="umo-1", target_key="github:LofiSu")
    await store.add_subscription(subscription)
    pusher = Pusher(FakeContext(result=False), store, FakeConfig(), FakeCard())
    target = Target(platform="github", target_id="LofiSu", display_name="LofiSu")

    ok = await pusher.push(
        subscription,
        target,
        [FeedItem(id="a", title="t", text="", url="u")],
    )

    assert ok is False
    saved = (await store.list_subscriptions("umo-1"))[0]
    assert saved.seen_ids == []
    assert saved.last_error == "发送失败"
