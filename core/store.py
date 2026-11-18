from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .models import FeedItem, Subscription, Target


class JsonStore:
    """Single-writer JSON state with atomic replace."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self._state = {
            "version": 1,
            "targets": {},
            "subscriptions": [],
            "items": {},
            "suppressed": [],
            "summaries": {},
        }

    async def load(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            self._state.update(data)

    async def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    async def upsert_target(self, target: Target) -> None:
        async with self._lock:
            self._state["targets"][target.key] = target.to_dict()
            await self._save()

    async def get_target(self, key: str) -> Target | None:
        raw = self._state["targets"].get(key)
        if not raw:
            return None
        return Target(
            platform=raw["platform"],
            target_id=raw["target_id"],
            display_name=raw.get("display_name", ""),
            avatar_url=raw.get("avatar_url", ""),
            last_check_at=raw.get("last_check_at", ""),
            failures=raw.get("failures", 0),
            paused_reason=raw.get("paused_reason"),
        )

    async def list_targets(self) -> list[Target]:
        result = []
        for raw in self._state["targets"].values():
            result.append(
                Target(
                    platform=raw["platform"],
                    target_id=raw["target_id"],
                    display_name=raw.get("display_name", ""),
                    avatar_url=raw.get("avatar_url", ""),
                    last_check_at=raw.get("last_check_at", ""),
                    failures=raw.get("failures", 0),
                    paused_reason=raw.get("paused_reason"),
                )
            )
        return result

    async def add_subscription(self, sub: Subscription) -> None:
        async with self._lock:
            self._state["subscriptions"].append(sub.to_dict())
            await self._save()

    async def add_subscription_unique(self, sub: Subscription) -> bool:
        async with self._lock:
            for raw in self._state["subscriptions"]:
                if raw["umo"] == sub.umo and raw["target_key"] == sub.target_key:
                    return False
            self._state["subscriptions"].append(sub.to_dict())
            await self._save()
            return True

    async def update_subscription_fields(self, sub_id: str, **fields) -> bool:
        async with self._lock:
            for raw in self._state["subscriptions"]:
                if raw["id"] != sub_id:
                    continue
                raw.update(fields)
                await self._save()
                return True
            return False

    async def remove_subscriptions_for_target(self, target_key: str) -> int:
        async with self._lock:
            before = len(self._state["subscriptions"])
            self._state["subscriptions"] = [
                raw
                for raw in self._state["subscriptions"]
                if raw["target_key"] != target_key
            ]
            removed = before - len(self._state["subscriptions"])
            if removed:
                await self._save()
            return removed

    async def set_summary(self, target_key: str, summary: str) -> None:
        async with self._lock:
            self._state.setdefault("summaries", {})[target_key] = summary
            await self._save()

    async def get_summary(self, target_key: str) -> str:
        return self._state.get("summaries", {}).get(target_key, "")

    async def remove_target_data(self, target_key: str) -> None:
        async with self._lock:
            self._state["targets"].pop(target_key, None)
            self._state["items"].pop(target_key, None)
            self._state.get("summaries", {}).pop(target_key, None)
            self._state["subscriptions"] = [
                raw
                for raw in self._state["subscriptions"]
                if raw["target_key"] != target_key
            ]
            await self._save()

    async def suppress(self, umo: str, target_key: str) -> None:
        async with self._lock:
            key = f"{umo}|{target_key}"
            if key not in self._state["suppressed"]:
                self._state["suppressed"].append(key)
                await self._save()

    async def unsuppress(self, umo: str, target_key: str) -> None:
        async with self._lock:
            key = f"{umo}|{target_key}"
            if key in self._state["suppressed"]:
                self._state["suppressed"].remove(key)
                await self._save()

    def is_suppressed(self, umo: str, target_key: str) -> bool:
        return f"{umo}|{target_key}" in self._state["suppressed"]

    async def list_subscriptions(self, umo: str | None = None) -> list[Subscription]:
        result = []
        for raw in self._state["subscriptions"]:
            if umo is not None and raw["umo"] != umo:
                continue
            result.append(
                Subscription(
                    id=raw["id"],
                    umo=raw["umo"],
                    target_key=raw["target_key"],
                    enabled=raw.get("enabled", True),
                    seen_ids=list(raw.get("seen_ids", [])),
                    last_item_at=raw.get("last_item_at"),
                    baseline_done=raw.get("baseline_done", False),
                    last_error=raw.get("last_error"),
                    created_at=raw.get("created_at", ""),
                    source=raw.get("source", "command"),
                )
            )
        return result

    async def find_subscription(self, umo: str, target_key: str) -> Subscription | None:
        for sub in await self.list_subscriptions(umo):
            if sub.target_key == target_key:
                return sub
        return None

    async def update_subscription(self, sub: Subscription) -> None:
        async with self._lock:
            for index, raw in enumerate(self._state["subscriptions"]):
                if raw["id"] == sub.id:
                    self._state["subscriptions"][index] = sub.to_dict()
                    await self._save()
                    return
            raise KeyError(sub.id)

    async def remove_subscription(self, sub_id: str) -> bool:
        async with self._lock:
            before = len(self._state["subscriptions"])
            self._state["subscriptions"] = [
                raw for raw in self._state["subscriptions"] if raw["id"] != sub_id
            ]
            if len(self._state["subscriptions"]) == before:
                return False
            await self._save()
            return True

    async def cache_items(self, key: str, items: list[FeedItem], limit: int = 50) -> None:
        async with self._lock:
            self._state["items"][key] = [item.to_dict() for item in items[:limit]]
            await self._save()

    async def get_cached_items(self, key: str, limit: int = 50) -> list[FeedItem]:
        raw_items = self._state["items"].get(key, [])[:limit]
        return [
            FeedItem(
                id=raw["id"],
                title=raw.get("title", ""),
                text=raw.get("text", ""),
                url=raw.get("url", ""),
                images=list(raw.get("images", [])),
                published_at=raw.get("published_at"),
                author=raw.get("author", ""),
                event_type=raw.get("event_type", ""),
            )
            for raw in raw_items
        ]

    async def snapshot(self) -> dict:
        return json.loads(json.dumps(self._state, ensure_ascii=False))
