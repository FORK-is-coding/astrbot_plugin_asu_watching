from __future__ import annotations

import pytest

from astrbot_plugin_creator_feed.platforms.base import PlatformError
from astrbot_plugin_creator_feed.platforms.github import GithubAdapter
from astrbot_plugin_creator_feed.platforms.xiaohongshu import (
    XiaohongshuAdapter,
    parse_user_id,
)


def test_parse_xhs_user_id_from_profile_url():
    url = "https://www.xiaohongshu.com/user/profile/620103f00000000021029b87?xsec_token=x"
    assert parse_user_id(url) == "620103f00000000021029b87"
    assert parse_user_id("620103f00000000021029b87") == "620103f00000000021029b87"


def test_parse_xhs_user_id_rejects_short_links():
    with pytest.raises(PlatformError):
        parse_user_id("https://xhslink.com/a/abc123")


@pytest.mark.asyncio
async def test_xhs_client_can_be_created_without_target(tmp_path):
    adapter = XiaohongshuAdapter(tmp_path / "xhs_auth.json")

    client = adapter._client()

    assert client is not None
    await adapter.close()


def test_xhs_find_notes_extracts_note_cards(tmp_path):
    adapter = XiaohongshuAdapter(tmp_path / "xhs_auth.json")
    state = {
        "user": {
            "notes": [
                [
                    {
                        "id": "note-1",
                        "xsecToken": "token-1",
                        "noteCard": {
                            "displayTitle": "标题",
                            "desc": "正文",
                            "cover": {"urlDefault": "https://img.example/cover.jpg"},
                        },
                    }
                ]
            ]
        }
    }

    notes = adapter._find_notes(state)

    assert len(notes) == 1
    assert notes[0]["id"] == "note-1"


def test_github_push_event_builds_compare_url():
    adapter = GithubAdapter()
    item = adapter._map_event(
        {
            "id": "evt-1",
            "type": "PushEvent",
            "repo": {"name": "LofiSu/repo"},
            "created_at": "2026-11-17T00:00:00Z",
            "payload": {
                "before": "a" * 40,
                "head": "b" * 40,
                "size": 2,
                "commits": [{"message": "first line\nbody"}],
            },
        }
    )

    assert item is not None
    assert item.url == f"https://github.com/LofiSu/repo/compare/{'a' * 40}...{'b' * 40}"
    assert item.title == "LofiSu/repo 有 2 个新提交"
    assert item.text == "first line"


def test_github_release_event_uses_payload_url():
    adapter = GithubAdapter()
    item = adapter._map_event(
        {
            "id": "evt-2",
            "type": "ReleaseEvent",
            "repo": {"name": "LofiSu/repo"},
            "created_at": "2026-11-17T00:00:00Z",
            "payload": {
                "release": {
                    "html_url": "https://github.com/LofiSu/repo/releases/tag/v1",
                    "name": "v1",
                }
            },
        }
    )

    assert item is not None
    assert item.url == "https://github.com/LofiSu/repo/releases/tag/v1"
