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

    # -- on_llm_request 钩子 -------------------------------------------

    def test_on_llm_request_records_message_without_touching_req(self) -> None:
        req = object()
        _run(self.plugin.on_llm_request(FakeEvent(text="今天聊聊"), req))
        snapshot = _run(
            self.plugin.manager.get_snapshot("bot-1", "user-1", None)
        )
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


if __name__ == "__main__":
    unittest.main()
