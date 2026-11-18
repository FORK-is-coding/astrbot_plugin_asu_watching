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


class RecordingContext:
    def __init__(self):
        self.messages = []

    async def send_message(self, umo, chain):
        self.messages.append((umo, chain))
        return True


@pytest.mark.asyncio
async def test_github_pushes_are_aggregated_in_message(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    await store.load()
    subscription = Subscription(id="1", umo="umo-1", target_key="github:LofiSu")
    await store.add_subscription(subscription)
    context = RecordingContext()
    pusher = Pusher(context, store, FakeConfig(), FakeCard())
    target = Target(platform="github", target_id="LofiSu", display_name="LofiSu")
    items = [
        FeedItem(
            id="1",
            title="LofiSu/repo 有 1 个新提交",
            text="",
            url="https://github.com/LofiSu/repo/commit/1",
            author="LofiSu/repo",
            event_type="PushEvent",
        ),
        FeedItem(
            id="2",
            title="LofiSu/repo 有 2 个新提交",
            text="",
            url="https://github.com/LofiSu/repo/commit/2",
            author="LofiSu/repo",
            event_type="PushEvent",
        ),
        FeedItem(
            id="3",
            title="LofiSu/repo 发布了 v1",
            text="",
            url="https://github.com/LofiSu/repo/releases/tag/v1",
            author="LofiSu/repo",
            event_type="ReleaseEvent",
        ),
    ]

    assert await pusher.push(subscription, target, items) is True

    sent_text = str(context.messages[0][1].chain[-1].text)
    assert "有 2 次推送" in sent_text
    assert "发布了 v1" in sent_text
    assert "有 1 个新提交" not in sent_text
    assert (await store.list_subscriptions("umo-1"))[0].seen_ids == ["1", "2", "3"]
