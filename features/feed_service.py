from __future__ import annotations

import re
import uuid

import aiohttp

from ..core.models import FeedItem, Subscription, Target
from ..core.store import JsonStore
from ..platforms.base import PlatformAdapter, PlatformError
from ..platforms.xiaohongshu import parse_user_id

XHS_NOTE_RE = re.compile(r"/explore/([0-9a-f]{24})")
GITHUB_RE = re.compile(r"github\.com/([^/?#]+)")
X_HANDLE_RE = re.compile(r"(?:x|twitter)\.com/([^/?#]+)")
XHS_PROFILE_RE = re.compile(r"xiaohongshu\.com/user/profile/([0-9a-f]{24})")


class ServiceError(RuntimeError):
    """User-facing command failure."""


class FeedService:
    def __init__(
        self,
        store: JsonStore,
        adapters: dict[str, PlatformAdapter],
        config,
    ):
        self.store = store
        self.adapters = adapters
        self.config = config

    def adapter(self, platform: str) -> PlatformAdapter:
        adapter = self.adapters.get(platform)
        if adapter is None:
            raise ServiceError(f"不支持的平台：{platform}（可选 github / x / xhs）")
        return adapter

    async def add(
        self,
        umo: str,
        platform: str,
        raw_target: str,
        alias: str = "",
    ) -> tuple[Target, Subscription, FeedItem | None]:
        adapter = self.adapter(platform)
        try:
            info = await adapter.resolve(raw_target)
        except PlatformError as exc:
            raise ServiceError(str(exc)) from exc

        target = Target(
            platform=platform,
            target_id=info.target_id,
            display_name=alias or info.display_name or info.target_id,
            avatar_url=info.avatar_url,
        )
        if await self.store.find_subscription(umo, target.key):
            raise ServiceError("这个会话已经订阅了该账号")

        try:
            baseline = await adapter.fetch(target, limit=20)
        except PlatformError as exc:
            raise ServiceError(str(exc)) from exc

        subscription = Subscription(
            id=uuid.uuid4().hex[:8],
            umo=umo,
            target_key=target.key,
            seen_ids=[item.id for item in baseline][-200:],
            last_item_at=baseline[-1].published_at if baseline else None,
            baseline_done=True,
            source="command",
        )
        await self.store.upsert_target(target)
        await self.store.cache_items(target.key, list(reversed(baseline)))
        created = await self.store.add_subscription_unique(subscription)
        if not created:
            raise ServiceError("这个会话已经订阅了该账号")
        await self.store.unsuppress(umo, target.key)
        sample = baseline[-1] if baseline else None
        return target, subscription, sample

    async def list_subscriptions(self, umo: str) -> list[tuple[int, Subscription, Target | None]]:
        subscriptions = await self.store.list_subscriptions(umo)
        result = []
        for index, subscription in enumerate(subscriptions, start=1):
            result.append(
                (index, subscription, await self.store.get_target(subscription.target_key))
            )
        return result

    async def remove(self, umo: str, identifier: str) -> Subscription:
        subscription = await self._select(umo, identifier)
        await self.store.remove_subscription(subscription.id)
        await self.store.suppress(subscription.umo, subscription.target_key)
        return subscription

    async def toggle(self, umo: str, identifier: str, enabled: bool) -> Subscription:
        subscription = await self._select(umo, identifier)
        await self.store.update_subscription_fields(subscription.id, enabled=enabled)
        subscription.enabled = enabled
        return subscription

    async def check(self, platform: str, raw_target: str) -> tuple[Target, list[FeedItem]]:
        adapter = self.adapter(platform)
        try:
            info = await adapter.resolve(raw_target)
            target = Target(
                platform=platform,
                target_id=info.target_id,
                display_name=info.display_name or info.target_id,
                avatar_url=info.avatar_url,
            )
            items = await adapter.fetch(target, limit=10)
        except PlatformError as exc:
            raise ServiceError(str(exc)) from exc
        return target, items

    async def run_now(
        self, umo: str
    ) -> tuple[list[tuple[Subscription, Target, list[FeedItem]]], list[tuple[str, str]]]:
        results = []
        errors = []
        for _, subscription, target in await self.list_subscriptions(umo):
            if not subscription.enabled:
                continue
            if target is None:
                platform, target_id = subscription.target_key.split(":", 1)
                target = Target(
                    platform=platform,
                    target_id=target_id,
                    display_name=target_id,
                )
            adapter = self.adapter(target.platform)
            try:
                items = await adapter.fetch(target, limit=20)
            except PlatformError as exc:
                errors.append((target.display_name, str(exc)))
                continue
            seen = set(subscription.seen_ids)
            fresh = [item for item in items if item.id not in seen]
            results.append((subscription, target, fresh))
        return results, errors

    async def mark_pushed(self, subscription: Subscription, items: list[FeedItem]) -> None:
        fields = {
            "seen_ids": (subscription.seen_ids + [item.id for item in items])[-200:],
            "last_error": None,
        }
        if items:
            fields["last_item_at"] = items[-1].published_at or subscription.last_item_at
        await self.store.update_subscription_fields(subscription.id, **fields)

    async def resolve_link(self, raw: str) -> str:
        text = raw.strip()
        lower = text.lower()

        if "xhslink.com" in lower:
            text = await self._expand_short_link(text)
            lower = text.lower()

        if "xiaohongshu.com" in lower:
            profile = XHS_PROFILE_RE.search(text)
            if profile:
                return f"xhs {profile.group(1)}"
            note = XHS_NOTE_RE.search(text)
            if note:
                raise ServiceError("这是笔记链接，不是账号主页；请给小红书主页链接")
            return f"xhs {parse_user_id(text)}"

        if "github.com" in lower:
            match = GITHUB_RE.search(text)
            if match:
                owner = match.group(1)
                if owner not in {"orgs", "users", "features"}:
                    return f"github {owner}"
            raise ServiceError("无法从 GitHub 链接解析账号")

        if "x.com" in lower or "twitter.com" in lower:
            match = X_HANDLE_RE.search(text)
            if match:
                handle = match.group(1)
                if handle not in {"i", "home", "search", "hashtag"}:
                    return f"x {handle}"
            raise ServiceError("无法从 X 链接解析账号")

        raise ServiceError("无法识别链接：支持小红书主页、github.com、x.com/twitter.com")

    async def _expand_short_link(self, url: str) -> str:
        try:
            async with aiohttp.ClientSession(
                trust_env=True, timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    final_url = str(resp.url)
        except aiohttp.ClientError as exc:
            raise ServiceError(f"小红书短链解析失败：{exc}") from exc
        if "xiaohongshu.com" not in final_url:
            raise ServiceError("小红书短链没有跳转到主页")
        return final_url

    async def _select(self, umo: str, identifier: str) -> Subscription:
        subscriptions = await self.store.list_subscriptions(umo)
        if not subscriptions:
            raise ServiceError("当前会话还没有订阅")
        if identifier.isdigit():
            index = int(identifier)
            if 1 <= index <= len(subscriptions):
                return subscriptions[index - 1]
        for subscription in subscriptions:
            if subscription.target_key == identifier:
                return subscription
            if subscription.target_key.endswith(f":{identifier}"):
                return subscription
        raise ServiceError(f"找不到订阅：{identifier}")
