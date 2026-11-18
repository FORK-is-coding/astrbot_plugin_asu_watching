from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.core.card import CardRenderer
from astrbot_plugin_creator_feed.core.models import FeedItem, Target


class FakeConfig(dict):
    def save_config(self):
        pass


def test_card_renderer_writes_png(tmp_path):
    renderer = CardRenderer({"card_enabled": True, "card_font_path": ""}, tmp_path)
    if not renderer.enabled:
        pytest.skip("no Chinese font on this machine")

    target = Target(platform="github", target_id="LofiSu", display_name="LofiSu")
    items = [
        FeedItem(
            id="1",
            title="LofiSu/repo 有 2 个新提交",
            text="feat: add feed plugin",
            url="https://github.com/LofiSu/repo",
        )
    ]

    path = renderer.render(target, items)

    assert path is not None
    assert path.exists()
    assert path.stat().st_size > 1000
    path.unlink()


def test_card_renderer_disabled_without_font(tmp_path):
    renderer = CardRenderer(
        {"card_enabled": True, "card_font_path": str(tmp_path / "missing.ttf")},
        tmp_path,
    )
    # A machine with a fallback font still renders; otherwise it stays disabled.
    if renderer.enabled:
        pytest.skip("fallback font available on this machine")
    assert renderer.render(
        Target(platform="x", target_id="su_lofi", display_name="@su_lofi"),
        [FeedItem(id="1", title="t", text="", url="u")],
    ) is None
