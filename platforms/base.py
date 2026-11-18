from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.models import FeedItem, Target


@dataclass
class TargetInfo:
    platform: str
    target_id: str
    display_name: str
    avatar_url: str = ""


class PlatformError(RuntimeError):
    """A platform request failed in a way the user must act on."""


class PlatformAdapter(ABC):
    name: str

    @abstractmethod
    async def resolve(self, target: str) -> TargetInfo:
        """Resolve a user supplied account to a canonical target."""

    @abstractmethod
    async def fetch(self, target: Target, limit: int = 20) -> list[FeedItem]:
        """Fetch the latest items, oldest first."""

    async def close(self) -> None:
        return None
