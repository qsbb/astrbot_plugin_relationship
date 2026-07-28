"""凝心溯溪-情：AstrBot 关系状态插件入口。

本入口只负责 AstrBot 适配：
- 在 on_llm_request 阶段记录互动；
- 通过 Manager 提供结构化只读建议，不修改请求或发送流程；
- 提供 /rel status 与 /rel reset 命令；
- terminate 时强制持久化长期状态。

核心逻辑不依赖 AstrBot，集中在 core/；其他插件应只消费
RelationshipStateManager.record / get_snapshot / reset。
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time
from typing import Any, Mapping

try:
    from astrbot.api.web import json_response, request
except ImportError:
    json_response = None
    request = None

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .core.affinity import AffinityCalculator
from .core.config import (
    DEFAULTS,
    affinity_config,
    decay_config,
    familiarity_config,
    followup_config,
    log_level,
    mood_enabled,
    mood_kwargs,
    policy_config,
    prompt_config,
    save_interval_seconds,
    trust_config,
)
from .core.familiarity import FamiliarityCalculator
from .core.manager import RelationshipStateManager
from .core.models import InteractionEvent, RelationshipScope
from .core.mood import MoodTracker
from .core.prompts import INJECT_MARKER, build_injection_block
from .core.repository import JsonRepository
from .core.trust import TrustCalculator

PLUGIN_NAME = "astrbot_plugin_relationship"
__version__ = "0.4.0"

_CONFIG_STORE_NAME = "relationship-config.json"

# overlay 文件中保存「写入当时 AstrBot 插件配置页取值」的键名。
# 不属于用户配置，读写时都要排除。
_BASELINE_KEY = "__astrbot_baseline__"


@register(
    PLUGIN_NAME,
    "Justice-ocr",
    "凝心溯溪-情，统一管理情绪、好感、信任与熟悉度",
    __version__,
)
class RelationshipPlugin(Star):
    """关系状态插件。"""

    _current_instance: "RelationshipPlugin | None" = None

    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.context = context
        self.logger = logger
        self._raw_config = self._coerce_config(config)
        self._native_config = (
            config if callable(getattr(config, "save_config", None)) else None
        )

        data_dir = pathlib.Path(StarTools.get_data_dir(PLUGIN_NAME))
        data_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir = data_dir
        overrides, baseline = self._config_store_read()
        self._config_overrides: dict[str, Any] = overrides
        self._config_baseline: dict[str, Any] = baseline
        # 双向同步：若用户在 AstrBot 插件配置页改过某字段，丢弃该字段的过期页面设置。
        dropped = self._drop_stale_overrides()
        if dropped:
            self.logger.info(
                "[rel] 检测到 AstrBot 插件配置页更新，已放弃过期页面设置: %s",
                ", ".join(sorted(dropped)),
            )

        merged = self._merged_config()
        repository = JsonRepository(data_dir / "relationship_state.json")
        tracker = MoodTracker(**mood_kwargs(merged))
        self.manager = RelationshipStateManager(
            repository=repository,
            mood_tracker=tracker,
            affinity=AffinityCalculator(affinity_config(merged)),
            trust=TrustCalculator(trust_config(merged)),
            familiarity=FamiliarityCalculator(familiarity_config(merged)),
            decay_config=decay_config(merged),
            policy_config=policy_config(merged),
            followup_config=followup_config(merged),
            save_interval_seconds=save_interval_seconds(merged),
            mood_enabled=mood_enabled(merged),
            logger=self.logger,
        )
        self._mood_enabled = mood_enabled(merged)
        self._affinity_config = affinity_config(merged)
        self._prompt_config = prompt_config(merged)
        self._apply_log_level()
        self._register_pages_web_api()
        RelationshipPlugin._current_instance = self
        self.logger.info("[relationship] 凝心溯溪-情 v%s 已加载", __version__)

    # ------------------------------------------------------------------
    # LLM 请求：记录事件、注入表达约束
    # ------------------------------------------------------------------

    @filter.on_llm_request(priority=600)
    async def on_llm_request(
        self, event: AstrMessageEvent, req: Any, *args: Any, **kwargs: Any
    ) -> None:
        """记录本轮互动，并把关系表达约束注入本轮请求。

        注入内容只影响表达方式与收尾方式；不改变事实判断、安全边界与工具权限，
        也不阻断事件或接管发送。命令消息与静默建议轮次不注入。
        """
        del args, kwargs
        plugin = RelationshipPlugin._current_instance or self
        if not isinstance(plugin, RelationshipPlugin):
            return

        text = plugin._get_text(event)
        scope = plugin._get_scope(event)
        if not scope.bot_id or not scope.user_id:
            return
        kind = "command" if text.lstrip().startswith("/") else "message"
        interaction = InteractionEvent(
            bot_id=scope.bot_id,
            user_id=scope.user_id,
            group_id=scope.group_id,
            text=text,
            timestamp=time.time(),
            kind=kind,
            event_id=plugin._safe_event_id(event, "get_message_id"),
            source="platform_message",
        )

        # 即使关闭短期情绪，也继续记录长期关系；Manager 内部会跳过 mood 累积。
        snapshot = await plugin.manager.record(interaction)

        if snapshot.should_silence:
            plugin.logger.debug(
                "[relationship] 静默建议 scope=%s mood=%s willingness=%d",
                scope.session_key,
                snapshot.mood,
                snapshot.willingness,
            )
            return

        if kind == "command":
            return

        followup = await plugin.manager.followup_state(scope)
        block = build_injection_block(snapshot, followup, plugin._prompt_config)
        if block:
            plugin._inject(req, block)

    def _inject(self, req: Any, block: str) -> bool:
        """把约束块写入本轮请求；优先 extra_user_content_parts，降级 system_prompt。

        同一轮重复注入会被标记幂等跳过，避免多个钩子叠加同样的约束。
        """
        if req is None or not block:
            return False
        if self._already_injected(req):
            return False
        try:
            parts = getattr(req, "extra_user_content_parts", None)
        except Exception:
            parts = None
        if parts is not None:
            try:
                from astrbot.core.agent.message import TextPart

                parts.append(TextPart(text=block))
                return True
            except Exception:
                try:
                    parts.append({"type": "text", "text": block})
                    return True
                except Exception as exc:
                    self.logger.debug("[relationship] parts 注入失败: %s", exc)
        try:
            current = getattr(req, "system_prompt", None) or ""
            req.system_prompt = f"{current}\n\n{block}" if current else block
            return True
        except Exception as exc:
            self.logger.warning("[relationship] 表达约束注入失败: %s", exc)
            return False

    @staticmethod
    def _already_injected(req: Any) -> bool:
        try:
            if INJECT_MARKER in (getattr(req, "system_prompt", None) or ""):
                return True
        except Exception:
            return False
        try:
            parts = getattr(req, "extra_user_content_parts", None) or ()
            for part in parts:
                text = getattr(part, "text", None)
                if text is None and isinstance(part, Mapping):
                    text = part.get("text")
                if isinstance(text, str) and INJECT_MARKER in text:
                    return True
        except Exception:
            return False
        return False

    # ------------------------------------------------------------------
    # LLM 响应：统计服务式追问收尾
    # ------------------------------------------------------------------

    @filter.on_llm_response(priority=600)
    async def on_llm_response(
        self, event: AstrMessageEvent, resp: Any, *args: Any, **kwargs: Any
    ) -> None:
        """统计 bot 本轮是否以服务式追问收尾；只记录特征，不修改回复。"""
        del args, kwargs
        plugin = RelationshipPlugin._current_instance or self
        if not isinstance(plugin, RelationshipPlugin):
            return
        scope = plugin._get_scope(event)
        if not scope.bot_id or not scope.user_id:
            return
        text = plugin._response_text(resp)
        if not text:
            return
        decision = await plugin.manager.record_bot_reply(scope, text)
        if decision.suppressed:
            plugin.logger.debug(
                "[relationship] 追问收尾 scope=%s level=%s streak=%d",
                scope.session_key,
                decision.level,
                decision.streak,
            )

    @staticmethod
    def _response_text(resp: Any) -> str:
        """尽可能安全地取出本轮回复文本，取不到就放弃统计。"""
        if resp is None:
            return ""
        if isinstance(resp, str):
            return resp.strip()
        for attr in ("completion_text", "text", "content"):
            try:
                value = getattr(resp, attr, None)
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()
        getter = getattr(resp, "get_plain_text", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                return ""
            if isinstance(value, str):
                return value.strip()
        return ""

    # ------------------------------------------------------------------
    # Plugin Page：关系总览与好感可视化
    # ------------------------------------------------------------------

    def _register_pages_web_api(self) -> bool:
        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            return False
        routes = (
            ("overview", self._page_overview, ["GET"], "关系状态总览"),
            ("config", self._page_get_config, ["GET"], "读取关系插件配置"),
            ("config", self._page_save_config, ["POST"], "保存关系插件配置"),
        )
        try:
            for name, handler, methods, description in routes:
                register(f"/{PLUGIN_NAME}/{name}", handler, methods, description)
        except Exception as exc:
            self.logger.debug("[relationship] page api unavailable: %s", exc)
            return False
        return True

    def _relation_band(self, score: float) -> str:
        if score >= self._affinity_config.high_affinity_threshold:
            return "高好感 / 信任圈"
        if score >= 60:
            return "朋友"
        if score >= 40:
            return "普通熟人"
        if score >= 25:
            return "保持距离"
        return "边界警戒"

    async def _page_overview(self):
        states = getattr(self.manager, "_states", {})
        users = []
        whitelist = set(self._affinity_config.whitelist_user_ids)
        for key, state in states.items():
            user_id = key.rsplit(":user:", 1)[-1]
            users.append(
                {
                    "user_id": user_id,
                    "affinity": round(state.affinity_score, 1),
                    "trust": round(state.trust_score, 1),
                    "familiarity": round(state.familiarity_score, 1),
                    "interaction_count": state.interaction_count,
                    "band": self._relation_band(state.affinity_score),
                    "whitelisted": user_id in whitelist,
                    "boundary": "开放"
                    if (
                        user_id in whitelist
                        and state.affinity_score
                        >= self._affinity_config.high_affinity_threshold
                        and state.trust_score
                        >= self._affinity_config.whitelist_trust_gate
                        and state.familiarity_score
                        >= self._affinity_config.whitelist_familiarity_gate
                    )
                    else "谨慎",
                    "last_event_at": state.last_event_at,
                }
            )
        users.sort(key=lambda item: (item["affinity"], item["trust"]), reverse=True)
        bands = {
            name: sum(item["band"] == name for item in users)
            for name in ("高好感 / 信任圈", "朋友", "普通熟人", "保持距离", "边界警戒")
        }
        payload = {
            "success": True,
            "plugin": {"id": PLUGIN_NAME, "version": __version__},
            "policy": {
                "high_affinity_threshold": self._affinity_config.high_affinity_threshold,
                "non_whitelist_ceiling": self._affinity_config.non_whitelist_ceiling,
                "trust_gate": self._affinity_config.whitelist_trust_gate,
                "familiarity_gate": self._affinity_config.whitelist_familiarity_gate,
                "whitelist_count": len(whitelist),
            },
            "summary": {
                "user_count": len(users),
                "high_affinity_count": bands["高好感 / 信任圈"],
                "friend_count": bands["朋友"],
                "cautious_count": sum(bands[name] for name in ("保持距离", "边界警戒")),
                "bands": bands,
            },
            "users": users,
        }
        return json_response(payload) if json_response else payload

    # ------------------------------------------------------------------
    # Plugin Page：配置读写与热应用
    # ------------------------------------------------------------------

    @staticmethod
    def _schema() -> dict[str, dict[str, Any]]:
        path = pathlib.Path(__file__).with_name("_conf_schema.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _get(self, key: str, default: Any) -> Any:
        if key in self._config_overrides:
            return self._config_overrides[key]
        if isinstance(self._raw_config, Mapping):
            return self._raw_config.get(key, default)
        getter = getattr(self._raw_config, "get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except TypeError:
                value = getter(key)
                return default if value is None else value
        return default

    def _merged_config(self) -> dict[str, Any]:
        base = dict(self._raw_config) if isinstance(self._raw_config, Mapping) else {}
        base.update(self._config_overrides)
        return base

    def _public_config(self) -> dict[str, Any]:
        public: dict[str, Any] = {}
        for key, field in self._schema().items():
            public[key] = self._get(key, field.get("default", DEFAULTS.get(key)))
        return public

    @staticmethod
    def _coerce_page_value(key: str, value: Any, field: dict[str, Any]) -> Any:
        kind = field.get("type")
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                return value.lower() == "true"
            raise ValueError(key)
        if kind == "int":
            value = int(value)
            minimum = field.get("minimum")
            maximum = field.get("maximum")
            if minimum is not None and value < minimum:
                raise ValueError(key)
            if maximum is not None and value > maximum:
                raise ValueError(key)
            return value
        if kind == "float":
            value = float(value)
            minimum = field.get("minimum")
            maximum = field.get("maximum")
            if minimum is not None and value < minimum:
                raise ValueError(key)
            if maximum is not None and value > maximum:
                raise ValueError(key)
            return value
        if kind == "string":
            value = str(value)
            options = field.get("options")
            if options and value not in options:
                raise ValueError(key)
            return value
        raise TypeError(key)

    async def _request_json(self) -> Any:
        if request is None:
            return None
        json_reader = getattr(request, "json", None)
        if callable(json_reader):
            value = json_reader(default={})
        else:
            get_json = getattr(request, "get_json", None)
            if not callable(get_json):
                return None
            value = get_json(force=True)
        if hasattr(value, "__await__"):
            value = await value
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        return value

    async def _page_get_config(self):
        payload = {
            "success": True,
            "config": self._public_config(),
            "schema": self._schema(),
        }
        return json_response(payload) if json_response else payload

    async def _page_save_config(self):
        data = await self._request_json()
        if not isinstance(data, dict):
            payload = {"success": False, "error": "INVALID_JSON_PAYLOAD"}
            return json_response(payload, status=400) if json_response else payload
        schema = self._schema()
        changes: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for key, value in data.items():
            if key not in schema:
                errors[key] = "UNKNOWN_FIELD"
                continue
            try:
                changes[key] = self._coerce_page_value(key, value, schema[key])
            except (TypeError, ValueError):
                errors[key] = "INVALID_VALUE"
        if errors:
            payload = {"success": False, "error": "VALIDATION_FAILED", "fields": errors}
            return json_response(payload, status=400) if json_response else payload

        updated_overrides = {**self._config_overrides, **changes}

        # 先回写 AstrBot 原生配置，再落盘 overlay：baseline 必须反映回写结果，
        # 顺序颠倒会把「回写前的旧值」写进 baseline，下次启动误判为用户改了配置页。
        native_ok = False
        if self._native_config is not None:
            try:
                self._native_config.update(changes)
                self._native_config.save_config()
                native_ok = True
            except Exception:
                native_ok = False

        baseline_snapshot = dict(self._config_baseline)
        self._record_baseline(changes, native_ok)
        try:
            self._config_store_write(updated_overrides)
        except OSError as exc:
            # 落盘失败：回滚 baseline，避免内存基线与磁盘不一致
            self._config_baseline = baseline_snapshot
            payload = {
                "success": False,
                "error": "CONFIG_PERSIST_FAILED",
                "detail": str(exc) or type(exc).__name__,
            }
            return json_response(payload, status=500) if json_response else payload

        self._config_overrides = updated_overrides
        try:
            self._apply_runtime_config()
        except Exception as exc:
            payload = {
                "success": False,
                "error": "CONFIG_APPLY_FAILED",
                "detail": str(exc) or type(exc).__name__,
            }
            return json_response(payload, status=500) if json_response else payload
        payload = {
            "success": True,
            "config": self._public_config(),
            "restart_required": False,
        }
        return json_response(payload) if json_response else payload

    def _apply_runtime_config(self) -> None:
        merged = self._merged_config()
        self._mood_enabled = mood_enabled(merged)
        self._affinity_config = affinity_config(merged)
        self.manager.update_runtime_config(
            mood_enabled=self._mood_enabled,
            mood_kwargs=mood_kwargs(merged),
            affinity_config=self._affinity_config,
            trust_config=trust_config(merged),
            familiarity_config=familiarity_config(merged),
            decay_config=decay_config(merged),
            policy_config=policy_config(merged),
            save_interval_seconds=save_interval_seconds(merged),
        )
        self._apply_log_level()

    def _config_store_read(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """读取 overlay 存储，返回 (页面覆盖值, AstrBot 侧基线快照)。

        基线与覆盖值同文件保存，用 `_BASELINE_KEY` 区分，避免再引入第二个文件。
        """
        path = self._data_dir / _CONFIG_STORE_NAME
        if not path.exists():
            return {}, {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}, {}
        if not isinstance(data, dict):
            return {}, {}
        raw_baseline = data.pop(_BASELINE_KEY, None)
        baseline = dict(raw_baseline) if isinstance(raw_baseline, dict) else {}
        return data, baseline

    def _drop_stale_overrides(self) -> list[str]:
        """丢弃已被 AstrBot 插件配置页覆盖的过期页面设置。

        管理页写入的 overlay 默认优先于 AstrBot 插件配置页。但若只有这一条规则，
        用户在插件配置页改的值会被旧 overlay 永久压制——配置页显示新值、实际运行
        仍是旧值，表现为「配置页怎么改都没用」。

        写入 overlay 时会顺带记录当时 AstrBot 侧的取值（baseline）。本次启动若发现
        AstrBot 侧的值已不等于 baseline，说明用户后来在插件配置页改过它，此时丢弃
        该字段的过期 overlay，以插件配置页为准。

        返回被丢弃的字段名列表。
        """
        if not self._config_baseline:
            return []
        raw = self._raw_config if isinstance(self._raw_config, Mapping) else {}
        stale = [
            key
            for key in self._config_overrides
            if key in self._config_baseline
            and key in raw
            and raw[key] != self._config_baseline[key]
        ]
        for key in stale:
            self._config_overrides.pop(key, None)
            self._config_baseline.pop(key, None)
        if stale:
            try:
                self._config_store_write(self._config_overrides)
            except OSError:
                # 落盘失败不阻塞启动：内存中已按插件配置页取值，
                # 下次保存或启动会再尝试清理。
                self.logger.warning(
                    "[rel] 清理过期页面配置后落盘失败，本次仅在内存生效: %s", stale
                )
        return stale

    def _record_baseline(self, changes: dict[str, Any], native_ok: bool) -> None:
        """记录本次写入时 AstrBot 侧的取值，供下次启动判断谁改得更晚。"""
        raw = self._raw_config if isinstance(self._raw_config, Mapping) else {}
        for key, value in changes.items():
            if native_ok:
                # 插件配置页已同步为新值
                self._config_baseline[key] = value
            elif key in raw:
                # 回写不可用：AstrBot 侧仍是旧值，如实记录
                self._config_baseline[key] = raw[key]
            else:
                # AstrBot 侧本就没有该字段，不记基线。
                # 否则下次启动 schema 默认值一出现就会被误判成「用户改过配置页」。
                self._config_baseline.pop(key, None)

    def _config_store_write(self, value: dict[str, Any]) -> None:
        path = self._data_dir / _CONFIG_STORE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(value)
        if self._config_baseline:
            payload[_BASELINE_KEY] = dict(self._config_baseline)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # /rel 命令
    # ------------------------------------------------------------------

    @filter.command_group("rel")
    def rel_group(self):
        """凝心溯溪-情指令组。"""
        pass

    @rel_group.command("status")
    async def rel_status(self, event: AstrMessageEvent):
        """查看当前用户/会话的关系状态快照。"""
        plugin = RelationshipPlugin._current_instance or self
        scope = plugin._get_scope(event)
        if not scope.bot_id or not scope.user_id:
            yield event.plain_result("无法识别当前用户或 bot 身份。")
            return
        snapshot = await plugin.manager.get_snapshot(
            scope.bot_id, scope.user_id, scope.group_id
        )
        mood_names = {"normal": "平常", "lazy": "慵懒", "annoyed": "烦躁"}
        lines = [
            f"凝心溯溪-情 v{__version__}",
            f"当前会话: {'私聊' if scope.is_private else '群聊'}",
            f"情绪: {mood_names.get(snapshot.mood, snapshot.mood)}",
            f"回复意愿: {snapshot.willingness}/100",
            f"好感: {snapshot.affinity}/100",
            f"信任: {snapshot.trust}/100",
            f"熟悉度: {snapshot.familiarity}/100",
            f"表达风格: {snapshot.response_style or 'natural'}",
            f"本轮静默建议: {'是' if snapshot.should_silence else '否'}",
        ]
        yield event.plain_result("\n".join(lines))

    @rel_group.command("reset")
    async def rel_reset(self, event: AstrMessageEvent):
        """重置当前会话情绪与当前用户的长期关系。"""
        plugin = RelationshipPlugin._current_instance or self
        scope = plugin._get_scope(event)
        if not scope.bot_id or not scope.user_id:
            yield event.plain_result("无法识别当前用户或 bot 身份。")
            return
        await plugin.manager.reset(scope)
        yield event.plain_result("当前会话情绪与用户关系状态已重置。")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def terminate(self) -> None:
        """插件卸载时强制落盘并释放模块级实例。"""
        try:
            self.manager._flush()
        finally:
            if RelationshipPlugin._current_instance is self:
                RelationshipPlugin._current_instance = None
        self.logger.info("[relationship] 插件已卸载，长期状态已保存")

    # ------------------------------------------------------------------
    # AstrBot 兼容辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_config(config: Any) -> dict[str, Any]:
        if config is None:
            return {}
        if isinstance(config, Mapping):
            return dict(config)
        try:
            return dict(config)
        except (TypeError, ValueError):
            return {}

    def _apply_log_level(self) -> None:
        level = log_level(self._merged_config())
        if level == "DEBUG":
            import logging

            self.logger.setLevel(logging.DEBUG)

    @staticmethod
    def _get_text(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_message_str() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _safe_event_id(event: AstrMessageEvent, method: str) -> str:
        try:
            getter = getattr(event, method, None)
            if callable(getter):
                value = getter()
                return str(value) if value is not None else ""
        except Exception:
            pass
        return ""

    @classmethod
    def _get_scope(cls, event: AstrMessageEvent) -> RelationshipScope:
        bot_id = cls._safe_event_id(event, "get_self_id")
        user_id = cls._safe_event_id(event, "get_sender_id")
        group_id = cls._safe_event_id(event, "get_group_id") or None
        return RelationshipScope(bot_id=bot_id, user_id=user_id, group_id=group_id)
