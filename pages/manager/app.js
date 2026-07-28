let bridge = null;

const bands = ["高好感 / 信任圈", "朋友", "普通熟人", "保持距离", "边界警戒"];

const CONFIG_GROUPS = [
  { title: "情绪追踪", prefix: "MOOD_" },
  { title: "好感计算", prefix: "AFFINITY_" },
  { title: "信任计算", prefix: "TRUST_" },
  { title: "熟悉度计算", prefix: "FAMILIARITY_" },
  { title: "衰减速率", prefix: "DECAY_" },
  { title: "策略与持久化", prefix: "POLICY_" },
  { title: "存储与日志", prefix: "SAVE_" },
  { title: "存储与日志", prefix: "LOG_" },
];

let configSchema = {};
let configValues = {};

function $(selector) {
  return document.querySelector(selector);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  })[ch]);
}

function formatTime(ts) {
  if (!ts) return "—";
  const date = new Date(ts * 1000);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleString("zh-CN", { hour12: false });
}

function toast(message, error = false) {
  const element = $("#toast");
  if (!element) return;
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.add("hidden"), 3000);
}

async function resolveBridge(timeout = 3000) {
  if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
  if (typeof window.waitForAstrBotBridge === "function") {
    return window.waitForAstrBotBridge(timeout);
  }

  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    await new Promise((resolve) => setTimeout(resolve, 50));
    if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
  }

  throw new Error("请从 AstrBot 插件管理页打开此页面");
}

function parseJsonResponse(value) {
  const data = typeof value === "string" ? JSON.parse(value) : value;
  if (data?.success === false) {
    throw new Error(data.error || data.detail || "请求失败");
  }
  return data?.data ?? data;
}

async function apiGet(name) {
  if (!bridge || typeof bridge.apiGet !== "function") {
    throw new Error("AstrBot 页面通信接口尚未就绪");
  }
  return parseJsonResponse(await bridge.apiGet(name));
}

async function apiPost(name, body) {
  if (!bridge || typeof bridge.apiPost !== "function") {
    throw new Error("AstrBot 页面通信接口尚未就绪");
  }
  return parseJsonResponse(await bridge.apiPost(name, body));
}

function render(payload) {
  const summary = payload?.summary || {};
  const policy = payload?.policy || {};

  document.querySelectorAll("[data-stat]").forEach((element) => {
    element.textContent = summary[element.dataset.stat] ?? 0;
  });
  document.querySelectorAll("[data-policy]").forEach((element) => {
    element.textContent = policy[element.dataset.policy] ?? "—";
  });

  const counts = summary.bands || {};
  const max = Math.max(1, ...bands.map((name) => counts[name] || 0));
  $("#band-chart").innerHTML = bands.map((name) => (
    `<div class="band-row"><div class="band-label">${escapeHtml(name)}</div>`
    + `<div class="bar-track"><div class="bar-fill" style="width:${((counts[name] || 0) / max * 100).toFixed(1)}%"></div></div>`
    + `<div class="band-count">${counts[name] || 0}</div></div>`
  )).join("");

  const users = payload?.users || [];
  const tbody = $("#relation-tbody");
  if (!users.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="9">暂无关系记录</td></tr>';
    return;
  }

  tbody.innerHTML = users.map((user) => (
    `<tr><td>${escapeHtml(user.user_id)}</td><td>${escapeHtml(user.band)}</td>`
    + `<td>${user.affinity}</td><td>${user.trust}</td><td>${user.familiarity}</td><td>${user.interaction_count}</td>`
    + `<td>${user.whitelisted ? '<span class="badge ok">白名单</span>' : '<span class="badge">普通</span>'}</td>`
    + `<td>${user.boundary === "开放" ? '<span class="badge safe">开放</span>' : '<span class="badge warn">谨慎</span>'}</td>`
    + `<td>${formatTime(user.last_event_at)}</td></tr>`
  )).join("");
}

async function load() {
  const button = $("#btn-refresh");
  if (button) button.disabled = true;
  try {
    render(await apiGet("overview"));
  } catch (error) {
    toast(`加载关系状态失败：${error?.message || String(error)}`, true);
    $("#relation-tbody").innerHTML = '<tr class="empty-row"><td colspan="9">加载失败</td></tr>';
  } finally {
    if (button) button.disabled = false;
  }
}

