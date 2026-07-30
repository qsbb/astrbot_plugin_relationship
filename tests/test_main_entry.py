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
    api_web = types.ModuleType("astrbot.api.web")

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

    class _Request:
        @staticmethod
        async def json(default=None):
            return default

    class _JsonResponse(dict):
        def __init__(self, payload, status_code):
            super().__init__(payload)
            self.status_code = status_code

    def json_response(payload, *, status_code=200):
        return _JsonResponse(payload, status_code)

    api_web.request = _Request()
    api_web.json_response = json_response

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
        platform_id: str = "qq-main",
        sender_name: str = "测试用户",
    ) -> None:
        self._text = text
        self._bot_id = bot_id
        self._sender_id = sender_id
        self._group_id = group_id
        self._message_id = message_id
        self._platform_id = platform_id
        self._sender_name = sender_name
        self.unified_msg_origin = (
            f"{platform_id}:GroupMessage:{group_id}"
            if group_id
            else f"{platform_id}:FriendMessage:{sender_id}"
        )

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

    def get_platform_id(self) -> str:
        return self._platform_id

    def get_platform_name(self) -> str:
        return self._platform_id

    def get_sender_name(self) -> str:
        return self._sender_name

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
        self.assertIn(f"/{main.PLUGIN_NAME}/identities", routes)
        self.assertIn(f"/{main.PLUGIN_NAME}/identity-merge", routes)
        self.assertIn(f"/{main.PLUGIN_NAME}/identity-delete", routes)

    def test_relationship_snapshot_contract_is_versioned_and_privacy_limited(self):
        contract = self.plugin.relationship_snapshot_contract()
        self.assertEqual(contract["name"], "relationship.snapshot")
        self.assertEqual(contract["version"], "1.0")
        self.assertEqual(contract["privacy"], "derived_only")

        snapshot = _run(
            self.plugin.get_relationship_snapshot("bot-1", "user-1", "group-1")
        )
        self.assertEqual(snapshot["version"], "1.0")
        self.assertIn(
            snapshot["relationship_tier"],
            {"guarded", "neutral", "familiar", "close", "inner_circle"},
        )
        self.assertEqual(
            set(snapshot),
            {
                "version",
                "mood",
                "willingness",
                "relationship_tier",
                "behavior",
                "silence",
            },
        )
        self.assertNotIn("affinity", snapshot)
        self.assertNotIn("trust", snapshot)
        self.assertNotIn("familiarity", snapshot)
        self.assertEqual(snapshot["behavior"]["followup"], "allow")

    def test_delivery_identity_requires_exact_bound_private_session(self) -> None:
        contract = self.plugin.delivery_identity_contract()
        self.assertEqual(contract["name"], "relationship.delivery_identity")
        self.assertEqual(contract["version"], "1.0")
        self.assertFalse(contract["exposes_raw_account_ids"])
        self.plugin.identity_registry.upsert(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                        "session_id": "qq-main:FriendMessage:user-1",
                    }
                ],
            }
        )

        verified = _run(
            self.plugin.resolve_delivery_identity(
                "summer", "qq-main:FriendMessage:user-1"
            )
        )
        self.assertTrue(verified["verified"])
        self.assertIn("relationship", verified)
        self.assertNotIn("user_id", str(verified))
        self.assertNotIn("bot_id", str(verified))

        denied = _run(
            self.plugin.resolve_delivery_identity(
                "summer", "qq-main:FriendMessage:someone-else"
            )
        )
        self.assertFalse(denied["verified"])
        self.assertEqual(denied["reason"], "bound_session_not_found")

        for invalid_umo in (
            "qq-main:GroupMessage:user-1",
            "qq-main:ChannelMessage:user-1",
            ":PrivateMessage:",
        ):
            denied = _run(self.plugin.resolve_delivery_identity("summer", invalid_umo))
            self.assertFalse(denied["verified"])
            self.assertEqual(denied["reason"], "private_session_required")

    def test_relationship_event_contract_records_trusted_semantics(self) -> None:
        contract = self.plugin.relationship_event_contract()
        self.assertEqual(contract["name"], "relationship.event")
        self.assertEqual(contract["version"], "1.0")
        self.assertNotIn("initial_prior", contract["event_kinds"])

        result = _run(
            self.plugin.submit_relationship_event(
                {
                    "version": "1.0",
                    "bot_id": "bot-1",
                    "user_id": "user-1",
                    "event_id": "verified-praise-1",
                    "kind": "praise",
                    "source": "direct",
                    "relationship_profile_id": "persona-a",
                    "confidence": 1,
                    "severity": 1,
                }
            )
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["snapshot"]["behavior"]["tone"], "warm_attentive")
        state = self.plugin.manager._states[
            "persona:persona-a:account:bot-1:user:user-1"
        ]
        self.assertGreater(state.affinity_score, 50)
        with self.assertRaisesRegex(ValueError, "RELATIONSHIP_EVENT_ID_REQUIRED"):
            _run(
                self.plugin.submit_relationship_event(
                    {
                        "bot_id": "bot-1",
                        "user_id": "user-1",
                        "kind": "praise",
                        "source": "direct",
                    }
                )
            )

    def test_relationship_event_resolves_bound_person_without_platform(self) -> None:
        self.plugin.identity_registry.upsert(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                    },
                    {
                        "platform_id": "telegram-main",
                        "user_id": "tg-user",
                        "bot_id": "tg-bot",
                    },
                ],
            }
        )

        result = _run(
            self.plugin.submit_relationship_event(
                {
                    "bot_id": "bot-1",
                    "user_id": "user-1",
                    "event_id": "bound-praise-1",
                    "kind": "praise",
                    "source": "direct",
                }
            )
        )

        self.assertTrue(result["accepted"])
        self.assertIn("persona:default:person:summer", self.plugin.manager._states)
        self.assertNotIn(
            "persona:default:account:bot-1:user:user-1",
            self.plugin.manager._states,
        )

    def test_relationship_event_rejects_ambiguous_unqualified_account(self) -> None:
        self.plugin.identity_registry.upsert(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "shared-user",
                        "bot_id": "bot-1",
                    }
                ],
            }
        )
        self.plugin.identity_registry.upsert(
            {
                "person_id": "other",
                "display_name": "另一人",
                "accounts": [
                    {
                        "platform_id": "telegram-main",
                        "user_id": "shared-user",
                        "bot_id": "bot-1",
                    }
                ],
            }
        )

        with self.assertRaisesRegex(
            ValueError, "RELATIONSHIP_EVENT_IDENTITY_AMBIGUOUS"
        ):
            _run(
                self.plugin.submit_relationship_event(
                    {
                        "bot_id": "bot-1",
                        "user_id": "shared-user",
                        "event_id": "ambiguous-praise-1",
                        "kind": "praise",
                        "source": "direct",
                    }
                )
            )

    def test_followup_guard_is_no_longer_owned_by_relationship(self) -> None:
        self.assertFalse(hasattr(main.RelationshipPlugin, "on_llm_response"))
        schema = json.loads((_PLUGIN_ROOT / "_conf_schema.json").read_text("utf-8"))
        for key in (
            "FOLLOWUP_GUARD_ENABLED",
            "FOLLOWUP_STREAK_LIMIT",
            "FOLLOWUP_WINDOW_SECONDS",
        ):
            self.assertNotIn(key, schema)

    # -- on_llm_request 钩子 -------------------------------------------

    def test_on_llm_request_records_message_without_touching_req(self) -> None:
        req = object()
        _run(self.plugin.on_llm_request(FakeEvent(text="今天聊聊"), req))
        snapshot = _run(self.plugin.manager.get_snapshot("bot-1", "user-1", None))
        state = self.plugin.manager._states.get(
            "persona:default:account:bot-1:user:user-1"
        )
        self.assertIsNotNone(state)
        self.assertEqual(state.interaction_count, 1)
        self.assertGreaterEqual(snapshot.familiarity, 0)
        # req 是普通 object：入口没有为它新增任何属性即未被修改。
        self.assertEqual(vars(req) if hasattr(req, "__dict__") else {}, {})

    def test_on_llm_request_command_text_is_readonly(self) -> None:
        _run(self.plugin.on_llm_request(FakeEvent(text="/rel status"), object()))
        self.assertNotIn(
            "persona:default:account:bot-1:user:user-1",
            self.plugin.manager._states,
        )

    def test_on_llm_request_missing_identity_is_noop(self) -> None:
        event = FakeEvent(text="hello", bot_id="", sender_id="")
        _run(self.plugin.on_llm_request(event, object()))
        self.assertEqual(self.plugin.manager._states, {})

    def test_on_llm_request_isolates_the_same_user_between_personas(self) -> None:
        def request(persona_id: str):
            return types.SimpleNamespace(
                conversation=types.SimpleNamespace(persona_id=persona_id),
                extra_user_content_parts=[],
                system_prompt="",
            )

        _run(
            self.plugin.on_llm_request(
                FakeEvent(text="persona a", message_id="same"), request("persona-a")
            )
        )
        _run(
            self.plugin.on_llm_request(
                FakeEvent(text="persona b", message_id="same"), request("persona-b")
            )
        )

        self.assertIn(
            "persona:persona-a:account:bot-1:user:user-1",
            self.plugin.manager._states,
        )
        self.assertIn(
            "persona:persona-b:account:bot-1:user:user-1",
            self.plugin.manager._states,
        )

    def test_persona_mapping_can_share_one_relationship_profile(self) -> None:
        self.plugin._persona_profile_map = {
            "persona-a": "companion",
            "persona-b": "companion",
        }

        def request(persona_id: str):
            return types.SimpleNamespace(
                conversation=types.SimpleNamespace(persona_id=persona_id),
                extra_user_content_parts=[],
                system_prompt="",
            )

        _run(
            self.plugin.on_llm_request(FakeEvent(message_id="a"), request("persona-a"))
        )
        _run(
            self.plugin.on_llm_request(FakeEvent(message_id="b"), request("persona-b"))
        )

        state = self.plugin.manager._states[
            "persona:companion:account:bot-1:user:user-1"
        ]
        self.assertEqual(state.interaction_count, 2)
        self.assertEqual(len(self.plugin.manager._states), 1)

    def test_resolved_session_persona_overrides_conversation_persona(self) -> None:
        class PersonaManager:
            default_persona = "default-persona"

            @staticmethod
            async def resolve_selected_persona(**_kwargs):
                return ("forced-persona", None, "forced-persona", False)

        self.context.persona_manager = PersonaManager()
        req = types.SimpleNamespace(
            conversation=types.SimpleNamespace(persona_id="conversation-persona"),
            extra_user_content_parts=[],
            system_prompt="",
        )

        _run(self.plugin.on_llm_request(FakeEvent(), req))

        self.assertIn(
            "persona:forced-persona:account:bot-1:user:user-1",
            self.plugin.manager._states,
        )

    def test_rel_reset_uses_cached_persona_when_resolver_fails(self) -> None:
        event = FakeEvent()
        req = types.SimpleNamespace(
            conversation=types.SimpleNamespace(persona_id="persona-a"),
            extra_user_content_parts=[],
            system_prompt="",
        )
        _run(self.plugin.on_llm_request(event, req))

        class PersonaManager:
            @staticmethod
            async def resolve_selected_persona(**_kwargs):
                raise RuntimeError("resolver unavailable")

        self.context.persona_manager = PersonaManager()
        _run(_collect(self.plugin.rel_reset(event)))

        self.assertNotIn(
            "persona:persona-a:account:bot-1:user:user-1",
            self.plugin.manager._states,
        )

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
        key = "persona:default:account:bot-1:user:user-1"
        self.assertIn(key, self.plugin.manager._states)
        results = _run(_collect(self.plugin.rel_reset(FakeEvent())))
        self.assertEqual(len(results), 1)
        self.assertIn("已重置", results[0])
        self.assertNotIn(key, self.plugin.manager._states)

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
        self.assertIn(
            "persona:default:account:bot-1:user:user-1",
            payload.get("users", {}),
        )

    # -- Plugin Page ---------------------------------------------------

    def test_page_overview_payload(self) -> None:
        _run(self.plugin.on_llm_request(FakeEvent(text="页面数据"), object()))
        payload = _run(self.plugin._page_overview())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["plugin"]["version"], main.__version__)
        self.assertEqual(payload["summary"]["user_count"], 1)
        self.assertEqual(payload["users"][0]["user_id"], "user-1")
        self.assertEqual(payload["users"][0]["scope_kind"], "account")
        self.assertEqual(payload["users"][0]["person_id"], "")
        self.assertEqual(payload["users"][0]["display_name"], "测试用户")
        self.assertEqual(payload["users"][0]["relationship_profile_id"], "default")
        self.assertEqual(
            payload["users"][0]["quick_account"],
            {
                "platform_id": "qq-main",
                "user_id": "user-1",
                "bot_id": "bot-1",
                "session_id": "qq-main:FriendMessage:user-1",
                "display_name": "测试用户",
                "complete": True,
            },
        )
        self.assertIn(payload["users"][0]["boundary"], ("开放", "谨慎"))

    def test_page_overview_groups_only_registered_person_profiles(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "Summer",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                    }
                ],
            }
        )
        self._save_identity(
            {
                "person_id": "departed",
                "display_name": "Departed",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-3",
                        "bot_id": "bot-1",
                    }
                ],
            }
        )

        def request(persona_id: str):
            return types.SimpleNamespace(
                conversation=types.SimpleNamespace(persona_id=persona_id),
                extra_user_content_parts=[],
                system_prompt="",
            )

        for persona_id in ("persona-a", "persona-b"):
            _run(
                self.plugin.on_llm_request(
                    FakeEvent(
                        sender_id="user-1",
                        message_id=f"registered-{persona_id}",
                    ),
                    request(persona_id),
                )
            )
            _run(
                self.plugin.on_llm_request(
                    FakeEvent(
                        sender_id="user-2",
                        message_id=f"unregistered-{persona_id}",
                    ),
                    request(persona_id),
                )
            )
            _run(
                self.plugin.on_llm_request(
                    FakeEvent(
                        sender_id="user-3",
                        message_id=f"orphaned-{persona_id}",
                    ),
                    request(persona_id),
                )
            )

        async def delete_request_json():
            return {"person_id": "departed"}

        self.plugin._request_json = delete_request_json
        self.assertTrue(_run(self.plugin._page_delete_identity())["success"])

        payload = _run(self.plugin._page_overview())
        registered = [
            item for item in payload["users"] if item["person_id"] == "summer"
        ]
        unregistered = [
            item for item in payload["users"] if item["user_id"] == "user-2"
        ]
        orphaned = [
            item
            for item in payload["users"]
            if item["orphaned_person_id"] == "departed"
        ]

        self.assertEqual(payload["summary"]["user_count"], 5)
        self.assertEqual(payload["policy"]["profile_count"], 2)
        self.assertEqual(len(registered), 1)
        self.assertEqual(
            registered[0]["relationship_profile_ids"],
            ["persona-a", "persona-b"],
        )
        self.assertEqual(registered[0]["merged_profile_count"], 2)
        self.assertEqual(registered[0]["interaction_count"], 2)
        self.assertEqual(len(unregistered), 2)
        self.assertTrue(all(not item["person_id"] for item in unregistered))
        self.assertTrue(
            all(len(item["relationship_profile_ids"]) == 1 for item in unregistered)
        )
        self.assertEqual(len(orphaned), 2)
        self.assertTrue(all(not item["person_id"] for item in orphaned))
        self.assertTrue(
            all(len(item["relationship_profile_ids"]) == 1 for item in orphaned)
        )

    def test_group_observation_does_not_prefill_group_umo(self) -> None:
        _run(
            self.plugin.on_llm_request(
                FakeEvent(sender_id="group-user", group_id="group-1"), object()
            )
        )

        observation = self.plugin.account_observations.get("bot-1", "group-user")

        self.assertIsNotNone(observation)
        self.assertEqual(observation["platform_id"], "qq-main")
        self.assertEqual(observation["session_id"], "")

    def _save_identity(self, body: dict) -> dict:
        async def fake_request_json():
            return body

        self.plugin._request_json = fake_request_json
        return _run(self.plugin._page_save_identity())

    def _merge_identity(self, body: dict) -> dict:
        async def fake_request_json():
            return body

        self.plugin._request_json = fake_request_json
        return _run(self.plugin._page_merge_identity())

    def test_identity_rejects_invalid_profile_before_persisting(self) -> None:
        payload = self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "relationship_profile_id": "bad/profile",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                    }
                ],
            }
        )

        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "INVALID_RELATIONSHIP_PROFILE_ID")
        self.assertIsNone(self.plugin.identity_registry.get("summer"))

    def test_identity_binding_failure_rolls_back_registry_and_relation(self) -> None:
        _run(self.plugin.on_llm_request(FakeEvent(), object()))
        alias = "persona:default:account:bot-1:user:user-1"

        class FailingRepository:
            @staticmethod
            def save(_states, _events) -> None:
                raise OSError("disk unavailable")

        self.plugin.manager._repo = FailingRepository()
        payload = self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                    }
                ],
            }
        )

        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "RELATIONSHIP_PERSIST_FAILED")
        self.assertIsNone(self.plugin.identity_registry.get("summer"))
        self.assertIn(alias, self.plugin.manager._states)

    def test_identity_page_lists_runtime_persona_profiles(self) -> None:
        req = types.SimpleNamespace(
            conversation=types.SimpleNamespace(persona_id="runtime-persona"),
            extra_user_content_parts=[],
            system_prompt="",
        )
        _run(self.plugin.on_llm_request(FakeEvent(), req))

        payload = _run(self.plugin._page_identities())

        self.assertIn("runtime-persona", payload["relationship_profiles"])

    def test_identity_binding_shares_relationship_state_between_platforms(self) -> None:
        payload = self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                    },
                    {
                        "platform_id": "telegram-main",
                        "user_id": "tg-user",
                        "bot_id": "tg-bot",
                    },
                ],
            }
        )
        self.assertTrue(payload["success"])

        qq_event = FakeEvent(text="QQ 上聊过的事")
        _run(self.plugin.on_llm_request(qq_event, object()))
        _run(
            self.plugin.on_llm_request(
                FakeEvent(
                    text="换到 Telegram 继续",
                    bot_id="tg-bot",
                    sender_id="tg-user",
                    platform_id="telegram-main",
                    message_id="msg-2",
                ),
                object(),
            )
        )

        canonical_key = "persona:default:person:summer"
        canonical = self.plugin.manager._states[canonical_key]
        self.assertEqual(canonical.interaction_count, 2)
        overview = _run(self.plugin._page_overview())
        self.assertEqual(overview["users"][0]["scope_kind"], "person")
        self.assertEqual(overview["users"][0]["person_id"], "summer")
        self.assertIsNone(overview["users"][0]["quick_account"])
        self.assertNotIn(
            "persona:default:account:bot-1:user:user-1",
            self.plugin.manager._states,
        )
        self.assertNotIn(
            "persona:default:account:tg-bot:user:tg-user",
            self.plugin.manager._states,
        )
        context = main.ensure_context(qq_event)
        identity = context["artifacts"]["relationship"]["canonical_identity"]
        self.assertEqual(
            identity,
            {
                "mapped": True,
                "account_count": 2,
                "permission_identity_mode": "raw_platform_account",
            },
        )

    def test_quick_account_merge_preserves_both_relationship_states(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                    }
                ],
            }
        )
        _run(self.plugin.on_llm_request(FakeEvent(message_id="target-1"), object()))
        source_event = FakeEvent(
            bot_id="discord-bot",
            sender_id="discord-user",
            platform_id="discord-main",
            message_id="source-1",
        )
        _run(self.plugin.on_llm_request(source_event, object()))

        body = {
            "target_person_id": "summer",
            "account": {
                "platform_id": "discord-main",
                "user_id": "discord-user",
                "bot_id": "discord-bot",
                "session_id": "discord-main:DirectMessage:discord-user",
            },
        }
        first = self._merge_identity(body)
        second = self._merge_identity(body)

        target_key = "persona:default:person:summer"
        source_key = "persona:default:account:discord-bot:user:discord-user"
        self.assertTrue(first["success"])
        self.assertTrue(first["state_merged"])
        self.assertTrue(second["success"])
        self.assertFalse(second["state_merged"])
        self.assertEqual(self.plugin.manager._states[target_key].interaction_count, 2)
        self.assertNotIn(source_key, self.plugin.manager._states)
        self.assertEqual(len(self.plugin.identity_registry.get("summer").accounts), 2)

    def test_registered_identity_merge_moves_accounts_and_relationship(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {"platform_id": "qq-main", "user_id": "user-1", "bot_id": "bot-1"}
                ],
            }
        )
        self._save_identity(
            {
                "person_id": "work-account",
                "display_name": "工作账号",
                "accounts": [
                    {
                        "platform_id": "discord-main",
                        "user_id": "discord-user",
                        "bot_id": "discord-bot",
                    }
                ],
            }
        )
        _run(self.plugin.on_llm_request(FakeEvent(message_id="target-1"), object()))
        _run(
            self.plugin.on_llm_request(
                FakeEvent(
                    bot_id="discord-bot",
                    sender_id="discord-user",
                    platform_id="discord-main",
                    message_id="source-1",
                ),
                object(),
            )
        )

        payload = self._merge_identity(
            {"target_person_id": "summer", "source_person_id": "work-account"}
        )

        self.assertTrue(payload["success"])
        self.assertTrue(payload["source_removed"])
        self.assertIsNone(self.plugin.identity_registry.get("work-account"))
        self.assertEqual(len(self.plugin.identity_registry.get("summer").accounts), 2)
        self.assertEqual(
            self.plugin.manager._states[
                "persona:default:person:summer"
            ].interaction_count,
            2,
        )
        self.assertNotIn(
            "persona:default:person:work-account", self.plugin.manager._states
        )

    def test_identity_merge_failure_restores_registry_and_relationship_states(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {"platform_id": "qq-main", "user_id": "user-1", "bot_id": "bot-1"}
                ],
            }
        )
        self._save_identity(
            {
                "person_id": "work-account",
                "display_name": "工作账号",
                "accounts": [
                    {
                        "platform_id": "discord-main",
                        "user_id": "discord-user",
                        "bot_id": "discord-bot",
                    }
                ],
            }
        )
        _run(self.plugin.on_llm_request(FakeEvent(message_id="target-1"), object()))
        _run(
            self.plugin.on_llm_request(
                FakeEvent(
                    bot_id="discord-bot",
                    sender_id="discord-user",
                    platform_id="discord-main",
                    message_id="source-1",
                ),
                object(),
            )
        )

        class FailingRepository:
            @staticmethod
            def save(_states, _events) -> None:
                raise OSError("disk unavailable")

        self.plugin.manager._repo = FailingRepository()
        payload = self._merge_identity(
            {"target_person_id": "summer", "source_person_id": "work-account"}
        )

        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "RELATIONSHIP_PERSIST_FAILED")
        self.assertIsNotNone(self.plugin.identity_registry.get("summer"))
        self.assertIsNotNone(self.plugin.identity_registry.get("work-account"))
        self.assertIn("persona:default:person:summer", self.plugin.manager._states)
        self.assertIn("persona:default:person:work-account", self.plugin.manager._states)

    def test_pending_account_merge_is_recovered_after_restart(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {"platform_id": "qq-main", "user_id": "user-1", "bot_id": "bot-1"}
                ],
            }
        )
        _run(self.plugin.on_llm_request(FakeEvent(message_id="target-1"), object()))
        _run(
            self.plugin.on_llm_request(
                FakeEvent(
                    bot_id="discord-bot",
                    sender_id="discord-user",
                    platform_id="discord-main",
                    message_id="source-1",
                ),
                object(),
            )
        )
        target_key = "persona:default:person:summer"
        source_key = "persona:default:account:discord-bot:user:discord-user"
        account = {
            "platform_id": "discord-main",
            "user_id": "discord-user",
            "bot_id": "discord-bot",
        }
        self.plugin._write_identity_merge_intent(
            {
                "mode": "account",
                "target_person_id": "summer",
                "source_person_id": "",
                "account": {
                    "platform_id": "discord-main",
                    "user_id": "discord-user",
                    "bot_id": "discord-bot",
                    "session_id": "",
                    "label": "",
                    "memory_profile_id": "",
                },
                "source_accounts": [],
                "bindings": [{"target": target_key, "sources": [source_key]}],
            }
        )
        self.plugin.identity_registry.merge_account("summer", account)
        self.assertIn(source_key, self.plugin.manager._states)

        recovered = main.RelationshipPlugin(
            self.context, {"SAVE_INTERVAL_SECONDS": 0}
        )
        self.plugin = recovered

        self.assertFalse(
            (Path(self._tmp.name) / main._IDENTITY_MERGE_JOURNAL_NAME).exists()
        )
        self.assertEqual(recovered.manager._states[target_key].interaction_count, 2)
        self.assertNotIn(source_key, recovered.manager._states)

    def test_conflicting_account_intent_is_not_recovered(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {"platform_id": "qq-main", "user_id": "user-1", "bot_id": "bot-1"}
                ],
            }
        )
        _run(self.plugin.on_llm_request(FakeEvent(message_id="target-1"), object()))
        _run(
            self.plugin.on_llm_request(
                FakeEvent(bot_id="other-bot", message_id="source-1"), object()
            )
        )
        target_key = "persona:default:person:summer"
        source_key = "persona:default:account:other-bot:user:user-1"
        self.plugin._write_identity_merge_intent(
            {
                "mode": "account",
                "target_person_id": "summer",
                "source_person_id": "",
                "account": {
                    "platform_id": "qq-main",
                    "user_id": "user-1",
                    "bot_id": "other-bot",
                    "session_id": "qq-main:FriendMessage:user-1",
                    "label": "",
                    "memory_profile_id": "",
                },
                "source_accounts": [],
                "bindings": [{"target": target_key, "sources": [source_key]}],
            }
        )

        recovered = main.RelationshipPlugin(
            self.context, {"SAVE_INTERVAL_SECONDS": 0}
        )
        self.plugin = recovered

        self.assertFalse(
            (Path(self._tmp.name) / main._IDENTITY_MERGE_JOURNAL_NAME).exists()
        )
        self.assertEqual(recovered.manager._states[target_key].interaction_count, 1)
        self.assertIn(source_key, recovered.manager._states)

    def test_future_relationship_schema_blocks_identity_writes_and_recovery(
        self,
    ) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "Summer",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                    }
                ],
            }
        )
        data_dir = Path(self._tmp.name)
        registry_path = data_dir / "identity_registry.json"
        registry_before = registry_path.read_text(encoding="utf-8")
        relationship_path = data_dir / "relationship_state.json"
        relationship_path.write_text(
            json.dumps(
                {
                    "schema_version": 999,
                    "users": {
                        "persona:default:account:bot-2:user:user-2": {
                            "interaction_count": 4
                        }
                    },
                    "events": [],
                }
            ),
            encoding="utf-8",
        )
        account = self.plugin.identity_registry.get("summer").accounts[0]
        journal_path = data_dir / main._IDENTITY_MERGE_JOURNAL_NAME
        journal_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "mode": "account",
                    "target_person_id": "summer",
                    "source_person_id": "",
                    "account": account.as_dict(),
                    "source_accounts": [],
                    "bindings": [
                        {
                            "target": "persona:default:person:summer",
                            "sources": [account.state_key_for("default")],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        protected = main.RelationshipPlugin(
            self.context, {"SAVE_INTERVAL_SECONDS": 0}
        )
        self.plugin = protected

        self.assertTrue(protected.manager.persistence_write_blocked)
        self.assertTrue(journal_path.exists())

        merge_result = self._merge_identity(
            {
                "target_person_id": "summer",
                "account": {
                    "platform_id": "discord-main",
                    "user_id": "user-2",
                    "bot_id": "bot-2",
                },
            }
        )
        save_result = self._save_identity(
            {
                "person_id": "other",
                "display_name": "Other",
                "accounts": [
                    {"platform_id": "tg-main", "user_id": "user-3"}
                ],
            }
        )

        async def delete_request_json():
            return {"person_id": "summer"}

        protected._request_json = delete_request_json
        delete_result = _run(protected._page_delete_identity())

        for result in (merge_result, save_result, delete_result):
            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "RELATIONSHIP_STORAGE_READ_ONLY")
        self.assertEqual(registry_path.read_text(encoding="utf-8"), registry_before)
        self.assertTrue(journal_path.exists())
        self.assertEqual(
            json.loads(relationship_path.read_text(encoding="utf-8"))["schema_version"],
            999,
        )

    def test_identity_can_apply_one_fixed_initial_prior(self) -> None:
        body = {
            "person_id": "summer",
            "display_name": "心夏",
            "relationship_profile_id": "persona-a",
            "initial_prior": "fond",
            "accounts": [
                {
                    "platform_id": "qq-main",
                    "user_id": "user-1",
                    "bot_id": "bot-1",
                    "memory_profile_id": "persona-a",
                }
            ],
        }

        first = self._save_identity(body)
        second = self._save_identity(body)

        self.assertTrue(first["initial_prior"]["applied"])
        self.assertFalse(second["initial_prior"]["applied"])
        self.assertEqual(
            second["initial_prior"]["error"], "INITIAL_PRIOR_ALREADY_APPLIED"
        )
        state = self.plugin.manager._states["persona:persona-a:person:summer"]
        self.assertEqual(state.affinity_score, 64)
        self.assertEqual(state.trust_score, 60)
        self.assertEqual(state.familiarity_score, 25)

    def test_cross_platform_memory_queries_only_other_verified_account(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                    },
                    {
                        "platform_id": "telegram-main",
                        "user_id": "tg-user",
                        "bot_id": "tg-bot",
                        "session_id": "telegram-main:FriendMessage:tg-user",
                    },
                ],
            }
        )

        calls: list[dict] = []

        class Bridge:
            @staticmethod
            async def compose_injection(query, *, session_context, top_k, max_chars):
                calls.append(
                    {
                        "query": query,
                        "session_context": session_context,
                        "top_k": top_k,
                        "max_chars": max_chars,
                    }
                )
                return "用户昨天在另一个平台聊过旅行计划。"

        self.plugin._memory_companion_bridge = lambda: Bridge()
        req = types.SimpleNamespace(extra_user_content_parts=[], system_prompt="")
        event = FakeEvent(text="旅行继续聊")
        _run(self.plugin.on_cross_platform_memory(event, req))

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["session_context"]["platform"], "telegram-main")
        self.assertEqual(calls[0]["session_context"]["user_id"], "tg-user")
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertIn(
            "同一自然人的跨平台连续记忆", str(req.extra_user_content_parts[0])
        )
        self.assertIn("旅行计划", str(req.extra_user_content_parts[0]))
        context = main.ensure_context(event)
        artifact = context["artifacts"]["relationship"]["cross_platform_memory"]
        self.assertEqual(
            set(artifact),
            {
                "queried_accounts",
                "injected_chars",
                "provider",
                "relationship_profile_id",
            },
        )
        fragments = context["artifacts"]["relationship"]["prompt_fragments"]
        self.assertNotIn("person_id", fragments[0]["metadata"])

    def test_memory_bridge_reuses_astrbot_loaded_module_instance(self) -> None:
        bridge = object()
        module_name = "data.plugins.astrbot_plugin_memory_companion.main"
        loaded = types.ModuleType(module_name)
        loaded.get_active_bridge = lambda: bridge
        sys.modules[module_name] = loaded
        try:
            self.assertIs(self.plugin._memory_companion_bridge(), bridge)
        finally:
            sys.modules.pop(module_name, None)

    def test_cross_platform_memory_does_not_cross_relationship_profiles(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                        "memory_profile_id": "persona-a",
                    },
                    {
                        "platform_id": "telegram-main",
                        "user_id": "tg-user",
                        "bot_id": "tg-bot",
                        "memory_profile_id": "persona-b",
                    },
                ],
            }
        )
        calls = []

        class Bridge:
            @staticmethod
            async def compose_injection(*args, **kwargs):
                calls.append((args, kwargs))
                return "should not be used"

        self.plugin._memory_companion_bridge = lambda: Bridge()
        req = types.SimpleNamespace(
            conversation=types.SimpleNamespace(persona_id="persona-a"),
            extra_user_content_parts=[],
            system_prompt="",
        )

        _run(self.plugin.on_cross_platform_memory(FakeEvent(), req))

        self.assertEqual(calls, [])
        self.assertEqual(req.extra_user_content_parts, [])

    def test_page_delete_identity_keeps_relationship_state(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {
                        "platform_id": "qq-main",
                        "user_id": "user-1",
                        "bot_id": "bot-1",
                    }
                ],
            }
        )
        _run(self.plugin.on_llm_request(FakeEvent(), object()))

        async def fake_request_json():
            return {"person_id": "summer"}

        self.plugin._request_json = fake_request_json
        payload = _run(self.plugin._page_delete_identity())
        self.assertTrue(payload["success"])
        self.assertEqual(payload.status_code, 200)
        self.assertIsNone(self.plugin.identity_registry.get("summer"))
        self.assertIn("persona:default:person:summer", self.plugin.manager._states)
        overview = _run(self.plugin._page_overview())
        self.assertEqual(overview["users"][0]["orphaned_person_id"], "summer")
        not_found = _run(self.plugin._page_delete_identity())
        self.assertFalse(not_found["success"])
        self.assertEqual(not_found["error"], "NOT_FOUND")
        self.assertEqual(not_found.status_code, 404)

    def test_orphaned_relationship_can_merge_into_existing_identity(self) -> None:
        self._save_identity(
            {
                "person_id": "summer",
                "display_name": "心夏",
                "accounts": [
                    {"platform_id": "qq-main", "user_id": "user-1", "bot_id": "bot-1"}
                ],
            }
        )
        self._save_identity(
            {
                "person_id": "old-work",
                "display_name": "旧工作身份",
                "accounts": [
                    {
                        "platform_id": "discord-main",
                        "user_id": "discord-user",
                        "bot_id": "discord-bot",
                    }
                ],
            }
        )
        _run(self.plugin.on_llm_request(FakeEvent(message_id="target-1"), object()))
        _run(
            self.plugin.on_llm_request(
                FakeEvent(
                    bot_id="discord-bot",
                    sender_id="discord-user",
                    platform_id="discord-main",
                    message_id="source-1",
                ),
                object(),
            )
        )

        async def delete_request_json():
            return {"person_id": "old-work"}

        self.plugin._request_json = delete_request_json
        self.assertTrue(_run(self.plugin._page_delete_identity())["success"])
        payload = self._merge_identity(
            {"target_person_id": "summer", "source_person_id": "old-work"}
        )

        self.assertTrue(payload["success"])
        self.assertTrue(payload["state_merged"])
        self.assertFalse(payload["source_removed"])
        self.assertEqual(
            self.plugin.manager._states[
                "persona:default:person:summer"
            ].interaction_count,
            2,
        )
        self.assertNotIn("persona:default:person:old-work", self.plugin.manager._states)

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

    def test_page_save_config_ignores_unchanged_legacy_profile(self) -> None:
        payload = self._save_config(
            {"RELATIONSHIP_LEGACY_PROFILE_ID": self.plugin._legacy_profile_id}
        )

        self.assertTrue(payload["success"])
        self.assertFalse(payload["restart_required"])
        self.assertFalse((Path(self._tmp.name) / main._CONFIG_STORE_NAME).exists())

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
