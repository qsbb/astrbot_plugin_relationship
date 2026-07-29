let bridge = null;

const bands = ["高好感 / 信任圈", "朋友", "普通熟人", "保持距离", "边界警戒"];

const CONFIG_GROUPS = [
  { title: "情绪追踪", prefix: "MOOD_" },
  { title: "短期态度", prefix: "AFFECT_" },
  { title: "好感计算", prefix: "AFFINITY_" },
  { title: "信任计算", prefix: "TRUST_" },
  { title: "熟悉度计算", prefix: "FAMILIARITY_" },
  { title: "关系成长", prefix: "DYNAMICS_" },
  { title: "衰减速率", prefix: "DECAY_" },
  { title: "关系人格", prefix: "RELATIONSHIP_" },
  { title: "提示词", prefix: "PROMPT_" },
  { title: "跨平台记忆", prefix: "CROSS_PLATFORM_MEMORY_" },
  { title: "策略与持久化", prefix: "POLICY_" },
  { title: "存储与日志", prefix: "SAVE_" },
  { title: "存储与日志", prefix: "LOG_" },
];

let configSchema = {};
let configValues = {};
let identities = [];
let overviewUsers = [];
let relationshipProfiles = ["default"];
let defaultRelationshipProfile = "default";
let identityMergeSource = null;
let identityMergeConfirmTimer = null;
let pendingDeletePersonId = "";
let pendingDeleteTimer = null;

const API_ERROR_MESSAGES = {
  RELATIONSHIP_STORAGE_READ_ONLY: "关系数据由更高版本写入，当前版本已暂停账号归属修改；请先升级插件",
};

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
    throw new Error(API_ERROR_MESSAGES[data.error] || data.error || data.detail || "请求失败");
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
  overviewUsers = users;
  const tbody = $("#relation-tbody");
  if (!users.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="11">暂无关系记录</td></tr>';
    return;
  }

  tbody.innerHTML = users.map((user, index) => {
    const orphaned = Boolean(user.orphaned_person_id);
    const actionLabel = user.person_id ? "编辑归属" : (orphaned ? "合并历史关系" : "快速归属");
    const identityHint = orphaned
      ? `<small class="user-id">${escapeHtml(user.orphaned_person_id)} · 账号归属已删除</small>`
      : (user.display_name
        ? `<small class="user-id">${escapeHtml(user.user_id)} · ${user.linked_accounts} 个账号</small>`
        : "");
    return (`<tr><td>${escapeHtml(user.display_name || user.user_id)}`
    + `${identityHint}</td>`
    + `<td><code class="profile-id">${escapeHtml(user.relationship_profile_id || defaultRelationshipProfile)}</code></td>`
    + `<td>${escapeHtml(user.band)}</td>`
    + `<td>${user.affinity}</td><td>${user.trust}</td><td>${user.familiarity}</td><td>${user.interaction_count}</td>`
    + `<td>${user.whitelisted ? '<span class="badge ok">白名单</span>' : '<span class="badge">普通</span>'}</td>`
    + `<td>${user.boundary === "开放" ? '<span class="badge safe">开放</span>' : '<span class="badge warn">谨慎</span>'}</td>`
    + `<td>${formatTime(user.last_event_at)}</td>`
    + `<td><button type="button" class="quick-edit-command" data-quick-edit="${index}">`
    + `${actionLabel}</button></td></tr>`);
  }).join("");
}