function renderConfigField(key, field, value) {
  const id = `cfg-${key}`;
  const desc = escapeHtml(field.description || key);
  const label = `<label for="${id}" title="${desc}">${escapeHtml(key)}</label>`;
  let input;
  if (field.type === "bool") {
    const checked = value === true || value === "true" ? " checked" : "";
    input = `<input type="checkbox" id="${id}" data-key="${key}"${checked} />`;
  } else if (field.options) {
    const opts = field.options.map((opt) => (
      `<option value="${escapeHtml(opt)}"${String(value) === String(opt) ? " selected" : ""}>${escapeHtml(opt)}</option>`
    )).join("");
    input = `<select id="${id}" data-key="${key}">${opts}</select>`;
  } else {
    const step = field.type === "float" ? "any" : "1";
    const min = field.minimum ?? "";
    const max = field.maximum ?? "";
    input = `<input type="number" id="${id}" data-key="${key}" step="${step}"` +
      (min !== "" ? ` min="${min}"` : "") + (max !== "" ? ` max="${max}"` : "") +
      ` value="${escapeHtml(value)}" />`;
  }
  return `<div class="config-field">${label}<div class="config-input">${input}` +
    `<span class="config-hint">${desc}</span></div></div>`;
}

function renderConfigForm(schema, config) {
  const form = $("#config-form");
  if (!form) return;
  const used = new Set();
  const sections = CONFIG_GROUPS.map((group) => {
    const fields = Object.entries(schema)
      .filter(([key]) => key.startsWith(group.prefix) && !used.has(key))
      .map(([key, field]) => {
        used.add(key);
        return renderConfigField(key, field, config[key]);
      });
    if (!fields.length) return "";
    return `<div class="config-group"><h3>${escapeHtml(group.title)}</h3>${fields.join("")}</div>`;
  }).join("");

  const remaining = Object.entries(schema)
    .filter(([key]) => !used.has(key))
    .map(([key, field]) => renderConfigField(key, field, config[key]));
  const extra = remaining.length
    ? `<div class="config-group"><h3>其他</h3>${remaining.join("")}</div>`
    : "";

  form.innerHTML = sections + extra || "<p class=\"config-loading\">无可配置项</p>";
}

async function loadConfig() {
  try {
    const data = await apiGet("config");
    configSchema = data.schema || {};
    configValues = data.config || {};
    renderConfigForm(configSchema, configValues);
  } catch (error) {
    $("#config-form").innerHTML = `<p class="config-loading">加载配置失败：${escapeHtml(error?.message || String(error))}</p>`;
  }
}

function collectConfigChanges() {
  const changes = {};
  document.querySelectorAll("#config-form [data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (el.type === "checkbox") {
      if (el.checked !== configValues[key]) changes[key] = el.checked;
    } else {
      const raw = el.value;
      const field = configSchema[key];
      if (!field) return;
      if (field.type === "int") {
        changes[key] = parseInt(raw, 10);
      } else if (field.type === "float") {
        changes[key] = parseFloat(raw);
      } else {
        changes[key] = raw;
      }
    }
  });
  return changes;
}

async function saveConfig() {
  const button = $("#btn-save-config");
  if (button) button.disabled = true;
  try {
    const changes = collectConfigChanges();
    if (!Object.keys(changes).length) {
      toast("没有需要保存的变更");
      return;
    }
    const data = await apiPost("config", changes);
    configValues = data.config || {};
    renderConfigForm(configSchema, configValues);
    toast("配置已保存并热应用");
  } catch (error) {
    toast(`保存配置失败：${error?.message || String(error)}`, true);
  } finally {
    if (button) button.disabled = false;
  }
}

function resetConfigForm() {
  renderConfigForm(configSchema, configValues);
  toast("已重置为当前生效配置");
}

function initTabs() {
  document.querySelectorAll(".tabs button[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      document.querySelectorAll(".tabs button[data-tab]").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".panel[data-panel]").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = document.querySelector(`.panel[data-panel="${target}"]`);
      if (panel) panel.classList.add("active");
    });
  });
}

async function init() {
  bridge = await resolveBridge();
  if (typeof bridge.ready === "function") await bridge.ready();
  if (!bridge || typeof bridge.apiGet !== "function") {
    throw new Error("AstrBot 页面通信接口不可用");
  }
  initTabs();
  $("#btn-refresh").addEventListener("click", load);
  $("#btn-save-config").addEventListener("click", saveConfig);
  $("#btn-reset-config").addEventListener("click", resetConfigForm);
  await load();
  await loadConfig();
}

init().catch((error) => {
  toast(`页面启动失败：${error?.message || String(error)}`, true);
  $("#relation-tbody").innerHTML = '<tr class="empty-row"><td colspan="9">页面启动失败</td></tr>';
});
