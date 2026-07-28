"""凝心溯溪-情 入口级测试。

使用 mock 的 astrbot 运行时模块测试 main.py 的插件入口：
- /rel status 与 /rel reset 命令解析；
- on_llm_request 钩子（普通消息与命令消息）；
- terminate() 生命周期落盘与实例释放；
- _page_overview 只读总览。

不依赖真实 AstrBot 运行时，可离线运行：
    python -m pytest -q tests/test_main_entry.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_PARENT = _PLUGIN_ROOT.parent
if str(_PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_PARENT))

_DATA_DIR: dict[str, str] = {"path": tempfile.mkdtemp()}


def _install_fake_astrbot() -> None:
    """在导入 main 之前注入最小可用的 astrbot 运行时替身。"""
    import logging

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api_event = types.ModuleType("astrbot.api.event")
    api_star = types.ModuleType("astrbot.api.star")
    api_web = types.ModuleType("astrbot.api.web")  # 故意不含 json_response

    api.logger = logging.getLogger("fake_astrbot")

    class AstrMessageEvent:  # noqa: D401 - 测试替身
        """事件基类替身。"""

    class _CommandGroup:
        def __init__(self, func):
            self._func = func

        def command(self, _name: str):
            def deco(fn):
                return fn

            return deco

        def __call__(self, *args, **kwargs):
            return self._func(*args, **kwargs)

    class _Filter:
        @staticmethod
        def on_llm_request(priority: int = 0):
            del priority

            def deco(fn):
                return fn

            return deco

        @staticmethod
        def on_llm_response(priority: int = 0):
            del priority

            def deco(fn):
                return fn

            return deco

        @staticmethod
        def command_group(_name: str):
            def deco(fn):
                return _CommandGroup(fn)

            return deco

    api_event.AstrMessageEvent = AstrMessageEvent
    api_event.filter = _Filter()

    class Star:
        def __init__(self, context) -> None:
            self.context = context

    class StarTools:
        @staticmethod
        def get_data_dir(_name: str) -> str:
            return _DATA_DIR["path"]

    def register(*_args, **_kwargs):
        def deco(cls):
            return cls

        return deco

    api_star.Context = object
    api_star.Star = Star
    api_star.StarTools = StarTools
    api_star.register = register

    astrbot.api = api
    api.event = api_event
    api.star = api_star
    api.web = api_web

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = api_event
    sys.modules["astrbot.api.star"] = api_star
    sys.modules["astrbot.api.web"] = api_web


_install_fake_astrbot()

main = importlib.import_module("astrbot_plugin_relationship.main")


class FakeEvent:
    """AstrMessageEvent 替身：仅提供入口用到的取值方法。"""

    def __init__(
        self,
        text: str = "你好",
        bot_id: str = "bot-1",
        sender_id: str = "user-1",
        group_id: str | None = None,
        message_id: str = "msg-1",
    ) -> None:
        self._text = text
        self._bot_id = bot_id
        self._sender_id = sender_id
        self._group_id = group_id
        self._message_id = message_id

    def get_self_id(self) -> str:
        return self._bot_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_group_id(self) -> str | None:
        return self._group_id

    def get_message_str(self) -> str:
        return self._text

    def get_message_id(self) -> str:
        return self._message_id

    @staticmethod
    def plain_result(text: str) -> str:
        return f"[plain]{text}"


class FakeContext:
    """Context 替身：记录 register_web_api 调用。"""

    def __init__(self) -> None:
        self.web_api_calls: list[tuple] = []

    def register_web_api(self, route, handler, methods, description) -> None:
        self.web_api_calls.append((route, handler, methods, description))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _collect(agen) -> list:
    return [item async for item in agen]


class MainEntryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        _DATA_DIR["path"] = self._tmp.name
        self.context = FakeContext()
        self.plugin = main.RelationshipPlugin(
            self.context, {"SAVE_INTERVAL_SECONDS": 0}
        )

    def tearDown(self) -> None:
        if main.RelationshipPlugin._current_instance is self.plugin:
            main.RelationshipPlugin._current_instance = None
        self._tmp.cleanup()

    # -- 初始化 --------------------------------------------------------

    def test_init_registers_web_api_and_instance(self) -> None:
        self.assertIs(main.RelationshipPlugin._current_instance, self.plugin)
        routes = [call[0] for call in self.context.web_api_calls]
        self.assertIn(f"/{main.PLUGIN_NAME}/overview", routes)
        self.assertIn(f"/{main.PLUGIN_NAME}/config", routes)

    # -- on_llm_request 钩子 -------------------------------------------

    def test_on_llm_request_records_message_without_touching_req(self) -> None:
        req = object()
        _run(self.plugin.on_llm_request(FakeEvent(text="今天聊聊"), req))
        snapshot = _run(self.plugin.manager.get_snapshot("bot-1", "user-1", None))
        state = self.plugin.manager._states.get("bot-1:user:user-1")
        self.assertIsNotNone(state)
        self.assertEqual(state.interaction_count, 1)
        self.assertGreaterEqual(snapshot.familiarity, 0)
        # req 是普通 object：入口没有为它新增任何属性即未被修改。
        self.assertEqual(vars(req) if hasattr(req, "__dict__") else {}, {})

    def test_on_llm_request_command_text_is_readonly(self) -> None:
        _run(self.plugin.on_llm_request(FakeEvent(text="/rel status"), object()))
        self.assertNotIn("bot-1:user:user-1", self.plugin.manager._states)

    def test_on_llm_request_missing_identity_is_noop(self) -> None:
        event = FakeEvent(text="hello", bot_id="", sender_id="")
        _run(self.plugin.on_llm_request(event, object()))
        self.assertEqual(self.plugin.manager._states, {})

    # -- /rel 命令 -----------------------------------------------------

    def test_rel_status_yields_snapshot_text(self) -> None:
        _run(self.plugin.on_llm_request(FakeEvent(text="你好呀"), object()))
        results = _run(_collect(self.plugin.rel_status(FakeEvent())))
        self.assertEqual(len(results), 1)
        self.assertIn("好感", results[0])
        self.assertIn("信任", results[0])
        self.assertIn(main.__version__, results[0])

    def test_rel_status_rejects_unknown_identity(self) -> None:
        event = FakeEvent(bot_id="", sender_id="")
        results = _run(_collect(self.plugin.rel_status(event)))
        self.assertEqual(len(results), 1)
        self.assertIn("无法识别", results[0])

    def test_rel_reset_clears_state(self) -> None:
        _run(self.plugin.on_llm_request(FakeEvent(text="累积一次互动"), object()))
        self.assertIn("bot-1:user:user-1", self.plugin.manager._states)
        results = _run(_collect(self.plugin.rel_reset(FakeEvent())))
        self.assertEqual(len(results), 1)
        self.assertIn("已重置", results[0])
        self.assertNotIn("bot-1:user:user-1", self.plugin.manager._states)

    def test_rel_reset_rejects_unknown_identity(self) -> None:
        event = FakeEvent(bot_id="", sender_id="")
        results = _run(_collect(self.plugin.rel_reset(event)))
        self.assertEqual(len(results), 1)
        self.assertIn("无法识别", results[0])

    # -- terminate 生命周期 --------------------------------------------

    def test_terminate_flushes_state_and_releases_instance(self) -> None:
        _run(self.plugin.on_llm_request(FakeEvent(text="落盘前互动"), object()))
        _run(self.plugin.terminate())
        self.assertIsNone(main.RelationshipPlugin._current_instance)
        state_file = Path(self._tmp.name) / "relationship_state.json"
        self.assertTrue(state_file.exists())
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertIn("bot-1:user:user-1", payload.get("users", {}))

    # -- Plugin Page ---------------------------------------------------

    def test_page_overview_payload(self) -> None:
        _run(self.plugin.on_llm_request(FakeEvent(text="页面数据"), object()))
        payload = _run(self.plugin._page_overview())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["plugin"]["version"], main.__version__)
        self.assertEqual(payload["summary"]["user_count"], 1)
        self.assertEqual(payload["users"][0]["user_id"], "user-1")
        self.assertIn(payload["users"][0]["boundary"], ("开放", "谨慎"))

    # -- Plugin Page 配置 API ------------------------------------------

    def test_page_get_config_returns_schema_and_values(self) -> None:
        payload = _run(self.plugin._page_get_config())
        self.assertTrue(payload["success"])
        self.assertIn("MOOD_ENABLED", payload["config"])
        self.assertIn("MOOD_ENABLED", payload["schema"])
        self.assertEqual(payload["schema"]["MOOD_ENABLED"]["type"], "bool")

    def _save_config(self, body: dict) -> dict:
        async def fake_request_json():
            return body

        self.plugin._request_json = fake_request_json
        return _run(self.plugin._page_save_config())

    def test_page_save_config_persists_and_applies(self) -> None:
        payload = self._save_config({"MOOD_WINDOW_SECONDS": 600})
        self.assertTrue(payload["success"])
        self.assertEqual(payload["config"]["MOOD_WINDOW_SECONDS"], 600)
        self.assertEqual(self.plugin.manager._mood._window, 600)

    def test_page_save_config_rejects_unknown_field(self) -> None:
        payload = self._save_config({"UNKNOWN_KEY": 1})
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "VALIDATION_FAILED")
        self.assertIn("UNKNOWN_KEY", payload["fields"])

    def test_page_save_config_rejects_invalid_value(self) -> None:
        payload = self._save_config({"MOOD_WINDOW_SECONDS": "not-a-number"})
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "VALIDATION_FAILED")

    def test_config_override_persists_across_instances(self) -> None:
        self._save_config({"AFFINITY_MESSAGE_GAIN": 0.5})
        config_path = Path(self._tmp.name) / "relationship-config.json"
        self.assertTrue(config_path.exists())
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["AFFINITY_MESSAGE_GAIN"], 0.5)

    def test_config_hot_reload_updates_affinity_threshold(self) -> None:
        self._save_config({"AFFINITY_HIGH_THRESHOLD": 80.0})
        self.assertEqual(self.plugin._affinity_config.high_affinity_threshold, 80.0)
        self.assertEqual(
            self.plugin.manager._affinity.config.high_affinity_threshold, 80.0
        )


class FakeNativeConfig(dict):
    """AstrBot 插件配置对象替身：具备 save_config 即被视为可回写。"""

    def __init__(self, *args, writable: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.writable = writable
        self.save_calls = 0

    def save_config(self) -> None:
        if not self.writable:
            raise RuntimeError("配置页不可写")
        self.save_calls += 1


class ConfigBaselineTest(unittest.TestCase):
    """配置双源 baseline 回归测试。

    管理页 overlay 默认优先于 AstrBot 插件配置页。若缺少 baseline 判定，用户在插件
    配置页改的值会被旧 overlay 永久压制，表现为「配置页怎么改都没用」。以下用例锁定
    双向同步语义，避免回归。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        _DATA_DIR["path"] = self._tmp.name
        self.config_path = Path(self._tmp.name) / main._CONFIG_STORE_NAME

    def tearDown(self) -> None:
        main.RelationshipPlugin._current_instance = None
        self._tmp.cleanup()

    def _build(self, config) -> "main.RelationshipPlugin":
        plugin = main.RelationshipPlugin(FakeContext(), config)
        self.addCleanup(setattr, main.RelationshipPlugin, "_current_instance", None)
        return plugin

    def _save(self, plugin, body: dict) -> dict:
        async def fake_request_json():
            return body

        plugin._request_json = fake_request_json
        return _run(plugin._page_save_config())

    def _stored(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    # -- baseline 记录 --------------------------------------------------

    def test_save_records_baseline_alongside_override(self) -> None:
        """页面保存后，overlay 文件应同时留下写入当时的 AstrBot 侧取值。"""
        native = FakeNativeConfig({"MOOD_WINDOW_SECONDS": 300})
        plugin = self._build(native)
        self.assertTrue(self._save(plugin, {"MOOD_WINDOW_SECONDS": 600})["success"])

        stored = self._stored()
        self.assertEqual(stored["MOOD_WINDOW_SECONDS"], 600)
        # 回写成功时 AstrBot 侧已同步为新值，baseline 记新值。
        self.assertEqual(stored[main._BASELINE_KEY]["MOOD_WINDOW_SECONDS"], 600)
        self.assertEqual(native["MOOD_WINDOW_SECONDS"], 600)
        self.assertEqual(native.save_calls, 1)

    def test_baseline_records_old_value_when_native_write_fails(self) -> None:
        """回写不可用时，baseline 必须如实记录 AstrBot 侧仍是旧值。"""
        native = FakeNativeConfig({"MOOD_WINDOW_SECONDS": 300}, writable=False)
        plugin = self._build(native)
        self.assertTrue(self._save(plugin, {"MOOD_WINDOW_SECONDS": 600})["success"])

        stored = self._stored()
        self.assertEqual(stored["MOOD_WINDOW_SECONDS"], 600)
        self.assertEqual(stored[main._BASELINE_KEY]["MOOD_WINDOW_SECONDS"], 300)

    def test_no_baseline_for_field_absent_from_native_config(self) -> None:
        """AstrBot 侧没有该字段时不记基线。

        否则下次启动 schema 默认值一出现，就会被误判成「用户改过配置页」，
        把刚保存的页面设置立刻丢掉。
        """
        plugin = self._build({})  # 无 save_config：不可回写且字段缺失
        self.assertTrue(self._save(plugin, {"MOOD_WINDOW_SECONDS": 600})["success"])

        stored = self._stored()
        self.assertEqual(stored["MOOD_WINDOW_SECONDS"], 600)
        self.assertNotIn("MOOD_WINDOW_SECONDS", stored.get(main._BASELINE_KEY, {}))

    # -- 重启后的双向同步判定 ------------------------------------------

    def test_stale_override_dropped_when_plugin_page_changed_later(self) -> None:
        """插件配置页后改的值应胜出，过期 overlay 被丢弃并落盘清理。"""
        native = FakeNativeConfig({"MOOD_WINDOW_SECONDS": 300}, writable=False)
        plugin = self._build(native)
        self._save(plugin, {"MOOD_WINDOW_SECONDS": 600})  # baseline = 300
        main.RelationshipPlugin._current_instance = None

        # 重启：用户随后在插件配置页把值改成 900，已不等于 baseline 300。
        reborn = self._build(FakeNativeConfig({"MOOD_WINDOW_SECONDS": 900}))
        self.assertNotIn("MOOD_WINDOW_SECONDS", reborn._config_overrides)
        self.assertEqual(reborn._merged_config()["MOOD_WINDOW_SECONDS"], 900)
        self.assertEqual(reborn.manager._mood._window, 900)
        # 清理结果已落盘，不依赖下次保存。
        self.assertNotIn("MOOD_WINDOW_SECONDS", self._stored())

    def test_override_survives_when_plugin_page_unchanged(self) -> None:
        """插件配置页没动过时，页面 overlay 必须继续生效。"""
        native = FakeNativeConfig({"MOOD_WINDOW_SECONDS": 300}, writable=False)
        plugin = self._build(native)
        self._save(plugin, {"MOOD_WINDOW_SECONDS": 600})  # baseline = 300
        main.RelationshipPlugin._current_instance = None

        reborn = self._build(FakeNativeConfig({"MOOD_WINDOW_SECONDS": 300}))
        self.assertEqual(reborn._config_overrides["MOOD_WINDOW_SECONDS"], 600)
        self.assertEqual(reborn._merged_config()["MOOD_WINDOW_SECONDS"], 600)
        self.assertEqual(reborn.manager._mood._window, 600)

    def test_other_fields_unaffected_by_stale_drop(self) -> None:
        """丢弃只针对被改动的字段，同批次其他 overlay 不受牵连。"""
        native = FakeNativeConfig(
            {"MOOD_WINDOW_SECONDS": 300, "AFFINITY_HIGH_THRESHOLD": 60.0},
            writable=False,
        )
        plugin = self._build(native)
        self._save(
            plugin,
            {"MOOD_WINDOW_SECONDS": 600, "AFFINITY_HIGH_THRESHOLD": 80.0},
        )
        main.RelationshipPlugin._current_instance = None

        reborn = self._build(
            FakeNativeConfig(
                {"MOOD_WINDOW_SECONDS": 900, "AFFINITY_HIGH_THRESHOLD": 60.0}
            )
        )
        self.assertNotIn("MOOD_WINDOW_SECONDS", reborn._config_overrides)
        self.assertEqual(reborn._config_overrides["AFFINITY_HIGH_THRESHOLD"], 80.0)

    def test_legacy_overlay_without_baseline_keeps_working(self) -> None:
        """历史 overlay 文件没有 baseline 键，升级后应照旧生效而非崩溃。"""
        self.config_path.write_text(
            json.dumps({"MOOD_WINDOW_SECONDS": 600}), encoding="utf-8"
        )
        plugin = self._build(FakeNativeConfig({"MOOD_WINDOW_SECONDS": 300}))
        self.assertEqual(plugin._config_overrides["MOOD_WINDOW_SECONDS"], 600)
        self.assertEqual(plugin._config_baseline, {})
        self.assertEqual(plugin._merged_config()["MOOD_WINDOW_SECONDS"], 600)

    def test_baseline_key_never_leaks_into_public_config(self) -> None:
        """baseline 是内部记账，不得出现在页面配置或合并结果中。"""
        native = FakeNativeConfig({"MOOD_WINDOW_SECONDS": 300})
        plugin = self._build(native)
        self._save(plugin, {"MOOD_WINDOW_SECONDS": 600})
        self.assertNotIn(main._BASELINE_KEY, plugin._config_overrides)
        self.assertNotIn(main._BASELINE_KEY, plugin._merged_config())
        self.assertNotIn(main._BASELINE_KEY, plugin._public_config())


if __name__ == "__main__":
    unittest.main()