async function load() {
  const button = $("#btn-refresh");
  if (button) button.disabled = true;
  try {
    render(await apiGet("overview"));
  } catch (error) {
    toast(`加载关系状态失败：${error?.message || String(error)}`, true);
    $("#relation-tbody").innerHTML = '<tr class="empty-row"><td colspan="11">加载失败</td></tr>';
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
  } else if (field.type === "string") {
    const wideClass = key.endsWith("_MAP") ? " config-text-wide" : "";
    input = `<input type="text" class="config-text${wideClass}" id="${id}" data-key="${key}" value="${escapeHtml(value)}" />`;
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
      let value;
      if (field.type === "int") {
        value = parseInt(raw, 10);
      } else if (field.type === "float") {
        value = parseFloat(raw);
      } else {
        value = raw;
      }
      if (!Object.is(value, configValues[key])) changes[key] = value;
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
    toast(data.restart_required ? "配置已保存；旧数据归属需重启后生效" : "配置已保存并热应用");
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

function accountRow(account = {}) {
  return `<div class="account-row">`
    + `<label>平台 ID<input data-account="platform_id" type="text" maxlength="120" value="${escapeHtml(account.platform_id || "")}" /></label>`
    + `<label>UID<input data-account="user_id" type="text" maxlength="120" value="${escapeHtml(account.user_id || "")}" /></label>`
    + `<label>Bot ID<input data-account="bot_id" type="text" maxlength="120" value="${escapeHtml(account.bot_id || "")}" /></label>`
    + `<label>UMO<input data-account="session_id" type="text" maxlength="240" value="${escapeHtml(account.session_id || "")}" /></label>`
    + `<label>记忆人格 ID<input data-account="memory_profile_id" type="text" maxlength="64" value="${escapeHtml(account.memory_profile_id || "")}" placeholder="留空使用默认人格" /></label>`
    + `<label>备注<input data-account="label" type="text" maxlength="80" value="${escapeHtml(account.label || "")}" /></label>`
    + `<button type="button" class="remove-account icon-command danger-command" title="移除账号" aria-label="移除账号">×</button>`
    + `</div>`;
}

function setInitialPriorAvailability(enabled, hint = "仅能为尚未产生互动的新关系设置一次。") {
  const select = $("#initial-prior");
  select.disabled = !enabled;
  if (!enabled) select.value = "";
  $("#initial-prior-hint").textContent = hint;
}

function hideIdentityMerge() {
  clearTimeout(identityMergeConfirmTimer);
  identityMergeConfirmTimer = null;
  identityMergeSource = null;
  const panel = $("#identity-merge-panel");
  panel.hidden = true;
  $("#identity-merge-target").innerHTML = "";
  const button = $("#btn-merge-identity");
  button.disabled = false;
  button.dataset.confirmed = "";
  button.textContent = "合并到此身份";
}

function resetIdentityMergeConfirmation() {
  clearTimeout(identityMergeConfirmTimer);
  identityMergeConfirmTimer = null;
  const button = $("#btn-merge-identity");
  button.dataset.confirmed = "";
  button.textContent = identityMergeSource?.type === "account" ? "合并账号" : "合并身份";
}

function renderIdentityMergeTargets() {
  if (!identityMergeSource) return;
  const excluded = identityMergeSource.type === "person"
    ? identityMergeSource.source_person_id
    : "";
  const targets = identities.filter((person) => person.person_id !== excluded);
  const panel = $("#identity-merge-panel");
  if (!targets.length) {
    panel.hidden = true;
    return;
  }
  $("#identity-merge-target").innerHTML = targets.map((person) => (
    `<option value="${escapeHtml(person.person_id)}">${escapeHtml(person.display_name)} · ${escapeHtml(person.person_id)}</option>`
  )).join("");
  panel.hidden = false;
}

function showIdentityMerge(source) {
  identityMergeSource = source;
  const isAccount = source.type === "account";
  const isOrphan = source.type === "orphan";
  $("#identity-merge-title").textContent = isAccount
    ? "也可以合并到已有身份"
    : (isOrphan ? "合并未归属历史关系" : "合并当前身份");
  $("#identity-merge-hint").textContent = isAccount
    ? "把这个平台账号及其已有关系合并到已确认的自然人。"
    : (isOrphan
      ? "把删除归属后保留的历史关系迁移到已确认的自然人。"
      : "来源身份会被移除；账号、关系和记忆归属会并入目标身份。");
  $("#btn-merge-identity").textContent = isAccount ? "合并账号" : "合并身份";
  renderIdentityMergeTargets();
}

function resetIdentityEditor(accountCount = 1) {
  hideIdentityMerge();
  $("#identity-editor-title").textContent = "新建自然人";
  $("#person-display-name").value = "";
  $("#person-display-name").readOnly = false;
  $("#person-id").value = "";
  $("#person-id").readOnly = false;
  renderRelationshipProfileOptions(defaultRelationshipProfile);
  $("#initial-prior").value = "";
  setInitialPriorAvailability(true);
  $("#account-list").innerHTML = Array.from({ length: accountCount }, () => accountRow()).join("");
  $("#btn-add-account").hidden = false;
  $("#btn-save-person").hidden = false;
  $("#btn-save-person").textContent = "保存账号归属";
}

function editIdentity(person) {
  hideIdentityMerge();
  $("#identity-editor-title").textContent = "编辑账号归属";
  $("#person-display-name").value = person.display_name || "";
  $("#person-display-name").readOnly = false;
  $("#person-id").value = person.person_id || "";
  $("#person-id").readOnly = true;
  renderRelationshipProfileOptions(person.relationship_profile_id || defaultRelationshipProfile);
  $("#initial-prior").value = "";
  setInitialPriorAvailability(true);
  $("#account-list").innerHTML = (person.accounts || []).map(accountRow).join("") || accountRow();
  $("#btn-add-account").hidden = false;
  $("#btn-save-person").hidden = false;
  $("#btn-save-person").textContent = "保存账号归属";
}

function activateTab(target) {
  if (target !== "identities") resetIdentityMergeConfirmation();
  document.querySelectorAll(".tabs button[data-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === target);
  });
  document.querySelectorAll(".panel[data-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === target);
  });
}

