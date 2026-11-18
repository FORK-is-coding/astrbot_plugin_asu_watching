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

    def render_digest(self, summary, entries):
        return None


class FakeSummary:
    def __init__(self):
        self.labels = []

    async def summarize(self, items, umo=None, labels=None):
        self.labels = list(labels or [])
        return "整合摘要"


class FakeContext:
    def __init__(self):
        self.messages = []

    async def send_message(self, umo, chain):
        self.messages.append((umo, chain))
        return True


@pytest.mark.asyncio
async def test_push_batch_sends_one_message_with_combined_summary(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    sub_a = Subscription(id="a", umo="umo-1", target_key="github:LofiSu")
    sub_b = Subscription(id="b", umo="umo-1", target_key="xhs:620103f00000000021029b87")
    await store.add_subscription(sub_a)
    await store.add_subscription(sub_b)
    context = FakeContext()
    summary = FakeSummary()
    pusher = Pusher(context, store, FakeConfig(), FakeCard(), summary)
    target_a = Target(platform="github", target_id="LofiSu", display_name="LofiSu")
    target_b = Target(platform="xhs", target_id="620103f00000000021029b87", display_name="阿酥在coding")
    entries = [
        (sub_a, target_a, [FeedItem(id="1", title="GitHub 动态", text="", url="u1")]),
        (sub_b, target_b, [FeedItem(id="2", title="小红书笔记", text="", url="u2")]),
    ]

    assert await pusher.push_batch("umo-1", entries) is True

    assert len(context.messages) == 1
    text = str(context.messages[0][1].chain[-1].text)
    assert "AI 摘要：整合摘要" in text
    assert "GitHub 动态" in text
    assert "小红书笔记" in text
    assert summary.labels == ["LofiSu", "阿酥在coding"]
    saved = await store.list_subscriptions("umo-1")
    assert {tuple(item.seen_ids) for item in saved} == {("1",), ("2",)}
