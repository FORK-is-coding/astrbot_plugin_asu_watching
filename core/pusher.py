from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain

from .card import CardRenderer
from .models import FeedItem, Subscription, Target
from .store import JsonStore

logger = logging.getLogger("astrbot")


class Pusher:
    def __init__(self, context, store: JsonStore, config, card: CardRenderer, summary=None):
        self.context = context
        self.store = store
        self.config = config
        self.card = card
        self.summary = summary
        self._hour_counts: dict[tuple[str, str, str], int] = {}
        self._day_counts: dict[tuple[str, str], int] = {}

    async def push(
        self,
        subscription: Subscription,
        target: Target,
        items: list[FeedItem],
    ) -> bool:
        if not items:
            return True
        if not self._within_limits(target, subscription.umo):
            subscription.last_error = "达到每小时/每日推送上限"
            await self.store.update_subscription(subscription)
            return False

        display_items = self._display_items(target, items)
        header = (
            f"{target.display_name}（{target.target_id}）更新了 {len(items)} 条："
        )
        summary_text = ""
        if self.summary is not None:
            summary_text = await self.summary.summarize(
                display_items, subscription.umo
            )
        text = header
        if summary_text:
            text += "\n\nAI 摘要：" + summary_text
            await self.store.set_summary(target.key, summary_text)
        text += "\n\n" + self._format_items(display_items)
        try:
            card_path = self.card.render(target, display_items, summary_text)
        except Exception as exc:
            logger.warning("卡片渲染失败，降级纯文本：%s", exc)
            card_path = None

        chain = MessageChain()
        if card_path is not None:
            chain.chain.append(Comp.Image.fromFileSystem(str(card_path)))
        chain.chain.append(Comp.Plain(text))

        try:
            sent = await self.context.send_message(subscription.umo, chain)
        except Exception as exc:
            logger.warning("发送动态失败：%s", exc)
            sent = False

        if not sent:
            sent = await self._send_text_only(subscription.umo, text)

        if card_path is not None:
            Path(card_path).unlink(missing_ok=True)

        if sent:
            fields = {
                "seen_ids": (
                    subscription.seen_ids + [item.id for item in items]
                )[-200:],
                "last_error": None,
            }
            if items[-1].published_at:
                fields["last_item_at"] = items[-1].published_at
            await self.store.update_subscription_fields(subscription.id, **fields)
            self._record_push(target, subscription.umo)
            return True

        await self.store.update_subscription_fields(
            subscription.id, last_error="发送失败"
        )
        return False

    async def push_batch(self, umo: str, entries) -> bool:
        """Send one combined message for all targets updated in this tick."""
        if not entries:
            return True
        display_entries = []
        for subscription, target, items in entries:
            if not self._within_limits(target, umo):
                await self.store.update_subscription_fields(
                    subscription.id, last_error="达到每小时/每日推送上限"
                )
                continue
            display_items = self._display_items(target, items)
            if display_items:
                display_entries.append((subscription, target, display_items))
        if not display_entries:
            return True

        flat_items = []
        labels = []
        for _subscription, target, items in display_entries:
            for item in items:
                flat_items.append(item)
                labels.append(target.display_name)

        summary_text = ""
        if self.summary is not None:
            summary_text = await self.summary.summarize(
                flat_items, umo, labels=labels
            )

        total = sum(len(items) for _subscription, _target, items in display_entries)
        text = f"阿酥盯盘器：{len(display_entries)} 个账号更新了 {total} 条"
        if summary_text:
            text += "\n\nAI 摘要：" + summary_text
        text += "\n\n" + self._format_batch(display_entries)

        try:
            card_path = self.card.render_digest(
                summary_text,
                [(target, items) for _subscription, target, items in display_entries],
            )
        except Exception as exc:
            logger.warning("聚合卡片渲染失败，降级纯文本：%s", exc)
            card_path = None

        chain = MessageChain()
        if card_path is not None:
            chain.chain.append(Comp.Image.fromFileSystem(str(card_path)))
        chain.chain.append(Comp.Plain(text))

        try:
            sent = await self.context.send_message(umo, chain)
        except Exception as exc:
            logger.warning("发送聚合动态失败：%s", exc)
            sent = False
        if not sent:
            sent = await self._send_text_only(umo, text)
        if card_path is not None:
            Path(card_path).unlink(missing_ok=True)

        for subscription, target, items in display_entries:
            if sent:
                fields = {
                    "seen_ids": (
                        subscription.seen_ids + [item.id for item in items]
                    )[-200:],
                    "last_error": None,
                }
                if items[-1].published_at:
                    fields["last_item_at"] = items[-1].published_at
                await self.store.update_subscription_fields(subscription.id, **fields)
                self._record_push(target, umo)
                if summary_text:
                    await self.store.set_summary(target.key, summary_text)
            else:
                await self.store.update_subscription_fields(
                    subscription.id, last_error="发送失败"
                )
        return sent

    def _format_batch(self, display_entries) -> str:
        max_items = int(self.config.get("card_max_items", 5))
        sections = []
        for _subscription, target, items in display_entries:
            lines = [f"【{target.display_name}】"]
            for item in items[:max_items]:
                text = (item.title or item.text or "").strip().replace("\n", " ")
                if len(text) > 120:
                    text = text[:120] + "…"
                lines.append(f"- {text}\n  {item.url}")
            if len(items) > max_items:
                lines.append(f"……还有 {len(items) - max_items} 条")
            sections.append("\n".join(lines))
        return "\n\n".join(sections)

    def _display_items(self, target: Target, items: list[FeedItem]) -> list[FeedItem]:
        if target.platform != "github":
            return items
        pushes = [item for item in items if item.event_type == "PushEvent"]
        if len(pushes) <= 1:
            return items
        others = [item for item in items if item.event_type != "PushEvent"]
        groups: dict[str, list[FeedItem]] = {}
        for item in pushes:
            groups.setdefault(item.author, []).append(item)
        merged = [
            FeedItem(
                id=group[-1].id,
                title=f"{repo} 有 {len(group)} 次推送",
                text=group[-1].text,
                url=group[-1].url,
                published_at=group[-1].published_at,
                author=repo,
                event_type="PushEvent",
            )
            for repo, group in groups.items()
        ]
        result = others + merged
        result.sort(key=lambda item: item.published_at or "")
        return result

    def _within_limits(self, target: Target, umo: str) -> bool:
        hour = datetime.now().strftime("%Y%m%d%H")
        day = datetime.now().strftime("%Y%m%d")
        hourly_cap = int(self.config.get("max_pushes_per_hour", 6))
        daily_cap = int(self.config.get("daily_push_limit", 30))
        hour_key = (target.key, umo, hour)
        day_key = (umo, day)
        return (
            self._hour_counts.get(hour_key, 0) < hourly_cap
            and self._day_counts.get(day_key, 0) < daily_cap
        )

    def _record_push(self, target: Target, umo: str) -> None:
        hour = datetime.now().strftime("%Y%m%d%H")
        day = datetime.now().strftime("%Y%m%d")
        hour_key = (target.key, umo, hour)
        day_key = (umo, day)
        self._hour_counts[hour_key] = self._hour_counts.get(hour_key, 0) + 1
        self._day_counts[day_key] = self._day_counts.get(day_key, 0) + 1
        if len(self._hour_counts) > 1000:
            self._hour_counts.clear()
        if len(self._day_counts) > 1000:
            self._day_counts.clear()

    async def _send_text_only(self, umo: str, text: str) -> bool:
        try:
            return bool(
                await self.context.send_message(umo, MessageChain().message(text))
            )
        except Exception as exc:
            logger.warning("发送纯文本失败：%s", exc)
            return False

    def _format_items(self, items: list[FeedItem]) -> str:
        max_items = int(self.config.get("card_max_items", 5))
        lines = []
        for item in items[:max_items]:
            text = (item.title or item.text or "").strip().replace("\n", " ")
            if len(text) > 120:
                text = text[:120] + "…"
            lines.append(f"- {text}\n  {item.url}")
        if len(items) > max_items:
            lines.append(f"……还有 {len(items) - max_items} 条")
        return "\n".join(lines)
