"""凝心溯溪-情 页面 UI 静态检查。

验证 pages/manager/ 下的 HTML、JS、CSS 文件包含设置 tab 所需的结构和交互逻辑，
不依赖 AstrBot 运行时，可离线运行：
    python -m pytest -q tests/test_pages_ui.py
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

PAGES_DIR = Path(__file__).resolve().parents[1] / "pages" / "manager"


class PagesUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")
        self.js = (PAGES_DIR / "app.js").read_text(encoding="utf-8")
        self.css = (PAGES_DIR / "style.css").read_text(encoding="utf-8")
        self.series_css = (PAGES_DIR / "series-ui.css").read_text(encoding="utf-8")
        self.schema = json.loads(
            (PAGES_DIR.parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
        )

    def test_html_has_settings_tab(self) -> None:
        self.assertIn('data-tab="settings"', self.html)
        self.assertIn('data-panel="settings"', self.html)

    def test_html_has_identity_binding_tab(self) -> None:
        self.assertIn('data-tab="identities"', self.html)
        self.assertIn('data-panel="identities"', self.html)
        self.assertIn('id="identity-list"', self.html)
        self.assertIn('class="card identity-editor"', self.html)
        self.assertIn('id="account-list"', self.html)

    def test_html_has_config_form_container(self) -> None:
        self.assertIn('id="config-form"', self.html)
        self.assertIn('id="btn-save-config"', self.html)
        self.assertIn('id="btn-reset-config"', self.html)

    def test_js_has_config_load_and_save(self) -> None:
        self.assertIn("async function loadConfig()", self.js)
        self.assertIn("async function saveConfig()", self.js)
        self.assertIn("function renderConfigForm(", self.js)
        self.assertIn("function collectConfigChanges()", self.js)

    def test_js_has_api_post(self) -> None:
        self.assertIn("async function apiPost(", self.js)

    def test_js_has_identity_crud(self) -> None:
        self.assertIn("async function loadIdentities()", self.js)
        self.assertIn("async function saveIdentity()", self.js)
        self.assertIn("async function deleteIdentity(", self.js)
        self.assertIn("function collectIdentity()", self.js)

    def test_identity_merge_supports_account_person_and_orphan_sources(self) -> None:
        self.assertIn('id="identity-merge-panel"', self.html)
        self.assertIn('id="identity-merge-target"', self.html)
        self.assertIn('id="btn-merge-identity"', self.html)
        self.assertIn("async function mergeIdentity()", self.js)
        self.assertIn('apiPost("identity-merge"', self.js)
        self.assertIn("identityMergeConfirmTimer", self.js)
        self.assertIn("RELATIONSHIP_STORAGE_READ_ONLY", self.js)
        self.assertIn("请在 8 秒内再次点击", self.js)
        self.assertIn('type: "account"', self.js)
        self.assertIn('type: "person"', self.js)
        self.assertIn('type: "orphan"', self.js)

    def test_delete_uses_inline_confirmation_and_refreshes_overview(self) -> None:
        self.assertNotIn("window.confirm", self.js)
        self.assertIn("function armDeleteIdentity(", self.js)
        self.assertIn('data-action="${pending ? "confirm-delete" : "delete"}"', self.js)
        self.assertIn('apiPost("identity-delete"', self.js)
        self.assertIn("restore_account:", self.js)
        self.assertIn("现有关系迁回到", self.js)
        self.assertIn("解除归属", self.js)
        self.assertIn("Promise.all([loadIdentities(), load()])", self.js)

    def test_relationship_delete_is_separate_from_whitelist(self) -> None:
        self.assertIn('apiPost("relationship-delete"', self.js)
        self.assertIn("确认删除关系", self.js)
        self.assertIn("白名单设置保持不变", self.js)
        self.assertNotIn('apiPost("config", { AFFINITY_WHITELIST_USER_IDS', self.js)

    def test_multi_profile_relationship_delete_requires_one_explicit_profile(self) -> None:
        self.assertIn("function relationshipDeleteProfiles(", self.js)
        self.assertIn("function relationshipDeleteProfilePicker(", self.js)
        self.assertIn("data-delete-relationship-profile", self.js)
        self.assertIn('<option value="" disabled', self.js)
        self.assertIn("请选择要删除的关系人格", self.js)
        self.assertIn("!profiles.includes(selectedProfile)", self.js)
        self.assertIn("relationship_profile_ids: [selectedProfile]", self.js)
        self.assertNotIn("relationship_profile_ids: profiles", self.js)
        self.assertIn("button.disabled = true", self.js)
        self.assertIn("deleteButton.disabled = !picker.value", self.js)
        self.assertIn("const awaitingProfile = deletePending && multipleProfiles", self.js)
        self.assertIn('data-awaiting-profile="${awaitingProfile ? "true" : "false"}"', self.js)
        self.assertIn('[data-awaiting-profile="true"]:disabled', self.css)
        self.assertIn("本次只删除所选人格", self.js)
        for forbidden_copy in ("删除全部关系", "删除所有关系人格", "一次删除全部"):
            self.assertNotIn(forbidden_copy, self.js)

    def test_single_profile_relationship_delete_keeps_simple_confirmation(self) -> None:
        self.assertIn("const multipleProfiles = profiles.length > 1;", self.js)
        self.assertIn('multipleProfiles ? "确认删除所选人格" : "确认删除关系"', self.js)
        self.assertIn(": profiles[0];", self.js)

    def test_relationship_delete_clears_confirmation_before_request(self) -> None:
        start = self.js.index("async function deleteRelationship(")
        request = self.js.index('await apiPost("relationship-delete"', start)
        before_request = self.js[start:request]
        self.assertIn("clearRelationshipDeleteConfirmation();", before_request)
        self.assertIn('button.textContent = "删除中…";', before_request)

    def test_relationship_delete_cancel_and_timeout_clear_profile_picker(self) -> None:
        self.assertIn("data-cancel-delete-relationship", self.js)
        self.assertIn("function expireRelationshipDeleteConfirmation()", self.js)
        self.assertIn("setTimeout(expireRelationshipDeleteConfirmation, 8000)", self.js)
        self.assertIn(
            'querySelectorAll("[data-relationship-delete-confirmation]")', self.js
        )
        self.assertIn(
            'if (target !== "overview") clearRelationshipDeleteConfirmation();',
            self.js,
        )
        self.assertIn("pendingRelationshipDeleteProfileId = picker.value", self.js)
        self.assertIn(".relationship-delete-confirmation", self.css)

    def test_js_has_tab_switching(self) -> None:
        self.assertIn("function initTabs()", self.js)
        self.assertIn("data-tab", self.js)
        self.assertIn("data-panel", self.js)

    def test_js_has_config_groups(self) -> None:
        self.assertIn("CONFIG_GROUPS", self.js)
        self.assertIn("MOOD_", self.js)
        self.assertIn("AFFINITY_", self.js)
        self.assertIn("TRUST_", self.js)
        self.assertIn("FAMILIARITY_", self.js)
        self.assertIn("AFFECT_", self.js)
        self.assertIn("DYNAMICS_", self.js)
        self.assertIn("RELATIONSHIP_", self.js)
        self.assertIn("PROMPT_", self.js)
        self.assertIn("CROSS_PLATFORM_MEMORY_", self.js)

    def test_string_config_is_rendered_as_text(self) -> None:
        self.assertIn('field.type === "string"', self.js)
        self.assertIn('<input type="text" class="config-text', self.js)

    def test_overview_shows_relationship_profile(self) -> None:
        self.assertIn("关系人格", self.html)
        self.assertIn("user.relationship_profile_id", self.js)
        self.assertIn("user.relationship_profile_ids", self.js)
        self.assertIn('class="profile-stack"', self.js)
        self.assertIn('colspan="5"', self.html)
        self.assertIn('colspan="5"', self.js)

    def test_overview_has_relationship_type_editor(self) -> None:
        """关系性质列：下拉框编辑 + relationship-type API。"""
        self.assertIn("关系性质", self.html)
        self.assertIn('data-set-type="${index}"', self.js)
        self.assertIn("async function saveRelationshipType(", self.js)
        self.assertIn('apiPost("relationship-type", payload)', self.js)
        self.assertIn("relationship_type", self.js)
        self.assertIn('["lover", "情侣"]', self.js)
        self.assertIn('["exclusive", "专属联结"]', self.js)
        self.assertIn('["family", "家人"]', self.js)
        self.assertIn('["teammate", "队友"]', self.js)
        self.assertIn('["rival", "对手"]', self.js)
        self.assertIn('["friend", "朋友"]', self.js)
        self.assertIn('["close_friend", "挚友"]', self.js)

    def test_overview_table_has_matching_columns(self) -> None:
        """方案 A：主行 5 列，colgroup 与 thead 一致。"""
        self.assertIn('class="relation-col-type"', self.html)
        for column in ("relation-col-user", "relation-col-type", "relation-col-band",
                       "relation-col-time", "relation-col-state"):
            self.assertIn(f'class="{column}"', self.html)
        for header in ("自然人", "关系性质", "层级", "最近互动", "边界状态"):
            self.assertIn(f'<th scope="col">{header}</th>', self.html)
        self.assertEqual(self.html.count("<col class=\"relation-col-"), 5)
        self.assertEqual(self.html.count('<th scope="col">'), 5)
        self.assertIn('colspan="5"', self.html)
        self.assertIn('colspan="5"', self.js)

    def test_details_main_row_has_five_labeled_cells(self) -> None:
        """主行只放决策必需信息：5 列 data-label 与表头一一对应。"""
        for label in ("自然人", "关系性质", "层级", "最近互动", "边界状态"):
            self.assertIn(f'data-label="{label}"', self.js)
        self.assertIn("function relationshipMainRow(", self.js)
        self.assertIn("function relationshipDetailMarkup(", self.js)

    def test_details_rows_expand_secondary_fields(self) -> None:
        """点击主行/详情按钮展开次要字段，删除关系收进展开区。"""
        self.assertIn("const expandedRelationshipKeys = new Set();", self.js)
        self.assertIn("function toggleRelationshipDetail(", self.js)
        self.assertIn('data-relation-toggle="${index}"', self.js)
        self.assertIn('aria-expanded="${expanded ? "true" : "false"}"', self.js)
        self.assertIn("expandedRelationshipKeys.has(key)", self.js)
        self.assertIn("人格", self.js)
        self.assertIn("互动", self.js)
        self.assertIn("白名单", self.js)
        self.assertIn("范围", self.js)
        self.assertIn("toggleRelationshipDetail(Number(dataRow.dataset.relationIndex))", self.js)
        self.assertIn('event.target.closest("button, select, input, textarea, a, label, [data-copy-id]")', self.js)

    def test_details_filters_cover_every_dimension(self) -> None:
        """搜索 + 层级 / 关系性质 / 人格 / 白名单 / 边界筛选。"""
        for control in ("relation-search", "relation-band-filter", "relation-type-filter",
                        "relation-profile-filter", "relation-whitelist-filter",
                        "relation-boundary-filter", "relation-sort"):
            self.assertIn(f'id="{control}"', self.html)
        self.assertIn('id="btn-relation-reset"', self.html)
        for state in ("relationshipTypeFilter", "relationshipProfileFilter",
                      "relationshipWhitelistFilter", "relationshipBoundaryFilter"):
            self.assertIn(f"let {state} = \"\";", self.js)
        self.assertIn("function relationshipMatchesFilters(user, query)", self.js)
        self.assertIn("function populateRelationshipFilters()", self.js)
        self.assertIn('relationshipWhitelistFilter === "no"', self.js)
        self.assertIn("relationshipBoundaryFilter && user.boundary !== relationshipBoundaryFilter", self.js)
        self.assertIn("relationshipDeleteProfiles(user).includes(relationshipProfileFilter)", self.js)

    def test_details_progressive_display_matches_viewport(self) -> None:
        """桌面 20 条、移动端 10 条渐进显示，刷新重置分页。"""
        self.assertIn("function relationshipPageSize()", self.js)
        self.assertIn('window.matchMedia("(max-width: 760px)").matches ? 10 : 20', self.js)
        self.assertIn("relationshipVisible = relationshipPageSize();", self.js)
        self.assertIn("relationshipVisible += relationshipPageSize();", self.js)
        self.assertIn('data-relation-more', self.js)
        self.assertIn('data-relation-collapse', self.js)

    def test_overview_has_quick_identity_editor(self) -> None:
        self.assertIn("边界状态", self.html)
        self.assertIn('data-quick-edit="${index}"', self.js)
        self.assertIn("async function quickEditRelationship(", self.js)
        self.assertIn('activateTab("identities")', self.js)
        self.assertIn("请先私聊 Bot 一次后刷新", self.js)

    def test_identity_editor_has_profile_and_initial_prior(self) -> None:
        self.assertIn('id="relationship-profile-id"', self.html)
        self.assertIn('id="initial-prior"', self.html)
        self.assertIn('value="neutral"', self.html)
        self.assertIn('value="acquainted"', self.html)
        self.assertIn('value="fond"', self.html)
        self.assertIn("default_relationship_profile", self.js)
        self.assertIn("relationship_profiles", self.js)
        self.assertIn("relationship_profile_id:", self.js)
        self.assertIn("initial_prior:", self.js)
        self.assertIn('$("#initial-prior").disabled ? ""', self.js)
        self.assertIn("whitelisted_relationship_profiles", self.js)
        self.assertIn("白名单关系可在已有互动后设置或调整固定档位", self.js)
        self.assertIn("initial_prior_by_profile", self.js)

    def test_account_memory_profile_is_editable(self) -> None:
        self.assertIn('data-account="memory_profile_id"', self.js)
        self.assertIn("记忆人格", self.js)
        self.assertIn('data-account="memory_profile_id"', self.js)
        self.assertNotIn("<label>关系人格 ID", self.js)

    def test_partial_initial_prior_failure_is_reported(self) -> None:
        self.assertIn("initial_prior?.requested", self.js)
        self.assertIn("账号归属已保存，但初始关系未应用", self.js)
        self.assertIn("该关系已有互动，已保留现有关系", self.js)
        self.assertIn("该账号已有互动，将保留现有关系", self.js)
        self.assertIn("只有白名单关系可以调整", self.js)

    def test_page_assets_have_cache_stamp(self) -> None:
        self.assertIn("style.css?v=0.12.5-1", self.html)
        self.assertIn("series-ui.css?v=0.12.5-1", self.html)
        self.assertIn("series-ui.js?v=0.12.5-1", self.html)
        self.assertIn("app.js?v=0.12.5-1", self.html)

    def test_legacy_profile_change_reports_restart_requirement(self) -> None:
        self.assertIn("data.restart_required", self.js)
        self.assertIn("旧数据归属需重启后生效", self.js)

    def test_config_collector_only_submits_changed_non_boolean_values(self) -> None:
        self.assertIn("Object.is(value, configValues[key])", self.js)

    def test_css_has_config_form_styles(self) -> None:
        self.assertIn(".config-form", self.css)
        self.assertIn(".config-field", self.css)
        self.assertIn(".config-group", self.css)
        self.assertIn(".config-hint", self.css)
        self.assertIn("button.primary", self.series_css)

    def test_settings_use_chinese_names_and_plain_hints(self) -> None:
        self.assertEqual(len(self.schema), 53)
        for key, field in self.schema.items():
            self.assertTrue(field.get("description"), key)
            self.assertTrue(field.get("hint"), key)
            self.assertNotEqual(field["description"], key)
        self.assertIn("field.description || key", self.js)
        self.assertIn("field.hint ? escapeHtml(field.hint)", self.js)
        self.assertNotIn("${escapeHtml(key)}</label>", self.js)
        self.assertIn('data-key="${key}"', self.js)

    def test_css_has_identity_editor_styles(self) -> None:
        self.assertIn(".identity-grid", self.css)
        self.assertIn(".account-row", self.css)
        self.assertIn(".identity-item", self.css)
        self.assertIn(".quick-edit-command", self.css)
        self.assertIn(".unbind-confirmation", self.css)
        self.assertIn(".row-actions", self.css)
        self.assertIn("table-layout: fixed", self.css)
        self.assertIn("content: attr(data-label)", self.css)
        self.assertNotIn("overflow-x: scroll", self.css)
        self.assertNotIn("min-width: 1010px", self.css)
        self.assertIn('data-label="最近互动"', self.js)
        self.assertIn("relationship-detail-row", self.js)

    def test_init_loads_sections_in_parallel(self) -> None:
        self.assertIn(
            "await Promise.allSettled([load(), loadConfig(), loadIdentities()])",
            self.js,
        )
        self.assertNotIn("await load();\n  await loadConfig();", self.js)

    def test_init_failure_marks_all_panels(self) -> None:
        self.assertIn("页面启动失败，无法加载配置", self.js)
        self.assertIn("页面启动失败，无法加载账号归属", self.js)

    def test_tabs_expose_aria_selected_state(self) -> None:
        self.assertIn('role="tablist"', self.html)
        self.assertIn('role="tabpanel"', self.html)
        self.assertIn('aria-selected="true"', self.html)
        self.assertIn('aria-selected="false"', self.html)
        self.assertIn('button.setAttribute("aria-selected"', self.js)

    def test_tabs_have_panel_links_keyboard_roving_focus_and_timeout(self) -> None:
        self.assertIn('aria-controls="panel-overview"', self.html)
        self.assertIn('aria-labelledby="tab-overview"', self.html)
        self.assertIn("button.tabIndex = active ? 0 : -1", self.js)
        self.assertIn('event.key === "ArrowLeft"', self.js)
        self.assertIn('event.key === "ArrowRight"', self.js)
        self.assertIn('panel.setAttribute("aria-hidden", String(!active))', self.js)
        self.assertIn("页面通信初始化超时，可点击刷新重试", self.js)
        self.assertIn("@media (hover: hover) and (pointer: fine)", self.css)
        self.assertIn("body[data-series-ui] button:active", self.series_css)
        self.assertIn("transform: translateY(0)", self.series_css)
        self.assertIn("clearTimeout(timer);", self.js)

    def test_table_headers_have_scope(self) -> None:
        self.assertIn('<th scope="col">自然人</th>', self.html)
        self.assertNotIn("<th>自然人</th>", self.html)

    def test_overview_shows_relation_count(self) -> None:
        self.assertIn('id="relation-count"', self.html)
        self.assertIn('$("#relation-count")', self.js)
        self.assertIn("共 ${filtered.length} 条", self.js)

    def test_invalid_numeric_config_is_not_submitted(self) -> None:
        self.assertIn("Number.isNaN(value)", self.js)
        self.assertIn("invalid.push(field.description || key)", self.js)
        self.assertIn("以下配置不是有效数字，未保存：", self.js)
        self.assertIn("有效配置已保存；以下数字项无效，未提交：", self.js)

    def test_error_feedback_uses_shared_toast(self) -> None:
        self.assertIn(
            'window.SeriesUI.toast(message, error ? "error" : "info")', self.js
        )
        self.assertNotIn("function toast(", self.js)

    def test_busy_buttons_restore_labels(self) -> None:
        self.assertIn('button.textContent = "保存中…";', self.js)
        self.assertIn('button.textContent = "刷新中…";', self.js)
        self.assertIn('button.textContent = "保存";', self.js)
        self.assertIn('button.textContent = "刷新";', self.js)
        self.assertIn("const originalLabel = button.textContent;", self.js)

    def test_editor_scrolling_respects_reduced_motion(self) -> None:
        self.assertIn("function scrollToIdentityEditor()", self.js)
        self.assertIn('"(prefers-reduced-motion: reduce)"', self.js)
        self.assertNotIn('behavior: "smooth", block: "start" });', self.js)

    def test_css_uses_series_color_tokens(self) -> None:
        for token in (
            "--si-primary:",
            "--si-success:",
            "--si-warning:",
            "--si-danger:",
            "--si-muted:",
            "--si-line-solid:",
            "--si-radius-sm:",
            "--si-shadow:",
        ):
            self.assertIn(token, self.series_css)
        self.assertIn("var(--si-", self.css)
        self.assertNotIn("background: #102236;", self.css)
        self.assertNotIn("background: #0b1826;", self.css)
        self.assertNotIn("linear-gradient(145deg", self.css)
        self.assertNotIn("radial-gradient(circle at top right", self.css)

    def test_css_has_keyboard_focus_and_shared_toast(self) -> None:
        self.assertIn("button:focus-visible", self.series_css)
        self.assertIn("box-shadow: var(--si-focus)", self.series_css)
        self.assertNotIn("#toast", self.css)
        self.assertIn('window.SeriesUI.toast(message, error ? "error" : "info")', self.js)


    def test_relationship_profile_title_uses_defined_escape_helper(self) -> None:
        self.assertNotIn("escapeHtmlAttr", self.js)
        self.assertIn('title="${escapeHtml(profileId)}"', self.js)


    def test_identity_tab_uses_master_detail_and_mobile_sheet(self) -> None:
        """账号归属：桌面列表 + 编辑器同屏，移动端编辑器收进全屏 sheet。"""
        self.assertIn(".identity-grid", self.css)
        self.assertIn("@media (min-width: 901px)", self.css)
        self.assertIn("max-height: min(54vh, 640px)", self.css)
        self.assertIn("overflow-y: auto", self.css)
        self.assertIn("#panel-identities .identity-grid > .identity-editor { display: none; }", self.css)
        self.assertIn(".identity-editor-dialog", self.css)
        self.assertIn("position: fixed;", self.css)
        self.assertIn('className: "identity-editor-dialog"', self.js)
        self.assertIn("if (!identitySheetMedia() || identityEditorDialog?.isOpen()) return false;", self.js)
        self.assertIn("if (!openIdentityEditorSheet()) scrollToIdentityEditor();", self.js)

    def test_settings_use_collapsible_groups_and_sticky_save_bar(self) -> None:
        """设置：分组折叠 + sticky 保存栏 + 未保存数量提示。"""
        self.assertIn('id="btn-config-fold"', self.html)
        self.assertIn('class="settings-sticky"', self.html)
        self.assertIn('id="config-dirty-count"', self.html)
        self.assertIn('id="btn-save-config"', self.html)
        self.assertIn("function configGroupMarkup(", self.js)
        self.assertIn("const openConfigGroups = new Set();", self.js)
        self.assertIn("function setAllConfigGroups(", self.js)
        self.assertIn("function updateConfigFoldButton(", self.js)
        self.assertIn("config-group-count", self.js)
        self.assertIn("openConfigGroups.has(title) ? \" open\" : \"\"", self.js)
        self.assertIn("#panel-settings .settings-sticky", self.css)
        self.assertIn("position: sticky;", self.css)
        self.assertIn(".config-group-title::before", self.css)
        self.assertIn(".config-group-body", self.css)

    def test_details_density_is_relaxed(self) -> None:
        """密度：主行 5 列、字号层级拉开，副信息收进展开区。"""
        self.assertIn(".relation-person strong", self.css)
        self.assertIn("font-size: 15px;", self.css)
        self.assertIn("font-size: 12.5px;", self.css)
        self.assertIn("padding: 15px 14px;", self.css)
        self.assertIn(".relation-detail", self.css)
        self.assertIn(".detail-chip", self.css)
        self.assertIn("relationshipVisible = relationshipPageSize();", self.js)

    def test_multi_profile_delete_confirmation_uses_full_width_row(self) -> None:
        # 多人格删除：确认区整行渲染在展开详情里，选择人格前确认按钮保持禁用
        self.assertNotIn('button.closest(".row-actions")?.insertAdjacentHTML', self.js)
        self.assertIn('const picker = deletePending && multipleProfiles', self.js)
        self.assertIn('relationshipDeleteProfilePicker(profiles, pendingRelationshipDeleteProfileId)', self.js)
        self.assertIn('<tr class="relationship-detail-row"><td colspan="5">', self.js)
        self.assertIn(".relationship-delete-confirmation", self.css)
        self.assertIn("flex: 1 1 100%", self.css)


if __name__ == "__main__":
    unittest.main()


def test_relationship_profiles_use_readable_short_labels():
    js = (PAGES_DIR / "app.js").read_text(encoding="utf-8")
    assert '"默认人格"' in js
    assert "自动 · ${profileId.slice(-4)}" in js
    assert "自定义人格 · ${String(profileId).slice(-4)}" in js
    assert '自动 · ${profileId.slice(-4)}' in js or '自动 · ' in js


def test_identity_editor_folds_advanced_fields():
    html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")
    js = (PAGES_DIR / "app.js").read_text(encoding="utf-8")
    css = (PAGES_DIR / "style.css").read_text(encoding="utf-8")

    assert 'id="identity-advanced"' in html
    assert 'class="si-disclosure identity-advanced"' in html
    advanced = html.split('id="identity-advanced"', 1)[1].split("</details>", 1)[0]
    for field in ("person-id", "relationship-profile-id", "initial-prior"):
        assert f'id="{field}"' in advanced
    assert 'id="person-display-name"' not in advanced  # 常用字段不折叠
    assert 'open = true;' in js
    assert ".identity-advanced-body" in css


def test_identity_tab_supports_search_and_copyable_ids():
    html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")
    js = (PAGES_DIR / "app.js").read_text(encoding="utf-8")
    css = (PAGES_DIR / "style.css").read_text(encoding="utf-8")

    assert 'id="identity-search"' in html
    assert "function filteredIdentities()" in js
    assert "let identityQuery = \"\";" in js
    assert "const filtered = filteredIdentities();" in js
    assert "没有匹配的自然人" in js
    assert 'account.session_id' in js
    assert 'account.memory_profile_id' in js
    assert '$("#identity-search")?.addEventListener("input"' in js
    assert 'idChip(person.person_id, "自然人 ID")' in js
    assert ".identity-search" in css
    assert ".identity-id-line" in css


def test_details_tab_filters_sorts_and_copies_long_ids():
    html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")
    js = (PAGES_DIR / "app.js").read_text(encoding="utf-8")
    css = (PAGES_DIR / "style.css").read_text(encoding="utf-8")

    # 关系明细：层级筛选 + 排序
    assert 'id="relation-band-filter"' in html
    assert 'id="relation-sort"' in html
    for value in ("affinity", "trust", "familiarity", "interaction", "recent"):
        assert f'value="{value}"' in html
    assert "function populateBandFilter()" in js
    assert "function sortRelationshipRows(rows, sort)" in js
    assert "relationshipBandFilter" in js and "relationshipSort" in js

    # 图表点击跳转 + 自动带筛选
    assert 'class="band-row" data-band=' in js
    assert 'activateTab("details");' in js
    assert 'relationshipBandFilter = row.dataset.band || "";' in js

    # 长 ID 短显示 + 复制
    assert "function shortId(value, head = 10, tail = 6)" in js
    assert "function idChip(value, label = \"标识\")" in js
    assert 'data-copy-id=' in js
    assert "window.SeriesUI?.copy ? await window.SeriesUI.copy(value) : false" in js
    assert ".id-chip" in css

    # 自动刷新 + 数据新鲜度
    assert 'id="auto-refresh"' in html
    assert 'id="relation-freshness"' in html
    assert "function updateFreshness(failed = false)" in js
    assert "function setAutoRefresh(enabled)" in js
    assert "const AUTO_REFRESH_MS = 60000;" in js
    assert ".freshness.is-stale" in css
