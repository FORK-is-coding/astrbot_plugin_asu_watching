from __future__ import annotations

import asyncio

import aiohttp

from ..core.models import FeedItem, Target
from .base import PlatformAdapter, PlatformError, TargetInfo

API_BASE = "https://api.github.com"


class GithubAdapter(PlatformAdapter):
    name = "github"

    def __init__(self, token: str = "", api_base: str = API_BASE):
        self.token = token.strip()
        self.api_base = api_base.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "astrbot-plugin-creator-feed",
            }
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._session = aiohttp.ClientSession(
                headers=headers,
                trust_env=True,
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict | None = None):
        try:
            async with self._client().get(
                self.api_base + path, params=params
            ) as resp:
                if resp.status == 401:
                    raise PlatformError("GitHub Token 无效，请更新 github_token")
                if resp.status == 403:
                    raise PlatformError("GitHub 触发限流：请配置 github_token 或稍后重试")
                if resp.status == 429:
                    raise PlatformError("GitHub 请求过于频繁，请稍后重试")
                if resp.status >= 400:
                    raise PlatformError(f"GitHub 请求失败（HTTP {resp.status}）")
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PlatformError(f"GitHub 网络请求失败：{exc}") from exc

    async def resolve(self, target: str) -> TargetInfo:
        login = target.strip().lstrip("@")
        data = await self._get(f"/users/{login}")
        return TargetInfo(
            platform=self.name,
            target_id=data.get("login") or login,
            display_name=data.get("name") or data.get("login") or login,
            avatar_url=data.get("avatar_url") or "",
        )

    async def fetch(self, target: Target, limit: int = 20) -> list[FeedItem]:
        events = await self._get(
            f"/users/{target.target_id}/events/public",
            {"per_page": max(1, min(limit, 100))},
        )
        items = [self._map_event(event) for event in events]
        items = [item for item in items if item is not None]
        items.sort(key=lambda item: item.published_at or "")
        return items

    def _map_event(self, event: dict) -> FeedItem | None:
        event_type = event.get("type", "")
        repo = (event.get("repo") or {}).get("name", "")
        if not repo:
            return None
        payload = event.get("payload") or {}
        repo_url = f"https://github.com/{repo}"
        url = repo_url
        title = f"{repo} 有新动态"
        text = ""

        if event_type == "PushEvent":
            commits = payload.get("commits") or []
            before = payload.get("before") or ""
            head = payload.get("head") or ""
            count = payload.get("size") or len(commits)
            branch = str(payload.get("ref") or "").removeprefix("refs/heads/")
            if count:
                title = f"{repo} 有 {count} 个新提交"
            elif branch:
                title = f"{repo} 更新了分支 {branch}"
            else:
                title = f"{repo} 有新的推送"
            if before and head and set(before) != {"0"}:
                url = f"https://github.com/{repo}/compare/{before}...{head}"
            if commits:
                text = str(commits[-1].get("message", "")).splitlines()[0]
        elif event_type == "ReleaseEvent":
            release = payload.get("release") or {}
            url = release.get("html_url") or repo_url
            name = release.get("name") or release.get("tag_name") or ""
            title = f"{repo} 发布了 {name}".strip()
        elif event_type == "IssuesEvent":
            issue = payload.get("issue") or {}
            url = issue.get("html_url") or repo_url
            action = payload.get("action") or "更新"
            title = f"{repo} {action} issue #{issue.get('number', '')}"
            text = issue.get("title") or ""
        elif event_type == "PullRequestEvent":
            pr = payload.get("pull_request") or {}
            url = pr.get("html_url") or repo_url
            action = payload.get("action") or "更新"
            title = f"{repo} {action} PR #{pr.get('number', '')}"
            text = pr.get("title") or ""
        elif event_type == "CreateEvent":
            ref_type = payload.get("ref_type") or ""
            ref = payload.get("ref") or ""
            title = f"{repo} 创建了 {ref_type} {ref}".strip()
        elif event_type in {"WatchEvent", "ForkEvent"}:
            title = f"{repo} {event_type}"
        else:
            return None

        return FeedItem(
            id=str(event.get("id", "")),
            title=title,
            text=text,
            url=url,
            images=[],
            published_at=event.get("created_at"),
            author=repo,
            event_type=event_type,
        )
