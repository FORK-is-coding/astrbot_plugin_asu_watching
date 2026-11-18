from __future__ import annotations

import json

MODEL_PROMPT = """你是一个资讯检索助手。请联网搜索关键词「{keyword}」，返回 {limit} 条最相关的结果。
每条包含：标题、一句摘要、链接。只输出结果列表，不要编造；没有结果就写“没有找到相关结果”。"""

BUILTIN_TOOL_NAMES = {
    "tavily": "TavilyWebSearchTool",
    "bocha": "BochaWebSearchTool",
    "brave": "BraveWebSearchTool",
    "serpapi": "SerpApiWebSearchTool",
    "firecrawl": "FirecrawlWebSearchTool",
    "baidu_ai_search": "BaiduWebSearchTool",
    "exa": "ExaWebSearchTool",
    "anysearch": "AnySearchWebSearchTool",
    "deepseek": "DeepSeekWebSearchTool",
}


class SearchService:
    def __init__(self, context, config):
        self.context = context
        self.config = config

    async def search(self, keyword: str, event=None) -> str:
        limit = int(self.config.get("search_max_results", 5))
        if self.config.get("search_builtin_fallback", True) and event is not None:
            builtin = await self._builtin_search(keyword, limit, event)
            if builtin:
                return builtin

        umo = event.unified_msg_origin if event is not None else None
        text, error = await self._model_search(keyword, limit, umo)
        if text:
            return "（未使用内置搜索，以下为模型回答，链接未验证）\n" + text
        if error:
            return f"搜索失败：{error}"
        return "没有找到相关结果。"

    async def _model_search(
        self, keyword: str, limit: int, umo: str | None
    ) -> tuple[str, str]:
        try:
            provider = await self.context.get_using_provider_async(umo=umo)
        except Exception as exc:
            return "", str(exc)
        if provider is None:
            return "", "没有可用的对话模型"
        try:
            response = await provider.text_chat(
                prompt=MODEL_PROMPT.format(keyword=keyword, limit=limit)
            )
        except Exception as exc:
            return "", str(exc)
        return (response.completion_text or "").strip(), ""

    async def _builtin_search(self, keyword: str, limit: int, event) -> str:
        try:
            from astrbot.api.agents import AstrAgentContext, ContextWrapper
        except ImportError:  # AstrBot 4.29
            from astrbot.core.agent.run_context import ContextWrapper
            from astrbot.core.astr_agent_context import AstrAgentContext

        config = self.context.get_config(umo=event.unified_msg_origin)
        settings = config.get("provider_settings", {})
        if not settings.get("web_search"):
            return ""
        provider = settings.get("websearch_provider", "tavily")
        class_name = BUILTIN_TOOL_NAMES.get(provider)
        if not class_name:
            return ""

        from astrbot.core.astr_agent_runtime.tools import web_search_tools

        tool_class = getattr(web_search_tools, class_name, None)
        if tool_class is None:
            return ""
        tool = self.context.get_llm_tool_manager().get_builtin_tool(tool_class)
        try:
            result = await tool.call(
                ContextWrapper(
                    AstrAgentContext(context=self.context, event=event)
                ),
                query=keyword,
                max_results=max(5, min(limit, 20)),
            )
        except Exception:
            return ""
        return self._format_builtin(result)

    def _format_builtin(self, result) -> str:
        if isinstance(result, str):
            if result.startswith("Error"):
                return ""
            try:
                payload = json.loads(result)
            except json.JSONDecodeError:
                return result
        elif isinstance(result, dict):
            payload = result
        else:
            return ""

        results = payload.get("results") if isinstance(payload, dict) else None
        if not results:
            return ""
        lines = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("url") or ""
            url = item.get("url") or ""
            snippet = (item.get("snippet") or "").replace("\n", " ").strip()
            if len(snippet) > 160:
                snippet = snippet[:160] + "…"
            lines.append(f"- {title}\n  {snippet}\n  {url}".rstrip())
        return "\n".join(lines)
