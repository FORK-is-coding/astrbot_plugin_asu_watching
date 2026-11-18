from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from astrbot.api.event import MessageChain

from .models import Subscription, Target
from .pusher import Pusher
from .store import JsonStore

logger = logging.getLogger("astrbot")


class Scheduler:
    def __init__(
        self,
        context,
        store: JsonStore,
        adapters: dict,
        config,
        pusher: Pusher,
    ):
        self.context = context
        self.store = store
        self.adapters = adapters
        self.config = config
        self.pusher = pusher
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="creator-feed-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(15)
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("创作者订阅调度异常")
            await asyncio.sleep(30)

    def _entries(self) -> dict[str, dict]:
        entries = {}
        for entry in self.config.get("watch_targets") or []:
            if not entry.get("enabled", True):
                continue
            platform = str(entry.get("platform", "")).strip().lower()
            target_id = str(entry.get("target", "")).strip()
            if platform and target_id:
                entries[f"{platform}:{target_id}"] = entry
        return entries

    async def sync_subscriptions(self) -> None:
        await self._sync_config_subscriptions(self._entries())

    async def tick(self) -> None:
        entries = self._entries()
        await self._sync_config_subscriptions(entries)

        targets: dict[str, Target] = {}
        for key, entry in entries.items():
            platform, target_id = key.split(":", 1)
            targets[key] = Target(
                platform=platform,
                target_id=target_id,
                display_name=str(entry.get("display_name") or target_id),
            )
        for subscription in await self.store.list_subscriptions():
            if subscription.target_key in targets:
                continue
            if ":" not in subscription.target_key:
                continue
            platform, target_id = subscription.target_key.split(":", 1)
            targets[subscription.target_key] = Target(
                platform=platform,
                target_id=target_id,
                display_name=target_id,
            )

        pending: dict[str, list] = {}
        for key, target in targets.items():
            stored = await self.store.get_target(key)
            if stored:
                target.failures = stored.failures
                target.paused_reason = stored.paused_reason
                target.avatar_url = stored.avatar_url
                target.last_check_at = stored.last_check_at
                if stored.display_name:
                    target.display_name = stored.display_name
            if target.last_check_at:
                if target.failures:
                    delay = min(30, 2 ** min(target.failures, 5))
                else:
                    delay = max(1, int(entries.get(key, {}).get("interval_min", 15)))
                next_run = storage_time(target.last_check_at) + timedelta(minutes=delay)
                if datetime.now(timezone.utc) < next_run:
                    continue
            await self._check_target(target, entries.get(key, {}), pending)

        for umo, batch in pending.items():
            await self.pusher.push_batch(umo, batch)

    async def _notify_failure(self, target: Target, exc: Exception) -> None:
        text = (
            f"创作者订阅：{target.display_name} 连续失败 5 次（{exc}）。"
            "仍会按退避重试；可在面板重新登录/检查。"
        )
        for umo in self.config.get("push_sessions") or []:
            try:
                await self.context.send_message(
                    str(umo), MessageChain().message(text)
                )
            except Exception:
                logger.warning("失败通知发送失败：%s", umo)

    async def _sync_config_subscriptions(self, entries: dict[str, dict]) -> None:
        sessions = {str(session) for session in self.config.get("push_sessions") or []}
        subscriptions = await self.store.list_subscriptions()
        for subscription in subscriptions:
            if subscription.source != "config":
                continue
            if (
                subscription.umo not in sessions
                or subscription.target_key not in entries
            ):
                await self.store.remove_subscription(subscription.id)

        for session in sessions:
            for key in entries:
                if self.store.is_suppressed(session, key):
                    continue
                if await self.store.find_subscription(session, key):
                    continue
                await self.store.add_subscription(
                    Subscription(
                        id=uuid.uuid4().hex[:8],
                        umo=session,
                        target_key=key,
                        baseline_done=False,
                        source="config",
                    )
                )

    async def _check_target(
        self, target: Target, entry: dict, pending: dict | None = None
    ) -> None:
        adapter = self.adapters.get(target.platform)
        if adapter is None:
            return
        try:
            items = await adapter.fetch(target, limit=20)
        except Exception as exc:
            target.failures += 1
            target.last_check_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            target.paused_reason = str(exc)
            await self.store.upsert_target(target)
            logger.warning("拉取 %s 失败：%s", target.key, exc)
            if target.failures == 5:
                await self._notify_failure(target, exc)
            return

        target.failures = 0
        target.paused_reason = None
        target.last_check_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await self.store.upsert_target(target)
        await self.store.cache_items(target.key, list(reversed(items)), limit=50)

        if self._in_quiet_hours():
            return

        include = [str(word) for word in entry.get("include") or []]
        exclude = [str(word) for word in entry.get("exclude") or []]
        push_events = entry.get("push_events") or []

        subscriptions = [
            subscription
            for subscription in await self.store.list_subscriptions()
            if subscription.target_key == target.key
            and subscription.enabled
            and not self.store.is_suppressed(subscription.umo, target.key)
        ]
        for subscription in subscriptions:
            if not subscription.baseline_done:
                await self.store.update_subscription_fields(
                    subscription.id,
                    seen_ids=[item.id for item in items][-200:],
                    baseline_done=True,
                    last_item_at=items[-1].published_at if items else None,
                )
                continue

            seen = set(subscription.seen_ids)
            fresh = [
                item
                for item in items
                if item.id not in seen
                and self._matches(item, include, exclude)
                and (not push_events or item.event_type in push_events)
            ]
            if fresh:
                if pending is None:
                    await self.pusher.push(subscription, target, fresh)
                else:
                    pending.setdefault(subscription.umo, []).append(
                        (subscription, target, fresh)
                    )

    @staticmethod
    def _matches(item, include: list[str], exclude: list[str]) -> bool:
        text = f"{item.title} {item.text}".lower()
        if include and not any(word.lower() in text for word in include):
            return False
        if exclude and any(word.lower() in text for word in exclude):
            return False
        return True

    def _in_quiet_hours(self) -> bool:
        raw = str(self.config.get("quiet_hours", "")).strip()
        if not raw or "-" not in raw:
            return False
        start_text, end_text = raw.split("-", 1)
        try:
            start = datetime.strptime(start_text.strip(), "%H:%M").time()
            end = datetime.strptime(end_text.strip(), "%H:%M").time()
        except ValueError:
            return False
        now = datetime.now(ZoneInfo(str(self.config.get("timezone", "Asia/Shanghai")))).time()
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end


def storage_time(value: str) -> datetime:
    return datetime.fromisoformat(value)
