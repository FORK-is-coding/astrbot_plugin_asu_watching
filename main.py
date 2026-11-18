from __future__ import annotations

import asyncio
import re
from pathlib import Path

import astrbot.api.message_components as Comp

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
try:
    from astrbot.api.util import get_astrbot_plugin_data_path
except ImportError:  # AstrBot 4.29
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    def get_astrbot_plugin_data_path() -> str:
        return str(Path(get_astrbot_data_path()) / "plugin_data")
from astrbot.api.web import error_response, json_response, request
from astrbot.core.star.filter.command import GreedyStr

from .core.card import CardRenderer
from .core.pusher import Pusher
from .core.scheduler import Scheduler
from .core.models import Target, utc_now_iso
from .platforms.base import PlatformError
from .core.store import JsonStore
from .features.commands_format import format_item, format_items
from .features.feed_service import FeedService, ServiceError
from .features.search_service import SearchService
from .features.summary_service import SummaryService
from .features.xhs_login import XhsLoginError, XhsLoginManager
from .platforms.registry import build_adapters

PLUGIN_NAME = "astrbot_plugin_creator_feed"

class CreatorFeedPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = JsonStore(self.data_dir / "state.json")
        self.adapters = build_adapters(config, self.data_dir)
        self.service = FeedService(self.store, self.adapters, config)
        self.search = SearchService(context, config)
        self.summary = SummaryService(context, config)
        self.xhs_login = XhsLoginManager(self.data_dir / "xhs_auth.json")
        self.card = CardRenderer(config, self.data_dir)
        self.pusher = Pusher(context, self.store, config, self.card, self.summary)
        self.scheduler = Scheduler(
            context, self.store, self.adapters, config, self.pusher
        )
        self._register_panel_api()

    async def initialize(self) -> None:
        await self.store.load()
        await self.scheduler.sync_subscriptions()
        await self._remember_qq_sessions()
        self.scheduler.start()

    async def _remember_qq_sessions(self) -> None:
        from astrbot.core.platform.message_session import MessageSession

        umos = {str(umo) for umo in self.config.get("push_sessions") or []}
        for subscription in await self.store.list_subscriptions():
            umos.add(subscription.umo)

        for umo in umos:
            try:
                session = MessageSession.from_str(umo)
            except ValueError:
                continue
            if session.message_type.value != "GroupMessage":
                continue
            platform = self.context.get_platform_inst(session.platform_id)
            remember = getattr(platform, "remember_session_scene", None)
            if callable(remember):
                remember(session.session_id, "group")

    async def terminate(self) -> None:
        await self.scheduler.stop()
        for adapter in self.adapters.values():
            await adapter.close()

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        await self._remember_qq_sessions()

    # ---------- /feed 指令组 ----------

    @filter.command_group("feed", alias={"订阅"})
    def feed(self):
        pass

    @staticmethod
    def _del_identifier(message: str, identifier: str) -> str:
        tokens = str(message or "").split()
        for index, token in enumerate(tokens):
            if token.lower() in {"github", "x", "xhs"} and index + 1 < len(tokens):
                return f"{token.lower()}:{tokens[index + 1]}"
        return identifier

    @staticmethod
    def _alias_from_message(message: str) -> str:
        match = re.search(r"--name\s+(.+)$", str(message or "").strip())
        return match.group(1).strip().strip('"').strip("'") if match else ""

    def _user_allowed(self, event: AstrMessageEvent) -> bool:
        if event.is_admin():
            return True
        allowed = self.config.get("allow_users") or []
        if not allowed:
            return True
        return str(event.get_sender_id()) in {str(item) for item in allowed}

    @feed.command("add")
    async def feed_add(
        self,
        event: AstrMessageEvent,
        platform: str,
        target: str,
    ):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        alias = self._alias_from_message(event.message_str)
        try:
            resolved, _subscription, sample = await self.service.add(
                event.unified_msg_origin,
                platform.lower(),
                target,
                alias,
            )
        except ServiceError as exc:
            yield event.plain_result(f"订阅失败：{exc}")
            return

        message = f"已订阅 {resolved.display_name}（{resolved.key}）"
        if sample:
            message += "\n\n最新一条：\n" + format_item(sample)
        else:
            message += "\n\n暂时没有可展示的内容。"
        yield event.plain_result(message)

    @feed.command("list")
    async def feed_list(self, event: AstrMessageEvent):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        rows = await self.service.list_subscriptions(event.unified_msg_origin)
        if not rows:
            yield event.plain_result("当前会话还没有订阅，用 /feed add <平台> <账号> 添加。")
            return
        lines = ["当前会话订阅："]
        for index, subscription, target in rows:
            name = target.display_name if target else subscription.target_key
            state = "开启" if subscription.enabled else "暂停"
            lines.append(f"{index}. [{state}] {name}（{subscription.target_key}）")
        yield event.plain_result("\n".join(lines))

    @feed.command("del")
    async def feed_del(self, event: AstrMessageEvent, identifier: str):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        identifier = self._del_identifier(event.message_str, identifier)
        try:
            subscription = await self.service.remove(
                event.unified_msg_origin, identifier
            )
        except ServiceError as exc:
            yield event.plain_result(f"删除失败：{exc}")
            return
        yield event.plain_result(f"已删除订阅 {subscription.target_key}")

    @feed.command("on")
    async def feed_on(self, event: AstrMessageEvent, identifier: str):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        try:
            subscription = await self.service.toggle(
                event.unified_msg_origin, identifier, True
            )
        except ServiceError as exc:
            yield event.plain_result(f"操作失败：{exc}")
            return
        yield event.plain_result(f"已开启 {subscription.target_key}")

    @feed.command("off")
    async def feed_off(self, event: AstrMessageEvent, identifier: str):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        try:
            subscription = await self.service.toggle(
                event.unified_msg_origin, identifier, False
            )
        except ServiceError as exc:
            yield event.plain_result(f"操作失败：{exc}")
            return
        yield event.plain_result(f"已暂停 {subscription.target_key}")

    @feed.command("check")
    async def feed_check(
        self,
        event: AstrMessageEvent,
        platform: str,
        target: str,
    ):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        yield event.plain_result(f"正在查询 {platform} / {target} …")
        try:
            resolved, items = await self.service.check(platform.lower(), target)
        except ServiceError as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        summary = await self.summary.summarize(items, event.unified_msg_origin)
        header = f"{resolved.display_name}（{resolved.key}）最近 {len(items)} 条："
        message = header
        if summary:
            message += "\n\nAI 摘要：" + summary
        message += "\n\n" + format_items(list(reversed(items)), limit=5)
        yield event.plain_result(message)

    @feed.command("now")
    async def feed_now(self, event: AstrMessageEvent):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        results, errors = await self.service.run_now(event.unified_msg_origin)
        if not results and not errors:
            yield event.plain_result("当前会话没有启用的订阅。")
            return
        for target_name, error in errors:
            yield event.plain_result(f"{target_name}：查询失败：{error}")
        for subscription, target, items in results:
            if items:
                summary = await self.summary.summarize(
                    items, event.unified_msg_origin
                )
                message = f"{target.display_name}："
                if summary:
                    message += "\n\nAI 摘要：" + summary
                message += "\n\n" + format_items(list(reversed(items)), limit=20)
                yield event.plain_result(message)
                await self.service.mark_pushed(subscription, items)
            else:
                yield event.plain_result(f"{target.display_name}：没有新内容。")

    @feed.command("resolve")
    async def feed_resolve(self, event: AstrMessageEvent, link: GreedyStr):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        try:
            resolved = await self.service.resolve_link(str(link))
        except (ServiceError, PlatformError) as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(f"可这样订阅：\n/feed add {resolved}")

    @feed.command("panel")
    async def feed_panel(self, event: AstrMessageEvent):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        yield event.plain_result(
            "打开 AstrBot WebUI → 插件 → 阿酥盯盘器 → 可视化视图，可在面板里粘贴小红书 Cookie、查看动态和配置推送。"
        )

    @feed.command("admin")
    async def feed_admin(
        self,
        event: AstrMessageEvent,
        action: str,
    ):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        if not event.is_admin():
            yield event.plain_result("只有 AstrBot 管理员可以使用该指令。")
            return
        if action == "targets":
            targets = await self.store.list_targets()
            if not targets:
                yield event.plain_result("还没有任何目标。")
                return
            lines = ["全部目标："]
            for target in targets:
                lines.append(f"- {target.key}（{target.display_name}）")
            yield event.plain_result("\n".join(lines))
            return
        if action == "del":
            tokens = str(event.message_str).split()
            umo = next((token for token in tokens if ":" in token), "")
            if not umo:
                yield event.plain_result("用法：/feed admin del <umo>")
                return
            subscriptions = await self.store.list_subscriptions(umo)
            for subscription in subscriptions:
                await self.store.suppress(umo, subscription.target_key)
                await self.store.remove_subscription(subscription.id)
            yield event.plain_result(f"已删除 {umo} 的 {len(subscriptions)} 条订阅")
            return
        yield event.plain_result("用法：/feed admin targets | /feed admin del <umo>")

    @feed.command("search")
    async def feed_search(self, event: AstrMessageEvent, keyword: GreedyStr):
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return
        if not self.config.get("search_enabled", True):
            yield event.plain_result("搜索功能未开启：请在插件配置里打开 search_enabled。")
            return
        yield event.plain_result(await self.search.search(str(keyword), event))

    @filter.llm_tool(name="feed_check")
    async def feed_check_tool(
        self,
        event: AstrMessageEvent,
        platform: str,
        target: str,
    ) -> str:
        """查询某个创作者的最新动态。

        Args:
            platform(string): 平台，可选 github / x / xhs
            target(string): 账号、user_id 或主页链接
        """
        if not self._user_allowed(event):
            return "你不在允许使用名单里。"
        try:
            resolved, items = await self.service.check(platform.lower(), target)
        except ServiceError as exc:
            return str(exc)
        summary = await self.summary.summarize(items, event.unified_msg_origin)
        prefix = f"{resolved.display_name}："
        if summary:
            prefix += "\nAI 摘要：" + summary
        return prefix + "\n" + format_items(list(reversed(items)), limit=3)

    @filter.llm_tool(name="feed_list")
    async def feed_list_tool(self, event: AstrMessageEvent) -> str:
        """列出当前会话订阅的创作者。"""
        if not self._user_allowed(event):
            return "你不在允许使用名单里。"
        rows = await self.service.list_subscriptions(event.unified_msg_origin)
        if not rows:
            return "当前会话还没有订阅。"
        return "\n".join(
            f"{index}. {target.display_name if target else subscription.target_key}"
            for index, subscription, target in rows
        )

    @filter.llm_tool(name="feed_search")
    async def feed_search_tool(self, event: AstrMessageEvent, keyword: str) -> str:
        """联网搜索与关键词相关的信息。

        Args:
            keyword(string): 搜索关键词
        """
        if not self._user_allowed(event):
            return "你不在允许使用名单里。"
        if not self.config.get("search_enabled", True):
            return "搜索功能未开启。"
        return await self.search.search(keyword, event)

    @filter.command("跟紧酥神", alias={"紧跟酥神", "酥神动向"})
    async def catch_up_all(self, event: AstrMessageEvent):
        """跟紧酥神：立刻抓取所有平台最新动态并生成整合 AI 摘要。"""
        if not self._user_allowed(event):
            yield event.plain_result("你不在允许使用名单里。")
            return

        entries = []
        flat_items = []
        labels = []
        errors = []
        for entry in self.config.get("watch_targets") or []:
            if not entry.get("enabled", True):
                continue
            platform = str(entry.get("platform", "")).strip().lower()
            target_id = str(entry.get("target", "")).strip()
            if not platform or not target_id:
                continue
            target = Target(
                platform=platform,
                target_id=target_id,
                display_name=str(entry.get("display_name") or target_id),
            )
            adapter = self.adapters.get(platform)
            if adapter is None:
                continue
            try:
                items = await adapter.fetch(target, limit=5)
            except Exception as exc:
                errors.append(f"{target.display_name}：{exc}")
                continue
            if not items:
                continue
            display_items = list(reversed(items))
            entries.append((target, display_items))
            for item in display_items:
                flat_items.append(item)
                labels.append(target.display_name)

        if not entries:
            suffix = ("；" + "；".join(errors)) if errors else ""
            yield event.plain_result("暂时没有拿到动态。" + suffix)
            return

        summary = await self.summary.summarize(
            flat_items, event.unified_msg_origin, labels=labels
        )
        lines = [
            f"阿酥盯盘器 · 跟紧酥神｜{len(entries)} 个账号 · {len(flat_items)} 条"
        ]
        if summary:
            lines.append("AI 摘要：" + summary)
        for target, items in entries:
            lines.append(f"\n【{target.display_name}】")
            for item in items[:5]:
                title = (item.title or item.text or "").strip().replace("\n", " ")
                if len(title) > 100:
                    title = title[:100] + "…"
                lines.append(f"- {title}\n  {item.url}")
        if errors:
            lines.append("\n未取到：" + "；".join(errors))
        text = "\n".join(lines)

        card_path = None
        try:
            card_path = self.card.render_digest(summary, entries)
        except Exception:
            card_path = None

        chain = []
        if card_path is not None:
            chain.append(Comp.Image.fromFileSystem(str(card_path)))
        chain.append(Comp.Plain(text))
        yield event.chain_result(chain)
        if card_path is not None:
            asyncio.create_task(self._cleanup_card(card_path))

    @staticmethod
    async def _cleanup_card(path) -> None:
        await asyncio.sleep(120)
        Path(path).unlink(missing_ok=True)

    # ---------- WebUI 面板 API ----------

    def _register_panel_api(self) -> None:
        routes = (
            ("panel/status", self.panel_status, ["GET"], "Creator feed status"),
            ("panel/items", self.panel_items, ["GET"], "Cached feed items"),
            ("panel/targets", self.panel_targets, ["POST"], "Add or remove a target"),
            ("panel/check", self.panel_check, ["POST"], "Fetch a target now"),
            (
                "panel/subscriptions",
                self.panel_subscriptions,
                ["POST"],
                "Toggle or delete a subscription",
            ),
            ("panel/config", self.panel_config, ["GET", "POST"], "Config readiness"),
            (
                "panel/xhs/login/start",
                self.panel_xhs_login_start,
                ["POST"],
                "Start Xiaohongshu QR login",
            ),
            (
                "panel/xhs/login/status",
                self.panel_xhs_login_status,
                ["GET"],
                "Poll Xiaohongshu QR login",
            ),
            ("panel/xhs/logout", self.panel_xhs_logout, ["POST"], "Xiaohongshu logout"),
            (
                "panel/xhs/cookie",
                self.panel_xhs_cookie,
                ["POST"],
                "Save Xiaohongshu cookie",
            ),
        )
        for route, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/{route}", handler, methods, desc
            )

    async def panel_status(self):
        targets = await self.store.list_targets()
        known = {target.key for target in targets}
        for entry in self.config.get("watch_targets") or []:
            if not entry.get("enabled", True):
                continue
            platform = str(entry.get("platform", "")).strip().lower()
            target_id = str(entry.get("target", "")).strip()
            if not platform or not target_id:
                continue
            key = f"{platform}:{target_id}"
            if key in known:
                continue
            targets.append(
                Target(
                    platform=platform,
                    target_id=target_id,
                    display_name=str(entry.get("display_name") or target_id),
                )
            )
        subscriptions = await self.store.list_subscriptions()
        latest = {}
        summaries = {}
        for target in targets:
            latest[target.key] = [
                item.to_dict() for item in await self.store.get_cached_items(target.key, 5)
            ]
            summaries[target.key] = await self.store.get_summary(target.key)
        return json_response(
            {
                "targets": [target.to_dict() for target in targets],
                "subscriptions": [
                    subscription.to_dict() for subscription in subscriptions
                ],
                "latest": latest,
                "summaries": summaries,
                "config": self._config_status(),
            }
        )

    async def panel_config(self):
        if request.method == "POST":
            payload = await request.json(default={})
            for key in (
                "github_token",
                "twitter_bearer_token",
                "rsshub_access_key",
                "summary_api_key",
            ):
                value = str(payload.get(key, "")).strip()
                if value:
                    self.config[key] = value
            if payload.get("twitter_provider") in {"fxtwitter", "x_api"}:
                self.config["twitter_provider"] = payload["twitter_provider"]
            if "rsshub_base" in payload:
                self.config["rsshub_base"] = str(payload["rsshub_base"]).strip()
            if "summary_api_base" in payload:
                self.config["summary_api_base"] = str(
                    payload["summary_api_base"]
                ).strip()
            if payload.get("summary_model"):
                self.config["summary_model"] = str(payload["summary_model"]).strip()
            if "summary_enabled" in payload:
                self.config["summary_enabled"] = bool(payload["summary_enabled"])
            if "summary_max_chars" in payload:
                self.config["summary_max_chars"] = int(payload["summary_max_chars"])
            self.config.save_config()
            await self._reload_adapters()
            return json_response(self._config_status())
        return json_response(self._config_status())

    async def _reload_adapters(self) -> None:
        old = self.adapters
        new = build_adapters(self.config, self.data_dir)
        self.adapters = new
        self.service.adapters = new
        self.scheduler.adapters = new
        for adapter in old.values():
            await adapter.close()

    async def panel_items(self):
        platform = request.query.get("platform", "", type=str)
        target = request.query.get("target", "", type=str)
        limit = request.query.get("limit", 20, type=int)
        key = f"{platform}:{target}"
        items = await self.store.get_cached_items(key, max(1, min(limit, 50)))
        return json_response({"items": [item.to_dict() for item in items]})

    async def panel_targets(self):
        payload = await request.json(default={})
        action = payload.get("action", "add")
        platform = str(payload.get("platform", "")).strip().lower()
        raw_target = str(payload.get("target", "")).strip()
        if not platform or not raw_target:
            return error_response("platform 和 target 不能为空", status_code=400)

        if action == "remove":
            key = f"{platform}:{raw_target}"
            self._remove_target_from_config(key)
            await self.store.remove_target_data(key)
            return json_response({"removed": key})

        display_name = str(payload.get("display_name", "")).strip()
        interval_min = int(payload.get("interval_min", 15))
        try:
            adapter = self.service.adapter(platform)
            info = await adapter.resolve(raw_target)
        except ServiceError as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:
            return error_response(f"解析失败：{exc}", status_code=400)

        self._upsert_target_in_config(
            platform=platform,
            target=info.target_id,
            display_name=display_name or info.display_name,
            interval_min=interval_min,
        )
        for session in self.config.get("push_sessions") or []:
            await self.store.unsuppress(str(session), f"{platform}:{info.target_id}")
        return json_response({"added": f"{platform}:{info.target_id}"})

    async def panel_check(self):
        payload = await request.json(default={})
        platform = str(payload.get("platform", "")).strip().lower()
        target = str(payload.get("target", "")).strip()
        if not platform or not target:
            return error_response("platform 和 target 不能为空", status_code=400)
        try:
            resolved, items = await self.service.check(platform, target)
        except ServiceError as exc:
            return error_response(str(exc), status_code=400)
        resolved.failures = 0
        resolved.paused_reason = None
        resolved.last_check_at = utc_now_iso()
        await self.store.upsert_target(resolved)
        await self.store.cache_items(resolved.key, list(reversed(items)), limit=50)
        summary = await self.summary.summarize(items)
        if summary:
            await self.store.set_summary(resolved.key, summary)
        return json_response(
            {
                "target": resolved.to_dict(),
                "items": [item.to_dict() for item in items],
                "summary": summary,
            }
        )

    async def panel_subscriptions(self):
        payload = await request.json(default={})
        action = payload.get("action", "")
        if action == "add":
            umo = str(payload.get("umo", "")).strip()
            if not umo:
                return error_response("umo 不能为空", status_code=400)
            from astrbot.core.platform.message_session import MessageSession

            try:
                MessageSession.from_str(umo)
            except ValueError:
                return error_response(
                    "UMO 格式应为 platform:message_type:session_id", status_code=400
                )

            targets = []
            requested = str(payload.get("target_key", "")).strip()
            if requested:
                targets.append(requested)
            else:
                for entry in self.config.get("watch_targets") or []:
                    if not entry.get("enabled", True):
                        continue
                    platform = str(entry.get("platform", "")).strip().lower()
                    target_id = str(entry.get("target", "")).strip()
                    if platform and target_id:
                        targets.append(f"{platform}:{target_id}")
            if not targets:
                return error_response("还没有盯住账号，先添加目标", status_code=400)

            added = 0
            errors = []
            for target_key in targets:
                platform, target_id = target_key.split(":", 1)
                try:
                    await self.service.add(umo, platform, target_id)
                except ServiceError as exc:
                    message = str(exc)
                    if "已经订阅" in message:
                        continue
                    errors.append(f"{target_key}: {message}")
                else:
                    added += 1
            if added == 0 and errors:
                return error_response("；".join(errors), status_code=400)
            return json_response({"ok": True, "added": added, "errors": errors})

        subscription_id = str(payload.get("id", "")).strip()
        if not subscription_id:
            return error_response("id 不能为空", status_code=400)
        subscriptions = await self.store.list_subscriptions()
        subscription = next(
            (item for item in subscriptions if item.id == subscription_id), None
        )
        if subscription is None:
            return error_response("订阅不存在", status_code=404)
        if action == "delete":
            await self.store.suppress(subscription.umo, subscription.target_key)
            await self.store.remove_subscription(subscription_id)
        elif action == "enable":
            subscription.enabled = True
            await self.store.update_subscription(subscription)
        elif action == "disable":
            subscription.enabled = False
            await self.store.update_subscription(subscription)
        else:
            return error_response("action 必须是 delete / enable / disable", status_code=400)
        return json_response({"ok": True})

    async def panel_xhs_login_start(self):
        try:
            return json_response(await self.xhs_login.start())
        except XhsLoginError as exc:
            return error_response(str(exc), status_code=400)

    async def panel_xhs_login_status(self):
        session_id = request.query.get("session", "", type=str)
        if not session_id:
            return error_response("session 不能为空", status_code=400)
        try:
            return json_response(await self.xhs_login.status(session_id))
        except XhsLoginError as exc:
            return error_response(str(exc), status_code=400)

    async def panel_xhs_cookie(self):
        payload = await request.json(default={})
        raw = str(payload.get("cookie", "")).strip()
        if not raw:
            return error_response("cookie 不能为空", status_code=400)
        try:
            self.xhs_login.save_cookie_string(raw)
        except XhsLoginError as exc:
            return error_response(str(exc), status_code=400)

        if self.config.get("xhs_provider", "cookie") == "cookie":
            xhs_target = None
            for entry in self.config.get("watch_targets") or []:
                if entry.get("platform") == "xhs" and entry.get("target"):
                    xhs_target = Target(
                        platform="xhs",
                        target_id=str(entry["target"]),
                        display_name=str(entry.get("display_name") or entry["target"]),
                    )
                    break
            if xhs_target is not None:
                try:
                    await self.adapters["xhs"].fetch(xhs_target, limit=1)
                except PlatformError as exc:
                    # The adapter deletes the cookie on a real login redirect;
                    # keep it for transient network/page errors so the user can retry.
                    return error_response(f"Cookie 验证失败：{exc}", status_code=400)
        return json_response({"ok": True, "logged_in": True})

    async def panel_xhs_logout(self):
        await self.xhs_login.logout()
        return json_response({"ok": True})

    def _config_status(self) -> dict:
        return {
            "github_token": bool(str(self.config.get("github_token", "")).strip()),
            "twitter_provider": self.config.get("twitter_provider", "fxtwitter"),
            "twitter_bearer_token": bool(
                str(self.config.get("twitter_bearer_token", "")).strip()
            ),
            "xhs_logged_in": self.xhs_login.logged_in(),
            "xhs_provider": self.config.get("xhs_provider", "cookie"),
            "rsshub_base": self.config.get("rsshub_base", ""),
            "rsshub_access_key": bool(
                str(self.config.get("rsshub_access_key", "")).strip()
            ),
            "card_enabled": bool(self.config.get("card_enabled", True)),
            "card_available": self.card.enabled,
            "search_enabled": bool(self.config.get("search_enabled", True)),
            "builtin_search": bool(
                (self.context.get_config().get("provider_settings") or {}).get("web_search")
            ),
            "push_sessions": len(self.config.get("push_sessions") or []),
            "summary_enabled": bool(self.config.get("summary_enabled", True)),
            "summary_api_key": bool(
                str(self.config.get("summary_api_key", "")).strip()
            ),
            "summary_api_base": self.config.get("summary_api_base", ""),
            "summary_model": self.config.get("summary_model", "gpt-4o-mini"),
        }

    def _upsert_target_in_config(
        self,
        platform: str,
        target: str,
        display_name: str,
        interval_min: int,
    ) -> None:
        targets = list(self.config.get("watch_targets") or [])
        key = f"{platform}:{target}"
        for entry in targets:
            if f"{entry.get('platform')}:{entry.get('target')}" == key:
                entry.update(
                    display_name=display_name,
                    interval_min=interval_min,
                    enabled=True,
                )
                break
        else:
            targets.append(
                {
                    "__template_key": "target",
                    "platform": platform,
                    "target": target,
                    "display_name": display_name,
                    "enabled": True,
                    "interval_min": interval_min,
                    "include": [],
                    "exclude": [],
                    "push_events": [],
                }
            )
        self.config["watch_targets"] = targets
        self.config.save_config()

    def _remove_target_from_config(self, key: str) -> None:
        targets = [
            entry
            for entry in (self.config.get("watch_targets") or [])
            if f"{entry.get('platform')}:{entry.get('target')}" != key
        ]
        self.config["watch_targets"] = targets
        self.config.save_config()