async function quickEditRelationship(index) {
  const user = overviewUsers[index];
  if (!user) return;
  activateTab("identities");

  if (user.person_id) {
    let person = identities.find((item) => item.person_id === user.person_id);
    if (!person) {
      await loadIdentities();
      person = identities.find((item) => item.person_id === user.person_id);
    }
    if (!person) {
      toast("未找到该自然人的账号归属，请刷新后重试", true);
      return;
    }
    editIdentity(person);
    document.querySelector(".identity-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  if (user.orphaned_person_id) {
    resetIdentityEditor(0);
    $("#identity-editor-title").textContent = "未归属历史关系";
    $("#person-display-name").value = "账号归属已删除";
    $("#person-display-name").readOnly = true;
    $("#person-id").value = user.orphaned_person_id;
    $("#person-id").readOnly = true;
    renderRelationshipProfileOptions(user.relationship_profile_id || defaultRelationshipProfile);
    setInitialPriorAvailability(false, "历史关系不会被初始关系覆盖。");
    $("#account-list").innerHTML = '<p class="config-loading">账号归属已删除；关系与记忆原始数据仍保留。</p>';
    $("#btn-add-account").hidden = true;
    $("#btn-save-person").hidden = true;
    showIdentityMerge({ type: "orphan", source_person_id: user.orphaned_person_id });
    document.querySelector(".identity-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  const account = user.quick_account || {};
  resetIdentityEditor(0);
  $("#identity-editor-title").textContent = "从关系记录创建账号归属";
  $("#person-display-name").value = account.display_name || user.user_id || "";
  $("#person-id").value = "";
  $("#person-id").readOnly = false;
  renderRelationshipProfileOptions(user.relationship_profile_id || defaultRelationshipProfile);
  setInitialPriorAvailability(false, "该账号已有互动，将保留现有关系，不应用初始关系。");
  $("#account-list").innerHTML = accountRow(account);
  $("#btn-save-person").textContent = "保存为新身份";
  showIdentityMerge({ type: "account" });

  const missing = [];
  if (!account.platform_id) missing.push("平台 ID");
  if (!account.bot_id) missing.push("Bot ID");
  if (!account.session_id) missing.push("私聊 UMO");
  toast(missing.length
    ? `已填入可确认字段；缺少${missing.join("、")}，请先私聊 Bot 一次后刷新`
    : "已自动填入最近一次真实私聊的账号信息，请确认后保存");
  document.querySelector(".identity-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderRelationshipProfileOptions(selectedProfile) {
  const select = $("#relationship-profile-id");
  if (!select) return;
  const selected = selectedProfile || defaultRelationshipProfile;
  const values = [...new Set([...relationshipProfiles, selected])];
  select.innerHTML = values.map((profileId) => (
    `<option value="${escapeHtml(profileId)}"${profileId === selected ? " selected" : ""}>${escapeHtml(profileId)}</option>`
  )).join("");
}

function clearDeleteConfirmation(rerender = true) {
  pendingDeletePersonId = "";
  clearTimeout(pendingDeleteTimer);
  pendingDeleteTimer = null;
  if (rerender) renderIdentityList();
}

function armDeleteIdentity(personId) {
  pendingDeletePersonId = personId;
  clearTimeout(pendingDeleteTimer);
  pendingDeleteTimer = setTimeout(() => clearDeleteConfirmation(), 8000);
  renderIdentityList();
  toast("请在 8 秒内再次点击“确认删除”；关系和记忆原始数据不会被删除");
}

function renderIdentityList() {
  const list = $("#identity-list");
  if (!identities.length) {
    list.innerHTML = '<p class="config-loading">暂无自然人身份</p>';
    return;
  }
  list.innerHTML = identities.map((person) => {
    const pending = pendingDeletePersonId === person.person_id;
    const mergeButton = identities.length > 1
      ? '<button type="button" data-action="merge">合并</button>'
      : "";
    return (`<div class="identity-item" data-person-id="${escapeHtml(person.person_id)}">`
      + `<div><strong>${escapeHtml(person.display_name)}</strong><span>${escapeHtml(person.person_id)}</span></div>`
      + `<span class="account-count">${(person.accounts || []).length} 个账号</span>`
      + `<div class="identity-actions"><button type="button" data-action="edit">编辑</button>`
      + mergeButton
      + `<button type="button" data-action="${pending ? "confirm-delete" : "delete"}" class="danger-command">`
      + `${pending ? "确认删除" : "删除"}</button></div></div>`);
  }).join("");
}

function renderIdentities(payload) {
  identities = payload?.persons || [];
  defaultRelationshipProfile = payload?.default_relationship_profile || "default";
  relationshipProfiles = Array.isArray(payload?.relationship_profiles)
    ? payload.relationship_profiles.filter(Boolean)
    : [];
  if (!relationshipProfiles.includes(defaultRelationshipProfile)) {
    relationshipProfiles.unshift(defaultRelationshipProfile);
  }
  renderRelationshipProfileOptions($("#relationship-profile-id")?.value || defaultRelationshipProfile);
  const bridgeAvailable = payload?.memory_companion?.available === true;
  $("#memory-bridge-status").textContent = bridgeAvailable
    ? "Memory Companion 已就绪"
    : "Memory Companion 未就绪，关系仍会跨平台共享";
  if (pendingDeletePersonId && !identities.some((person) => person.person_id === pendingDeletePersonId)) {
    clearDeleteConfirmation(false);
  }
  renderIdentityList();
  renderIdentityMergeTargets();
}

async function loadIdentities() {
  try {
    renderIdentities(await apiGet("identities"));
  } catch (error) {
    $("#identity-list").innerHTML = `<p class="config-loading">加载失败：${escapeHtml(error?.message || String(error))}</p>`;
  }
}

function collectIdentity() {
  const accounts = [...document.querySelectorAll("#account-list .account-row")].map((row) => {
    const account = {};
    row.querySelectorAll("[data-account]").forEach((input) => {
      account[input.dataset.account] = input.value.trim();
    });
    return account;
  }).filter((account) => account.platform_id || account.user_id);
  return {
    person_id: $("#person-id").value.trim(),
    display_name: $("#person-display-name").value.trim(),
    relationship_profile_id: $("#relationship-profile-id").value,
    initial_prior: $("#initial-prior").value,
    accounts,
  };
}

async function saveIdentity() {
  const button = $("#btn-save-person");
  button.disabled = true;
  try {
    const payload = collectIdentity();
    if (!payload.display_name || !payload.accounts.length) {
      throw new Error("请填写显示名称和至少一个平台账号");
    }
    const result = await apiPost("identities", payload);
    await Promise.all([loadIdentities(), load()]);
    resetIdentityEditor();
    if (result?.initial_prior?.requested === true && result.initial_prior.applied !== true) {
      const errorCode = result.initial_prior.error || "INITIAL_PRIOR_REJECTED";
      if (errorCode === "RELATIONSHIP_ALREADY_ACTIVE") {
        toast("账号归属已保存；该关系已有互动，已保留现有关系");
      } else if (errorCode === "INITIAL_PRIOR_ALREADY_APPLIED") {
        toast("账号归属已保存；初始关系此前已设置过，本次未重复应用");
      } else {
        toast(`账号归属已保存，但初始关系未应用（${errorCode}）`, true);
      }
    } else {
      toast(result?.initial_prior?.applied === true ? "账号归属和初始关系已保存" : "账号归属已保存");
    }
  } catch (error) {
    toast(`保存失败：${error?.message || String(error)}`, true);
  } finally {
    button.disabled = false;
  }
}

async function mergeIdentity() {
  const source = identityMergeSource;
  const button = $("#btn-merge-identity");
  const targetPersonId = $("#identity-merge-target").value;
  if (!source || !targetPersonId) return;
  if (source.type !== "account" && button.dataset.confirmed !== "true") {
    button.dataset.confirmed = "true";
    button.textContent = "确认合并";
    clearTimeout(identityMergeConfirmTimer);
    identityMergeConfirmTimer = setTimeout(() => {
      resetIdentityMergeConfirmation();
      toast("合并确认已取消，请重新选择");
    }, 8000);
    toast("请在 8 秒内再次点击“确认合并”；来源身份将不再单独保留");
    return;
  }

  clearTimeout(identityMergeConfirmTimer);
  identityMergeConfirmTimer = null;
  button.disabled = true;
  button.textContent = "合并中…";
  try {
    const payload = { target_person_id: targetPersonId };
    if (source.type === "account") {
      const account = collectIdentity().accounts[0];
      if (!account?.platform_id || !account?.user_id) {
        throw new Error("请先确认平台 ID 和 UID");
      }
      payload.account = account;
    } else {
      payload.source_person_id = source.source_person_id;
    }
    const result = await apiPost("identity-merge", payload);
    await Promise.all([loadIdentities(), load()]);
    resetIdentityEditor();
    const subject = result?.source_kind === "account" ? "账号" : "身份";
    toast(result?.state_merged
      ? `${subject}及已有关系已合并`
      : `${subject}已合并；没有发现需迁移的独立关系状态`);
  } catch (error) {
    toast(`合并失败：${error?.message || String(error)}`, true);
    button.disabled = false;
    button.dataset.confirmed = "";
    button.textContent = source.type === "account" ? "合并账号" : "合并身份";
  }
}

async function deleteIdentity(personId, button) {
  const person = identities.find((item) => item.person_id === personId);
  if (!person) return;
  button.disabled = true;
  button.textContent = "删除中…";
  clearDeleteConfirmation(false);
  try {
    await apiPost("identity-delete", { person_id: personId });
    await Promise.all([loadIdentities(), load()]);
    resetIdentityEditor();
    toast("账号归属已删除；关系和记忆原始数据仍保留");
  } catch (error) {
    toast(`删除失败：${error?.message || String(error)}`, true);
    renderIdentityList();
  }
}

function initIdentityEditor() {
  resetIdentityEditor();
  $("#btn-new-person").addEventListener("click", () => resetIdentityEditor());
  $("#btn-cancel-person").addEventListener("click", () => resetIdentityEditor());
  $("#btn-add-account").addEventListener("click", () => {
    $("#account-list").insertAdjacentHTML("beforeend", accountRow());
  });
  $("#btn-save-person").addEventListener("click", saveIdentity);
  $("#btn-merge-identity").addEventListener("click", mergeIdentity);
  $("#identity-merge-target").addEventListener("change", resetIdentityMergeConfirmation);
  $("#account-list").addEventListener("click", (event) => {
    const button = event.target.closest(".remove-account");
    if (button) button.closest(".account-row")?.remove();
  });
  $("#identity-list").addEventListener("click", (event) => {
    const item = event.target.closest("[data-person-id]");
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (!item || !action) return;
    const person = identities.find((value) => value.person_id === item.dataset.personId);
    if (action === "edit" && person) {
      clearDeleteConfirmation();
      editIdentity(person);
    }
    if (action === "merge" && person) {
      clearDeleteConfirmation();
      editIdentity(person);
      showIdentityMerge({ type: "person", source_person_id: person.person_id });
      document.querySelector(".identity-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (action === "delete") armDeleteIdentity(item.dataset.personId);
    if (action === "confirm-delete") deleteIdentity(item.dataset.personId, event.target.closest("button"));
  });
}

function initTabs() {
  document.querySelectorAll(".tabs button[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

async function init() {
  bridge = await resolveBridge();
  if (typeof bridge.ready === "function") await bridge.ready();
  if (!bridge || typeof bridge.apiGet !== "function") {
    throw new Error("AstrBot 页面通信接口不可用");
  }
  initTabs();
  initIdentityEditor();
  $("#btn-refresh").addEventListener("click", load);
  $("#btn-save-config").addEventListener("click", saveConfig);
  $("#btn-reset-config").addEventListener("click", resetConfigForm);
  $("#relation-tbody").addEventListener("click", (event) => {
    const button = event.target.closest("[data-quick-edit]");
    if (button) quickEditRelationship(Number(button.dataset.quickEdit));
  });
  await load();
  await loadConfig();
  await loadIdentities();
}

init().catch((error) => {
  toast(`页面启动失败：${error?.message || String(error)}`, true);
  $("#relation-tbody").innerHTML = '<tr class="empty-row"><td colspan="11">页面启动失败</td></tr>';
});
