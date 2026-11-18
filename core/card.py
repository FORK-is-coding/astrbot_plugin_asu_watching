from __future__ import annotations

import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import FeedItem, Target

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

PLATFORM_COLORS = {
    "github": (36, 41, 47),
    "x": (15, 20, 25),
    "xhs": (255, 36, 66),
}

PLATFORM_NAMES = {"github": "GitHub", "x": "X", "xhs": "小红书"}

WIDTH = 900
PADDING = 40


def resolve_font_path(configured: str) -> str | None:
    if configured and Path(configured).exists():
        return configured
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


class CardRenderer:
    def __init__(self, config, data_dir: Path):
        self.font_path = resolve_font_path(str(config.get("card_font_path", "")))
        self.enabled = bool(config.get("card_enabled", True)) and self.font_path is not None
        if self.enabled:
            try:
                ImageFont.truetype(self.font_path, 16)
            except OSError:
                self.enabled = False
        self.max_items = int(config.get("card_max_items", 5))
        self.temp_dir = data_dir / "cards"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def render(self, target: Target, items: list[FeedItem], summary: str = "") -> Path | None:
        if not self.enabled or not items:
            return None
        font = ImageFont.truetype(self.font_path, 28)
        small = ImageFont.truetype(self.font_path, 22)
        title_font = ImageFont.truetype(self.font_path, 32)

        body_width = WIDTH - PADDING * 2
        summary_lines = (
            self._wrap(summary, font, body_width, max_lines=4) if summary else []
        )
        blocks: list[list[str]] = []
        for item in items[: self.max_items]:
            text = (item.title or item.text or "").strip().replace("\n", " ")
            lines = self._wrap(text or item.url, title_font, body_width, max_lines=3)
            link_lines = self._wrap(item.url, small, body_width, max_lines=2)
            blocks.append(lines + link_lines)

        height = PADDING + 70
        if summary_lines:
            height += len(summary_lines) * 34 + 44
        for block in blocks:
            height += len(block) * 34 + 24
        height += PADDING

        image = Image.new("RGB", (WIDTH, height), (250, 250, 251))
        draw = ImageDraw.Draw(image)
        color = PLATFORM_COLORS.get(target.platform, (60, 60, 60))

        draw.rectangle([0, 0, WIDTH, 12], fill=color)
        draw.rounded_rectangle(
            [PADDING, PADDING, PADDING + 120, PADDING + 44],
            radius=10,
            fill=color,
        )
        platform_name = PLATFORM_NAMES.get(target.platform, target.platform)
        draw.text((PADDING + 18, PADDING + 8), platform_name, font=small, fill="white")
        draw.text(
            (PADDING + 140, PADDING + 8),
            target.display_name or target.target_id,
            font=title_font,
            fill=(30, 30, 30),
        )

        y = PADDING + 76
        if summary_lines:
            draw.text((PADDING, y), "AI 摘要", font=small, fill=color)
            y += 34
            for line in summary_lines:
                draw.text((PADDING, y), line, font=font, fill=(40, 40, 40))
                y += 34
            y += 12
        for block in blocks:
            for line in block:
                draw.text((PADDING, y), line, font=font, fill=(40, 40, 40))
                y += 34
            y += 24

        output = self.temp_dir / f"{uuid.uuid4().hex}.png"
        image.save(output)
        return output

    def render_digest(self, summary: str, entries) -> Path | None:
        if not self.enabled or not entries:
            return None
        font = ImageFont.truetype(self.font_path, 26)
        small = ImageFont.truetype(self.font_path, 22)
        title_font = ImageFont.truetype(self.font_path, 34)
        body_width = WIDTH - PADDING * 2

        summary_lines = (
            self._wrap(summary, font, body_width, max_lines=4) if summary else []
        )
        sections = []
        total = 0
        for target, items in entries:
            total += len(items)
            lines = []
            for item in items[: self.max_items]:
                text = (item.title or item.text or "").strip().replace("\n", " ")
                lines.extend(
                    self._wrap(text or item.url, font, body_width, max_lines=2)
                )
                lines.extend(self._wrap(item.url, small, body_width, max_lines=1))
            if len(items) > self.max_items:
                lines.append(f"……还有 {len(items) - self.max_items} 条")
            sections.append((target, lines))

        height = PADDING + 100
        if summary_lines:
            height += len(summary_lines) * 34 + 44
        for _target, lines in sections:
            height += 48 + len(lines) * 30 + 16
        height += PADDING

        image = Image.new("RGB", (WIDTH, height), (250, 250, 251))
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, WIDTH, 12], fill=(60, 60, 60))
        draw.text((PADDING, PADDING), "阿酥盯盘器", font=title_font, fill=(30, 30, 30))
        draw.text(
            (PADDING, PADDING + 48),
            f"{len(entries)} 个账号 · {total} 条更新",
            font=small,
            fill=(90, 90, 90),
        )

        y = PADDING + 96
        if summary_lines:
            draw.text((PADDING, y), "AI 摘要", font=small, fill=(60, 60, 60))
            y += 32
            for line in summary_lines:
                draw.text((PADDING, y), line, font=font, fill=(40, 40, 40))
                y += 34
            y += 12

        for target, lines in sections:
            color = PLATFORM_COLORS.get(target.platform, (60, 60, 60))
            draw.text((PADDING, y), target.display_name, font=font, fill=color)
            y += 40
            for line in lines:
                draw.text((PADDING, y), line, font=font, fill=(40, 40, 40))
                y += 30
            y += 16

        output = self.temp_dir / f"{uuid.uuid4().hex}.png"
        image.save(output)
        return output

    @staticmethod
    def _wrap(
        text: str,
        font: ImageFont.FreeTypeFont,
        width: int,
        max_lines: int = 3,
    ) -> list[str]:
        lines: list[str] = []
        current = ""
        for char in text:
            candidate = current + char
            if font.getlength(candidate) <= width:
                current = candidate
                continue
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if len(lines) == max_lines and len("".join(lines)) < len(text):
            lines[-1] = lines[-1][:-1] + "…"
        return lines
