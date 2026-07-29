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
    whitelist_user_ids: tuple[str, ...] = ()
    high_affinity_threshold: float = 75.0
    non_whitelist_ceiling: float = 68.0
    whitelist_trust_gate: float = 65.0
    whitelist_familiarity_gate: float = 25.0


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

    def _limit_positive_gain(
        self, event: InteractionEvent, state: UserRelationState, value: float
    ) -> tuple[float, str]:
        cfg = self._config
        is_whitelisted = event.relationship_user_id in cfg.whitelist_user_ids
        if not is_whitelisted:
            remaining = max(0.0, cfg.non_whitelist_ceiling - state.affinity_score)
            return min(value, remaining), "非白名单语义事件受朋友区上限约束"
        trust_ready = state.trust_score >= cfg.whitelist_trust_gate
        familiarity_ready = state.familiarity_score >= cfg.whitelist_familiarity_gate
        if state.affinity_score >= cfg.high_affinity_threshold:
            return 0.0, "已处于高好感区，正向语义事件不再刷分"
        if not (trust_ready and familiarity_ready):
            return min(value, 0.1), "白名单语义事件仍需通过信任与熟悉度门槛"
        return value, "白名单语义事件通过关系门槛"

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
            if value > 0:
                value, reason = self._limit_positive_gain(event, state, value)
                return DimensionDelta(affinity=value, reason=reason)
            return DimensionDelta(affinity=value, reason=f"事件 {event.kind}")

        # 普通消息 / @：只给微小加成，且有冷却，避免刷屏刷好感。
        if state.last_event_at and (
            event.timestamp - state.last_event_at < cfg.message_cooldown_seconds
        ):
            return DimensionDelta(reason="普通消息处于加成冷却期")

        # 好感不是“聊得越多越高”的积分。非白名单用户保留在朋友区间，
        # 只有白名单且信任、熟悉度都达标，才允许跨入高好感区。
        is_whitelisted = event.relationship_user_id in cfg.whitelist_user_ids
        if not is_whitelisted:
            remaining = max(0.0, cfg.non_whitelist_ceiling - state.affinity_score)
            if remaining <= 0:
                return DimensionDelta(reason="非白名单关系已达到朋友区上限")
            return DimensionDelta(
                affinity=min(cfg.message_gain, remaining),
                reason="非白名单用户仅在朋友区间缓慢累积",
            )

        trust_ready = state.trust_score >= cfg.whitelist_trust_gate
        familiarity_ready = state.familiarity_score >= cfg.whitelist_familiarity_gate
        if state.affinity_score >= cfg.high_affinity_threshold:
            return DimensionDelta(reason="已处于高好感区，普通互动不再刷分")
        if not (trust_ready and familiarity_ready):
            return DimensionDelta(
                affinity=min(cfg.message_gain, 0.1),
                reason="白名单仍需通过信任与熟悉度门槛",
            )
        return DimensionDelta(affinity=cfg.message_gain, reason="白名单关系稳定累积")
