const bridge = window.AstrBotPluginView;
const $ = (id) => document.getElementById(id);

let currentTargets = [];

function setMessage(text, ok = false) {
  for (const id of ["global-message", "xhs-message"]) {
    const el = $(id);
    if (!el) continue;
    el.textContent = text || "";
    el.className = ok ? "message ok" : "message";
  }
}

function setConfigMessage(text, ok = false) {
  const el = $("cfg-message");
  if (!el) return;
  el.textContent = text || "";
  el.className = ok ? "message ok" : "message";
}

async function apiGet(endpoint, params) {
  return bridge.apiGet(endpoint, params || {});
}

async function apiPost(endpoint, body) {
  return bridge.apiPost(endpoint, body || {});
}

async function loadStatus() {
  const data = await apiGet("panel/status");
  const config = data.config || {};
  currentTargets = data.targets || [];
  $("xhs-state").textContent = config.xhs_logged_in ? "已登录" : "未登录";
  $("xhs-state").className = config.xhs_logged_in ? "badge ok" : "badge warn";
  $("cfg-github").placeholder = config.github_token
    ? "已配置（留空不修改）"
    : "未配置";
  $("cfg-x-provider").value = config.twitter_provider || "fxtwitter";
  $("cfg-x-bearer").placeholder = config.twitter_bearer_token
    ? "已配置（留空不修改）"
    : "未配置";
  $("cfg-rsshub").value = config.rsshub_base || "";
  $("cfg-rsshub-key").placeholder = config.rsshub_access_key
    ? "已配置（留空不修改）"
    : "未配置";
  $("cfg-summary").checked = Boolean(config.summary_enabled);
  $("cfg-summary-base").value = config.summary_api_base || "";
  $("cfg-summary-key").placeholder = config.summary_api_key
    ? "已配置（留空不修改）"
    : "留空使用 AstrBot 当前模型";
  $("cfg-summary-model").value = config.summary_model || "gpt-4o-mini";
  renderTargets(currentTargets, data.latest || {}, data.summaries || {});
  renderSubscriptions(data.subscriptions || [], currentTargets);
}

function renderTargets(targets, latest, summaries) {
  const root = $("targets");
  root.innerHTML = "";
  if (!targets.length) {
    root.innerHTML = '<p class="muted">还没有目标；点右上角“添加目标”。</p>';
    return;
  }
  for (const target of targets) {
    const items = latest[target.key] || [];
    const summary = summaries[target.key] || "";
    const card = document.createElement("article");
    card.className = "target";
    const itemLines = items
      .slice(0, 3)
      .map((item) => {
        const title = item.title || item.text || item.url;
        return `<li><div>${escapeHtml(title).slice(0, 80)}</div><div class="meta">${escapeHtml(item.url)}</div></li>`;
      })
      .join("");
    card.innerHTML = `
      <h3>${escapeHtml(target.display_name || target.target_id)}</h3>
      <div class="meta">${escapeHtml(target.key)} · ${escapeHtml(target.last_check_at || "尚未检查")}${
        target.paused_reason ? ` · 最近失败：${escapeHtml(target.paused_reason)}` : ""
      }</div>
      ${summary ? `<div class="summary">AI 摘要：${escapeHtml(summary)}</div>` : ""}
      <ul class="items">${itemLines || "<li>暂无缓存内容</li>"}</ul>
      <div class="actions">
        <button class="btn primary" data-check="${escapeHtml(target.key)}">立即获取</button>
      </div>
    `;
    root.appendChild(card);
  }
  root.querySelectorAll("[data-check]").forEach((button) => {
    button.addEventListener("click", () => checkTarget(button.dataset.check));
  });
}

