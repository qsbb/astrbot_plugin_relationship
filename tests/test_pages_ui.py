"""凝心溯溪-情 页面 UI 静态检查。

验证 pages/manager/ 下的 HTML、JS、CSS 文件包含设置 tab 所需的结构和交互逻辑，
不依赖 AstrBot 运行时，可离线运行：
    python -m pytest -q tests/test_pages_ui.py
"""

from __future__ import annotations

import unittest
from pathlib import Path

PAGES_DIR = Path(__file__).resolve().parents[1] / "pages" / "manager"


class PagesUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")
        self.js = (PAGES_DIR / "app.js").read_text(encoding="utf-8")
        self.css = (PAGES_DIR / "style.css").read_text(encoding="utf-8")

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
        self.assertIn("Promise.all([loadIdentities(), load()])", self.js)

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
        self.assertIn('colspan="11"', self.html)
        self.assertIn('colspan="11"', self.js)

    def test_overview_has_quick_identity_editor(self) -> None:
        self.assertIn("<th>操作</th>", self.html)
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

    def test_account_memory_profile_is_editable(self) -> None:
        self.assertIn('data-account="memory_profile_id"', self.js)
        self.assertIn("记忆人格 ID", self.js)
        self.assertNotIn("<label>关系人格 ID", self.js)

    def test_partial_initial_prior_failure_is_reported(self) -> None:
        self.assertIn("initial_prior?.requested", self.js)
        self.assertIn("账号归属已保存，但初始关系未应用", self.js)
        self.assertIn("该关系已有互动，已保留现有关系", self.js)
        self.assertIn("该账号已有互动，将保留现有关系", self.js)

    def test_page_assets_have_cache_stamp(self) -> None:
        self.assertIn("style.css?v=0.6.4", self.html)
        self.assertIn("app.js?v=0.6.4", self.html)
        self.assertIn("rev=identity-merge-2", self.html)

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
        self.assertIn("button.primary", self.css)

    def test_css_has_identity_editor_styles(self) -> None:
        self.assertIn(".identity-grid", self.css)
        self.assertIn(".account-row", self.css)
        self.assertIn(".identity-item", self.css)
        self.assertIn(".quick-edit-command", self.css)


if __name__ == "__main__":
    unittest.main()
