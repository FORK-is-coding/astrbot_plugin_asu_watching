# 阿酥盯盘器（astrbot_plugin_creator_feed）

> GitHub 仓库：[FORK-is-coding/AsuWatching](https://github.com/FORK-is-coding/AsuWatching)

盯住小红书、GitHub、X 创作者的动向，通过 QQ 指令查询、WebUI 面板查看，并支持定时推送。

## 当前状态

| 模块 | 状态 |
| --- | --- |
| 插件骨架 / metadata / 配置 schema | 已完成 |
| 存储（state.json，原子写） | 已完成 |
| `/feed` 指令组（add/list/del/on/off/check/now/resolve/panel/admin/search） | 已完成 |
| LLM tools（feed_check / feed_list / feed_search） | 已完成并注册 |
| 关联搜索（AstrBot 内置搜索优先，模型回退） | 已完成 |
| AI 摘要（推送/查询/卡片） | 已完成，支持 AstrBot 当前模型或自定义 OpenAI 兼容 Key |
| 面板内 API / 模型配置 | 已完成，保存后立即重建适配器 |
| GitHub 适配器（events + 类型映射） | 已完成，真实账号验证通过 |
| X 适配器（FxTwitter 默认 / x_api 备选） | 已完成，FxTwitter 偶发 404 会给出可操作提示 |
| 小红书适配器（cookie + 主页解析） | 已完成；扫码登录接口需要浏览器签名，当前提供 Cookie 粘贴登录 |
| WebUI 面板（四栏：动向 / 订阅 / 小红书 / API 配置） | 已完成 |
| 调度器 / 定时推送 / Pillow 卡片 | 已完成（含 GitHub Push 聚合、quiet hours、每小时/每日上限） |
| 小红书登录页 | Cookie 粘贴 + 退出登录（扫码接口需要浏览器签名，面板不再提供无效二维码按钮） |

## 安装

1. 将本目录放到 AstrBot 的 `data/plugins/` 下（目录名 `astrbot_plugin_creator_feed`），或直接：

```bash
git clone https://github.com/FORK-is-coding/AsuWatching data/plugins/astrbot_plugin_creator_feed
```
2. 重载插件。
3. 在 WebUI 的插件配置里填写：
   - `github_token`（可选，提升 GitHub 限额）；
   - `twitter_provider`：默认 `fxtwitter`（免费），需要更稳可改 `x_api` 并填 `twitter_bearer_token`；
   - `push_sessions`：定时推送的会话（可多选）；配置里的 `watch_targets` 会推到这些会话；
   - `timezone`：quiet_hours 使用的时区，默认 `Asia/Shanghai`；
   - `search_builtin_fallback`：优先使用 AstrBot 内置搜索，失败/未配置时再用模型；
   - `summary_enabled` / `summary_api_base` / `summary_api_key` / `summary_model`：AI 摘要；留空 Base/Key 时用 AstrBot 当前模型；
   - `card_font_path`：可留空，自动查找系统中文字体（本机实测 `C:/Windows/Fonts/msyh.ttc` 可用）。

## AI 摘要

- 定时推送按会话合并所有账号的新条目，并生成**一条整合摘要**；`/feed check`、`/feed now`、LLM tool 和面板「立即获取」也会生成 2–3 句摘要。
- 默认使用 AstrBot 当前对话模型；在面板或配置页填写 `summary_api_base` + `summary_api_key` + `summary_model` 后，改为直连 OpenAI 兼容接口。
- 摘要失败不会影响推送：失败时只省略摘要，正文/链接照常发送。
- 卡片顶部会显示「AI 摘要」区块，文字消息里是 `AI 摘要：…`。

## 面板

- 左侧四栏切换：**动向**（目标/最新动态）、**订阅**（会话订阅表 + 添加会话 ID，一次订阅全部盯住账号）、**小红书**（Cookie 登录）、**API 配置**（平台 Key 与 AI 摘要）。
- 订阅页「添加会话 ID」用于给单个会话加目标订阅，不影响配置页的 `push_sessions`。

## 订阅模型

- 配置里的 `watch_targets` 是全局盯住列表，会通过 `push_sessions` 推送到配置页选中的会话。
- `/feed add` 是「当前会话」的独立订阅：只推给当前会话，调度器会把它加入轮询目标，不需要改配置。
- 删除配置来源的订阅会写入抑制记录，避免下一轮被自动重建；重新 `/feed add` 可恢复。

## 指令

**跟紧酥神**（顶层指令，唤醒前缀 + 指令）：

```
/跟紧酥神
/紧跟酥神
/酥神动向
```

立刻抓取所有启用目标的最新 5 条，合并成一条消息 + 一张聚合卡片 + 一条整合 AI 摘要，发到当前会话；不推进订阅游标。

`/feed` 指令组：

```
/feed add <平台> <账号|链接> [--name 别名]
/feed list
/feed del <序号|平台 账号>
/feed on|off <序号>
/feed check <平台> <账号>
/feed now
/feed resolve <链接>
/feed panel
/feed admin targets | del <umo>
/feed search <关键词>
```

平台：`github`、`x`、`xhs`。

## 开发

```bash
pytest tests
```

需要把仓库根目录和 AstrBot 源码加入 `PYTHONPATH`；测试只覆盖纯逻辑与解析，不发真实网络请求。

## 已知限制

- 小红书没有公开 API；扫码登录接口需要浏览器签名，当前以「Cookie 粘贴」为主（扫码按钮会提示该限制）。保存 Cookie 时会用配置里的小红书目标验证一次；适配器走 cookie + 主页 `__INITIAL_STATE__` 解析，页面改版或 cookie 过期会明确报错并删除失效登录态。
- FxTwitter 是非官方免费源，偶尔 404；可切换 `twitter_provider=x_api`（需要 Bearer，按量计费）。
- GitHub Push 目前是同一轮询批次内聚合；2 小时跨批次 digest 尚未实现。
- 面板「立即获取」是同步请求，XHS 可能需要十几秒；没有 job 队列。
- qq_official 群主动推送需要先在群里让机器人收到一条消息（插件会在启动/平台加载时为 `push_sessions` 里的群补 scene），否则平台可能静默跳过。
- GitHub Events API 只覆盖最近 30 天/最多 300 条事件。
- 图片卡片使用本地 Pillow 绘制，系统没有中文字体时自动降级为文本。
- 单 bot 插件，不处理多实例重复推送。
- `/feed check` 只读；面板「立即获取」会刷新该目标的失败/暂停状态。
- 搜索优先用 AstrBot 内置搜索；没有内置搜索时用模型回答，输出会标注“未使用内置搜索，链接未验证”。
- 小红书目标需要用户提供有效 Cookie；开发时用临时 Cookie 验证过主页解析，仓库不附带 Cookie。