function renderSubscriptions(subscriptions, targets) {
  const names = new Map(targets.map((target) => [target.key, target.display_name]));
  const body = $("subscriptions");
  body.innerHTML = "";
  if (!subscriptions.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted">还没有订阅；用 /feed add 或添加目标。</td></tr>';
    return;
  }
  for (const sub of subscriptions) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(shortUmo(sub.umo))}</td>
      <td>${escapeHtml(names.get(sub.target_key) || sub.target_key)}</td>
      <td>${sub.enabled ? "开启" : "暂停"}</td>
      <td>${escapeHtml(sub.last_item_at || "-")}</td>
      <td>${escapeHtml(sub.last_error || "-")}</td>
      <td>
        <button class="btn" data-action="${sub.enabled ? "disable" : "enable"}" data-id="${sub.id}">
          ${sub.enabled ? "暂停" : "开启"}
        </button>
        <button class="btn" data-action="delete" data-id="${sub.id}">删除</button>
      </td>
    `;
    body.appendChild(row);
  }
  body.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      await apiPost("panel/subscriptions", {
        action: button.dataset.action,
        id: button.dataset.id,
      });
      await loadStatus();
    });
  });
}

async function checkTarget(key, quiet = false) {
  const [platform, ...rest] = key.split(":");
  if (!quiet) setMessage(`正在获取 ${key} …`);
  try {
    const data = await apiPost("panel/check", {
      platform,
      target: rest.join(":"),
    });
    await loadStatus();
    if (!quiet) {
      const count = data.items?.length || 0;
      setMessage(
        data.summary
          ? `已获取 ${count} 条；AI 摘要：${data.summary}`
          : `已获取 ${count} 条`,
        true,
      );
    }
    return { ok: true, count: data.items?.length || 0, key };
  } catch (error) {
    if (!quiet) setMessage(`获取失败：${error.message || error}`);
    return { ok: false, key, error: error.message || String(error) };
  }
}

async function saveXhsCookie() {
  const cookie = $("xhs-cookie").value.trim();
  if (!cookie) {
    setMessage("请先粘贴 Cookie。");
    return;
  }
  try {
    await apiPost("panel/xhs/cookie", { cookie });
    setMessage("Cookie 已保存。", true);
    await loadStatus();
  } catch (error) {
    setMessage(error.message || String(error));
  }
}

async function saveConfig() {
  setConfigMessage("正在保存…");
  try {
    await apiPost("panel/config", {
      github_token: $("cfg-github").value.trim(),
      twitter_provider: $("cfg-x-provider").value,
      twitter_bearer_token: $("cfg-x-bearer").value.trim(),
      rsshub_base: $("cfg-rsshub").value.trim(),
      rsshub_access_key: $("cfg-rsshub-key").value.trim(),
      summary_enabled: $("cfg-summary").checked,
      summary_api_base: $("cfg-summary-base").value.trim(),
      summary_api_key: $("cfg-summary-key").value.trim(),
      summary_model: $("cfg-summary-model").value.trim(),
    });
    await loadStatus();
    setConfigMessage("配置已保存并立即生效。", true);
  } catch (error) {
    setConfigMessage(error.message || String(error));
  }
}

async function addTarget() {
  const platform = $("target-platform").value;
  const target = $("target-id").value.trim();
  if (!target) return;
  try {
    await apiPost("panel/targets", {
      action: "add",
      platform,
      target,
      display_name: $("target-name").value.trim(),
      interval_min: Number($("target-interval").value) || 15,
    });
    $("add-target-dialog").close();
    await loadStatus();
    setMessage(
      "目标已添加；定时推送会话请在插件配置页的 push_sessions 中选择。",
      true,
    );
  } catch (error) {
    setMessage(error.message || String(error));
  }
}

function switchView(name) {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.viewPanel === name);
  });
}

async function addSubscription() {
  const umo = $("sub-umo").value.trim();
  if (!umo) {
    setMessage("请填写会话 UMO。");
    return;
  }
  try {
    const result = await apiPost("panel/subscriptions", {
      action: "add",
      umo,
    });
    $("add-subscription-dialog").close();
    await loadStatus();
    switchView("subs");
    const suffix = result.errors?.length ? `；部分失败：${result.errors.join("；")}` : "";
    setMessage(`已添加 ${result.added} 个账号订阅${suffix}`, true);
  } catch (error) {
    setMessage(error.message || String(error));
  }
}

function applyTheme(isDark) {
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
  document.body.classList.toggle("dark", isDark);
}

function shortUmo(umo) {
  return umo.length > 28 ? `${umo.slice(0, 14)}…${umo.slice(-10)}` : umo;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function init() {
  const context = await bridge.ready();
  applyTheme(context.isDark);
  bridge.onContext((next) => applyTheme(Boolean(next.isDark)));

  $("refresh").addEventListener("click", loadStatus);
  $("check-all").addEventListener("click", async () => {
    setMessage("正在检查全部目标…");
    const status = await apiGet("panel/status");
    const results = [];
    for (const target of status.targets || []) {
      results.push(await checkTarget(target.key, true));
    }
    const failed = results.filter((item) => !item.ok);
    if (failed.length) {
      setMessage(
        `完成：${results.length - failed.length} 个成功，${failed.length} 个失败（${failed
          .map((item) => item.key)
          .join("、")}）`,
      );
    } else {
      setMessage(`已检查全部目标，共 ${results.length} 个。`, true);
    }
  });
  $("xhs-logout").addEventListener("click", async () => {
    await apiPost("panel/xhs/logout");
    setMessage("已退出小红书登录。", true);
    await loadStatus();
  });
  $("xhs-save-cookie").addEventListener("click", saveXhsCookie);
  $("cfg-save").addEventListener("click", saveConfig);
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $("add-target").addEventListener("click", () => $("add-target-dialog").showModal());
  $("add-subscription").addEventListener("click", () => {
    $("add-subscription-dialog").showModal();
  });
  $("sub-submit").addEventListener("click", (event) => {
    event.preventDefault();
    addSubscription();
  });
  $("target-submit").addEventListener("click", (event) => {
    event.preventDefault();
    addTarget();
  });
  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = button.closest("dialog");
      if (dialog) dialog.close();
    });
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  await loadStatus();
  setInterval(loadStatus, 30000);
}

init().catch((error) => setMessage(error.message || String(error)));
