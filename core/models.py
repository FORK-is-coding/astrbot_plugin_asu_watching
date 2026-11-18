from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def target_key(platform: str, target_id: str) -> str:
    return f"{platform}:{target_id}"


@dataclass
class FeedItem:
    id: str
    title: str
    text: str
    url: str
    images: list[str] = field(default_factory=list)
    published_at: str | None = None
    author: str = ""
    event_type: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Target:
    platform: str
    target_id: str
    display_name: str
    avatar_url: str = ""
    last_check_at: str = ""
    failures: int = 0
    paused_reason: str | None = None

    @property
    def key(self) -> str:
        return target_key(self.platform, self.target_id)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["key"] = self.key
        return data


@dataclass
class Subscription:
    id: str
    umo: str
    target_key: str
    enabled: bool = True
    seen_ids: list[str] = field(default_factory=list)
    last_item_at: str | None = None
    baseline_done: bool = False
    last_error: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    source: str = "command"

    def to_dict(self) -> dict:
        return asdict(self)
