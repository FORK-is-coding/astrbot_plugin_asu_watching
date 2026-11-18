from __future__ import annotations

import asyncio
import logging

import aiohttp

from ..core.models import FeedItem

logger = logging.getLogger("astrbot")

SYSTEM_PROMPT = "你是资讯摘要助手，用中文输出，不编造，不逐条复述。"
SUMMARY_PROMPT = """请根据以下创作者动态写 2-3 句中文摘要，突出主题、关键信息和变化：
{content}
只输出摘要正文，不要列表、不要链接列表。"""


class SummaryService:
    def __init__(self, context, config):
        self.context = context
        self.config = config

    async def summarize(
        self, items: list[FeedItem], umo: str | None = None, labels=None
    ) -> str:
        if not items or not self.config.get("summary_enabled", True):
            return ""
        content = self._build_content(items, labels)
        api_key = str(self.config.get("summary_api_key", "")).strip()
        if api_key:
            return await self._openai_summary(content)
        return await self._astrbot_summary(content, umo)

    def _build_content(self, items: list[FeedItem], labels=None) -> str:
        limit = int(self.config.get("summary_max_chars", 300))
        lines = []
        for index, item in enumerate(items[:5], start=1):
            text = (item.title or item.text or "").strip().replace("\n", " ")
            if len(text) > 200:
                text = text[:200] + "…"
            label = labels[index - 1] if labels and index - 1 < len(labels) else ""
            prefix = f"[{label}] " if label else ""
            lines.append(f"{index}. {prefix}{text}")
        content = "\n".join(lines)
        return content[: max(200, limit * 3)]

    async def _astrbot_summary(self, content: str, umo: str | None) -> str:
        try:
            provider = await self.context.get_using_provider_async(umo=umo)
        except Exception as exc:
            logger.warning("摘要模型获取失败：%s", exc)
            return ""
        if provider is None:
            return ""
        try:
            response = await provider.text_chat(
                prompt=SUMMARY_PROMPT.format(content=content),
                system_prompt=SYSTEM_PROMPT,
                persist=False,
            )
        except Exception as exc:
            logger.warning("AI 摘要生成失败：%s", exc)
            return ""
        return self._trim((response.completion_text or "").strip())

    async def _openai_summary(self, content: str) -> str:
        base = str(self.config.get("summary_api_base", "")).strip()
        if not base:
            base = "https://api.openai.com/v1"
        model = str(self.config.get("summary_model", "")).strip() or "gpt-4o-mini"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": SUMMARY_PROMPT.format(content=content)},
            ],
            "temperature": 0.3,
        }
        try:
            async with aiohttp.ClientSession(
                trust_env=True, timeout=aiohttp.ClientTimeout(total=40)
            ) as session:
                async with session.post(
                    base.rstrip("/") + "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config['summary_api_key']}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as resp:
                    if resp.status >= 400:
                        logger.warning("AI 摘要接口失败：HTTP %s", resp.status)
                        return ""
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("AI 摘要网络失败：%s", exc)
            return ""
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        return self._trim(str(text).strip())

    def _trim(self, text: str) -> str:
        limit = int(self.config.get("summary_max_chars", 300))
        return text[:limit].strip()
