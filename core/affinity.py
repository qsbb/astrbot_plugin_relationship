"""长期好感计算器。

设计取舍：
- 好感是「天~周级」慢变量，单事件变化量很小（±0.5~2 分）；
- 计算器只返回期望变化量，每日变化上限与向中位数回归由
  Manager/decay 统一执行，保持「计算器只算增量」的隔离原则；
- 情绪（mood）不作为输入：短期烦躁不等于讨厌用户（维度隔离）。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import models
from .models import DimensionDelta, InteractionEvent, UserRelationState


@dataclass(frozen=True)
class AffinityConfig:
    """好感计算配置。所有值可被 _conf_schema.json 覆盖。"""

    message_gain: float = 0.2  # 普通有效消息的微小加成
    praise_gain: float = 1.5  # 被夸奖/正向互动
    help_received_gain: float = 2.0  # bot 得到用户帮助（如纠错、补充信息）
    offense_penalty: float = -2.0  # 被冒犯/辱骂
    daily_cap: float = 5.0  # 每日好感正向变化额度（兼容字段名）
    daily_negative_cap: float = 5.0  # 每日好感负向变化额度（绝对值）
    message_cooldown_seconds: float = 60.0


_EVENT_GAIN_KEYS = {
    models.KIND_PRAISE: "praise_gain",
    models.KIND_HELP_RECEIVED: "help_received_gain",
    models.KIND_OFFENSE: "offense_penalty",
}


class AffinityCalculator:
    """基于事件类型缓慢增减好感。"""

    def __init__(self, config: AffinityConfig | None = None) -> None:
        self._config = config or AffinityConfig()

    @property
    def config(self) -> AffinityConfig:
        return self._config

    def update_config(self, config: AffinityConfig) -> None:
        self._config = config

    def compute(
        self, event: InteractionEvent, state: UserRelationState
    ) -> DimensionDelta:
        """返回本事件期望的好感变化量（未做每日限幅）。"""
        if event.is_command:
            return DimensionDelta(reason="命令消息不参与好感累积")

        cfg = self._config
        gain_key = _EVENT_GAIN_KEYS.get(event.kind)
        if gain_key is not None:
            value = float(getattr(cfg, gain_key))
            return DimensionDelta(affinity=value, reason=f"事件 {event.kind}")

        # 普通消息 / @：只给微小加成，且有冷却，避免刷屏刷好感。
        if state.last_event_at and (
            event.timestamp - state.last_event_at < cfg.message_cooldown_seconds
        ):
            return DimensionDelta(reason="普通消息处于加成冷却期")
        return DimensionDelta(affinity=cfg.message_gain, reason="正常互动")
