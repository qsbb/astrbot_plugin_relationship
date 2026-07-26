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

import pathlib
import time
from typing import Any, Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .core.affinity import AffinityCalculator
from .core.config import (
    affinity_config,
    decay_config,
    familiarity_config,
    log_level,
    mood_enabled,
    mood_kwargs,
    policy_config,
    save_interval_seconds,
    trust_config,
)
from .core.familiarity import FamiliarityCalculator
from .core.manager import RelationshipStateManager
from .core.models import InteractionEvent, RelationshipScope
from .core.mood import MoodTracker
from .core.repository import JsonRepository
from .core.trust import TrustCalculator

PLUGIN_NAME = "astrbot_plugin_relationship"
__version__ = "0.2.0"


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

        data_dir = pathlib.Path(StarTools.get_data_dir(PLUGIN_NAME))
        data_dir.mkdir(parents=True, exist_ok=True)
        repository = JsonRepository(data_dir / "relationship_state.json")
        tracker = MoodTracker(**mood_kwargs(self._raw_config))
        self.manager = RelationshipStateManager(
            repository=repository,
            mood_tracker=tracker,
            affinity=AffinityCalculator(affinity_config(self._raw_config)),
            trust=TrustCalculator(trust_config(self._raw_config)),
            familiarity=FamiliarityCalculator(familiarity_config(self._raw_config)),
            decay_config=decay_config(self._raw_config),
            policy_config=policy_config(self._raw_config),
            save_interval_seconds=save_interval_seconds(self._raw_config),
            mood_enabled=mood_enabled(self._raw_config),
            logger=self.logger,
        )
        self._mood_enabled = mood_enabled(self._raw_config)
        self._apply_log_level()
        RelationshipPlugin._current_instance = self
        self.logger.info("[relationship] 凝心溯溪-情 v%s 已加载", __version__)

    # ------------------------------------------------------------------
    # LLM 请求：记录事件、给出行为建议
    # ------------------------------------------------------------------

    @filter.on_llm_request(priority=600)
    async def on_llm_request(
        self, event: AstrMessageEvent, req: Any, *args: Any, **kwargs: Any
    ) -> None:
        """记录本轮互动，并把关系表达建议注入请求。"""
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

        # 0.2.0 只提供只读建议，不接管发送或阻断事件。
        if snapshot.should_silence:
            plugin.logger.debug(
                "[relationship] 静默建议 scope=%s mood=%s willingness=%d",
                scope.session_key,
                snapshot.mood,
                snapshot.willingness,
            )

        # 不修改 req，不接管内容生成；消费方可通过 manager 显式读取结构化建议。

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
        level = log_level(self._raw_config)
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

