"""熟悉度计算器：长期单调累积，增速随熟悉度提高而递减。

设计取舍：
- 熟悉度只增不减（共同经历不会消失），衰减模块也不回收它；
- 采用「递减增益」：增量 = base_gain * (1 - familiarity/100)^curve，
  熟悉度越高，同样的互动带来的增长越少，避免线性刷满；
- 与好感相同的冷却窗口，连续刷屏只算一次有效互动。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import SCORE_MAX, DimensionDelta, InteractionEvent, UserRelationState


@dataclass(frozen=True)
class FamiliarityConfig:
    """熟悉度计算配置。"""

    base_gain: float = 0.8  # 熟悉度为 0 时单次有效互动的增量
    diminish_curve: float = 2.0  # 递减曲线指数，越大后期增长越慢
    cooldown_seconds: float = 120.0  # 有效互动最小间隔


class FamiliarityCalculator:
    """随有效互动单调增长熟悉度。"""

    def __init__(self, config: FamiliarityConfig | None = None) -> None:
        self._config = config or FamiliarityConfig()

    @property
    def config(self) -> FamiliarityConfig:
        return self._config

    def update_config(self, config: FamiliarityConfig) -> None:
        self._config = config

    def compute(
        self, event: InteractionEvent, state: UserRelationState
    ) -> DimensionDelta:
        """返回本事件的熟悉度增量（永远 >= 0）。"""
        if event.is_command:
            return DimensionDelta(reason="命令消息不计入熟悉度")
        cfg = self._config
        if state.last_event_at and (
            event.timestamp - state.last_event_at < cfg.cooldown_seconds
        ):
            return DimensionDelta(reason="有效互动冷却期内")

        ratio = max(0.0, 1.0 - state.familiarity_score / float(SCORE_MAX))
        gain = cfg.base_gain * (ratio ** cfg.diminish_curve)
        if gain <= 0.0:
            return DimensionDelta(reason="熟悉度已达上限")
        return DimensionDelta(familiarity=gain, reason="有效互动累积")
