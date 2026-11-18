from __future__ import annotations

from pathlib import Path

from ..core.store import JsonStore
from .base import PlatformAdapter
from .github import GithubAdapter
from .twitter import TwitterAdapter
from .xiaohongshu import XiaohongshuAdapter


def build_adapters(config, data_dir: Path) -> dict[str, PlatformAdapter]:
    return {
        "github": GithubAdapter(config.get("github_token", "")),
        "x": TwitterAdapter(
            provider=config.get("twitter_provider", "fxtwitter"),
            bearer_token=config.get("twitter_bearer_token", ""),
        ),
        "xhs": XiaohongshuAdapter(
            data_dir / "xhs_auth.json",
            provider=config.get("xhs_provider", "cookie"),
            rsshub_base=config.get("rsshub_base", ""),
            rsshub_access_key=config.get("rsshub_access_key", ""),
        ),
    }
