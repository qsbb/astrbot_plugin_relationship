"""分维度信任计算器。

本模块只消费带来源证据的已发生事实，不登记、追踪或兑现承诺。
普通聊天和不可信来源不会修改信任。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import models
from .models import DimensionDelta, InteractionEvent, UserRelationState


@dataclass(frozen=True)
class TrustConfig:
    """信任计算配置。"""

    promise_kept_gain: float = 3.0
    promise_broken_penalty: float = -5.0
    offense_penalty: float = -1.0


class TrustCalculator:
    """将可信语义事件映射到独立信任维度。"""

    def __init__(self, config: TrustConfig | None = None) -> None:
        self._config = config or TrustConfig()

    @property
    def config(self) -> TrustConfig:
        return self._config

    def update_config(self, config: TrustConfig) -> None:
        self._config = config

    def compute(
        self, event: InteractionEvent, state: UserRelationState
    ) -> DimensionDelta:
        del state
        if event.source not in models.TRUSTED_SEMANTIC_SOURCES:
            return DimensionDelta(reason="来源不足，信任不变")

        weight = max(0.0, min(1.0, event.confidence)) * max(0.0, event.severity)
        if event.kind == models.KIND_PROMISE_KEPT:
            value = self._config.promise_kept_gain * weight
            return DimensionDelta(
                trust_reliability=value,
                trust_integrity=value,
                reason="经证据确认的履约事实",
            )
        if event.kind == models.KIND_PROMISE_BROKEN:
            value = self._config.promise_broken_penalty * weight
            return DimensionDelta(
                trust_reliability=value,
                trust_integrity=value,
                reason="经证据确认的失约事实",
            )
        if event.kind == models.KIND_OFFENSE:
            value = self._config.offense_penalty * weight
            return DimensionDelta(
                trust_benevolence=value,
                reason="可信冒犯事件",
            )
        if event.kind == models.KIND_HELP_RECEIVED:
            value = min(1.0, self._config.promise_kept_gain / 3.0) * weight
            return DimensionDelta(
                trust_benevolence=value,
                trust_epistemic=value,
                reason="可信帮助事实",
            )
        return DimensionDelta(reason="非信任相关事件")
