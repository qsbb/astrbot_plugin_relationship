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


if __name__ == "__main__":
    unittest.main()
