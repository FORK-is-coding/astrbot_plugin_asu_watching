from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import asyncio

import aiohttp

from ..core.models import FeedItem, Target
from .base import PlatformAdapter, PlatformError, TargetInfo

USER_ID_RE = re.compile(r"([0-9a-f]{24})")
INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def parse_user_id(raw: str) -> str:
    match = USER_ID_RE.search(raw.strip())
    if not match:
        raise PlatformError("小红书目标需要 24 位 user_id 或主页链接")
    return match.group(1)


class XiaohongshuAdapter(PlatformAdapter):
    name = "xhs"

    def __init__(
        self,
        auth_path: Path,
        provider: str = "cookie",
        rsshub_base: str = "",
        rsshub_access_key: str = "",
    ):
        self.auth_path = auth_path
        self.provider = provider
        self.rsshub_base = rsshub_base.rstrip("/")
        self.rsshub_access_key = rsshub_access_key
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": UA},
                trust_env=True,
                timeout=aiohttp.ClientTimeout(total=25),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _load_cookies(self) -> dict[str, str]:
        if not self.auth_path.exists():
            raise PlatformError("小红书未登录：请在插件面板粘贴 Cookie（扫码接口不可用）")
        try:
            payload = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PlatformError("小红书登录态文件损坏：请重新粘贴 Cookie") from exc
        cookies = payload.get("cookies") or {}
        if not cookies.get("web_session"):
            raise PlatformError("小红书登录态不完整：请在插件面板重新粘贴 Cookie")
        return {key: str(value) for key, value in cookies.items()}

    async def resolve(self, target: str) -> TargetInfo:
        return TargetInfo(
            platform=self.name,
            target_id=parse_user_id(target),
            display_name="",
        )

    async def fetch(self, target: Target, limit: int = 20) -> list[FeedItem]:
        if self.provider == "rsshub":
            return await self._fetch_rsshub(target, limit)
        cookies = self._load_cookies()
        url = f"https://www.xiaohongshu.com/user/profile/{target.target_id}"
        try:
            async with self._client().get(
                url,
                cookies=cookies,
                headers={
                    "Referer": f"https://www.xiaohongshu.com/user/profile/{target.target_id}",
                    "Origin": "https://www.xiaohongshu.com",
                },
            ) as resp:
                if str(resp.url).find("/login") >= 0:
                    self.auth_path.unlink(missing_ok=True)
                    raise PlatformError(
                        "小红书登录态已过期：请在插件面板重新粘贴 Cookie"
                    )
                if resp.status != 200:
                    raise PlatformError(f"小红书主页请求失败（HTTP {resp.status}）")
                html = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PlatformError(f"小红书网络请求失败：{exc}") from exc

        match = INITIAL_STATE_RE.search(html)
        if not match:
            raise PlatformError("小红书页面结构变化或需要验证：请重新粘贴 Cookie 或改用 rsshub")

        raw = match.group(1).replace("undefined", "null")
        try:
            state = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlatformError("小红书页面数据解析失败：页面可能已改版") from exc
        notes = self._find_notes(state)
        if not notes:
            raise PlatformError("小红书主页没有解析到笔记：请确认账号或重新登录")

        items: list[FeedItem] = []
        for note in notes[:limit]:
            card = note.get("noteCard") or note.get("note_card") or {}
            note_id = str(note.get("id") or note.get("noteId") or card.get("noteId") or "")
            if not note_id:
                continue
            token = note.get("xsecToken") or note.get("xsec_token") or ""
            link = f"https://www.xiaohongshu.com/explore/{note_id}"
            if token:
                link += f"?xsec_token={token}"
            cover = card.get("cover") or {}
            cover_url = (
                cover.get("urlDefault")
                or cover.get("url_default")
                or cover.get("url")
                or ""
            )
            published_at = None
            raw_time = card.get("time") or note.get("time")
            if raw_time:
                try:
                    published_at = datetime.fromtimestamp(
                        int(raw_time) / 1000, tz=timezone.utc
                    ).isoformat(timespec="seconds")
                except (TypeError, ValueError, OSError):
                    published_at = None
            items.append(
                FeedItem(
                    id=note_id,
                    title=card.get("displayTitle") or card.get("display_title") or "",
                    text=card.get("desc") or "",
                    url=link,
                    images=[cover_url] if cover_url else [],
                    published_at=published_at,
                    author=target.display_name,
                )
            )
        if all(item.published_at for item in items):
            items.sort(key=lambda item: item.published_at or "")
        else:
            items.reverse()
        return items

    async def _fetch_rsshub(self, target: Target, limit: int) -> list[FeedItem]:
        if not self.rsshub_base:
            raise PlatformError("xhs_provider=rsshub 需要先填写 rsshub_base")
        url = f"{self.rsshub_base}/xiaohongshu/user/{target.target_id}/notes"
        params = {}
        if self.rsshub_access_key:
            params["key"] = self.rsshub_access_key
        try:
            async with self._client().get(url, params=params) as resp:
                if resp.status != 200:
                    raise PlatformError(f"RSSHub 小红书请求失败（HTTP {resp.status}）")
                xml_text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PlatformError(f"RSSHub 网络请求失败：{exc}") from exc

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise PlatformError("RSSHub 返回内容无法解析") from exc
        items: list[FeedItem] = []
        for node in root.iter("item"):
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            description = node.findtext("description") or ""
            pub_date = (node.findtext("pubDate") or "").strip()
            if pub_date:
                try:
                    pub_date = parsedate_to_datetime(pub_date).isoformat(
                        timespec="seconds"
                    )
                except (TypeError, ValueError, OverflowError):
                    pub_date = ""
            guid = (node.findtext("guid") or link).strip()
            match = USER_ID_RE.search(link.replace("/explore/", " "))
            note_id = match.group(1) if match else guid
            items.append(
                FeedItem(
                    id=note_id,
                    title=title,
                    text=re.sub(r"<[^>]+>", " ", description).strip(),
                    url=link,
                    images=[],
                    published_at=pub_date or None,
                    author=target.display_name,
                )
            )
        if all(item.published_at for item in items):
            items.sort(key=lambda item: item.published_at or "")
            return items[-limit:]
        # RSSHub 小红书路由可能没有 pubDate；feed 顺序是新→旧。
        return list(reversed(items[:limit]))

    def _find_notes(self, state: dict) -> list[dict]:
        found: list[dict] = []
        seen: set[str] = set()

        def walk(value) -> None:
            if isinstance(value, dict):
                card = value.get("noteCard") or value.get("note_card")
                if isinstance(card, dict):
                    note_id = str(value.get("id") or card.get("noteId") or "")
                    if note_id and note_id not in seen:
                        seen.add(note_id)
                        found.append(value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(state)
        return found
