from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiohttp

from ..core.models import FeedItem, Target
from .base import PlatformAdapter, PlatformError, TargetInfo

FX_BASE = "https://api.fxtwitter.com"
X_API_BASE = "https://api.twitter.com/2"


def _timestamp_to_iso(value) -> str | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError, OSError):
        return None


class TwitterAdapter(PlatformAdapter):
    name = "x"

    def __init__(self, provider: str = "fxtwitter", bearer_token: str = ""):
        self.provider = provider
        self.bearer_token = bearer_token.strip()
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"User-Agent": "astrbot-plugin-creator-feed"}
            if self.provider == "x_api" and self.bearer_token:
                headers["Authorization"] = f"Bearer {self.bearer_token}"
            self._session = aiohttp.ClientSession(
                headers=headers,
                trust_env=True,
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_json(self, url: str, params: dict | None = None):
        try:
            async with self._client().get(url, params=params) as resp:
                if resp.status == 204:
                    return None, 204
                if resp.status == 401:
                    raise PlatformError("X Bearer Token 无效")
                if resp.status == 402:
                    raise PlatformError("X API 余额/额度不足")
                if resp.status == 403:
                    raise PlatformError("X API 无读取权限")
                if resp.status == 429:
                    raise PlatformError("X API 触发限流，请稍后重试")
                if resp.status == 404:
                    return None, 404
                if resp.status >= 400:
                    raise PlatformError(f"X API 请求失败（HTTP {resp.status}）")
                return await resp.json(), resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PlatformError(f"X 网络请求失败：{exc}") from exc

    async def resolve(self, target: str) -> TargetInfo:
        handle = target.strip().lstrip("@")
        if self.provider == "x_api":
            payload, _status = await self._get_json(
                f"{X_API_BASE}/users/by/username/{handle}"
            )
            user = (payload or {}).get("data") or {}
            if not user:
                raise PlatformError(f"X 用户 @{handle} 不存在或不可见")
            return TargetInfo(
                platform=self.name,
                target_id=handle,
                display_name=user.get("name") or handle,
                avatar_url=user.get("profile_image_url") or "",
            )

        payload, status = await self._get_json(f"{FX_BASE}/{handle}")
        if status == 404 or not payload:
            raise PlatformError(f"X 用户 @{handle} 不存在或不可见")
        user = payload.get("user") or {}
        return TargetInfo(
            platform=self.name,
            target_id=str(user.get("screen_name") or handle),
            display_name=user.get("name") or handle,
            avatar_url=user.get("avatar_url") or "",
        )

    async def fetch(self, target: Target, limit: int = 10) -> list[FeedItem]:
        if self.provider == "x_api":
            return await self._fetch_x_api(target, limit)
        return await self._fetch_fxtwitter(target, limit)

    async def _fetch_fxtwitter(self, target: Target, limit: int) -> list[FeedItem]:
        handle = target.target_id
        payload, status = await self._get_json(
            f"{FX_BASE}/2/profile/{handle}/statuses",
            {"count": max(1, min(limit, 20))},
        )
        if status == 204:
            return []
        if status == 404 or payload is None:
            raise PlatformError(
                f"X 时间线暂时不可用（@{handle}）：请稍后重试，或把 twitter_provider 换成 x_api"
            )

        items: list[FeedItem] = []
        for tweet in payload.get("results") or []:
            if tweet.get("reposted_by") or tweet.get("replying_to"):
                continue
            photos = (tweet.get("media") or {}).get("photos") or []
            items.append(
                FeedItem(
                    id=str(tweet.get("id", "")),
                    title="",
                    text=tweet.get("text") or "",
                    url=tweet.get("url") or "",
                    images=[photo.get("url", "") for photo in photos if photo.get("url")],
                    published_at=_timestamp_to_iso(tweet.get("created_timestamp")),
                    author=f"@{handle}",
                )
            )
        items.sort(key=lambda item: item.published_at or "")
        return items

    async def _fetch_x_api(self, target: Target, limit: int) -> list[FeedItem]:
        user_id = target.target_id
        if not user_id.isdigit():
            payload, _status = await self._get_json(
                f"{X_API_BASE}/users/by/username/{user_id}"
            )
            user_id = str(((payload or {}).get("data") or {}).get("id") or "")
            if not user_id:
                raise PlatformError(f"X 用户 @{target.target_id} 不存在或不可见")

        payload, _status = await self._get_json(
            f"{X_API_BASE}/users/{user_id}/tweets",
            {
                "max_results": max(5, min(limit, 100)),
                "exclude": "retweets,replies",
                "tweet.fields": "created_at,attachments",
                "expansions": "attachments.media_keys",
                "media.fields": "url,preview_image_url",
            },
        )
        if payload is None:
            return []
        media_map = {
            item.get("media_key"): item.get("url") or item.get("preview_image_url")
            for item in (payload.get("includes") or {}).get("media") or []
        }
        items: list[FeedItem] = []
        for tweet in payload.get("data") or []:
            keys = (tweet.get("attachments") or {}).get("media_keys") or []
            items.append(
                FeedItem(
                    id=str(tweet.get("id", "")),
                    title="",
                    text=tweet.get("text") or "",
                    url=f"https://x.com/i/status/{tweet.get('id')}",
                    images=[media_map[key] for key in keys if media_map.get(key)],
                    published_at=tweet.get("created_at"),
                    author=f"@{target.target_id}",
                )
            )
        items.sort(key=lambda item: item.published_at or "")
        return items
