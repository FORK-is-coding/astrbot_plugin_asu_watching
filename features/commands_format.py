from __future__ import annotations

from ..core.models import FeedItem


def format_item(item: FeedItem, text_limit: int = 300) -> str:
    text = (item.text or "").strip().replace("\n", " ")
    if len(text) > text_limit:
        text = text[:text_limit] + "…"
    lines = [item.title or text[:60] or item.id]
    if text and item.title:
        lines.append(text)
    lines.append(item.url)
    if item.published_at:
        lines.append(item.published_at)
    return "\n".join(line for line in lines if line)


def format_items(items: list[FeedItem], limit: int = 5) -> str:
    if not items:
        return "暂时没有新内容。"
    blocks = [format_item(item) for item in items[:limit]]
    if len(items) > limit:
        blocks.append(f"……还有 {len(items) - limit} 条")
    return "\n\n".join(blocks)
