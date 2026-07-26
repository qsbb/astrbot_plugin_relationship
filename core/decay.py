"""时间衰减与恢复。

只处理长期维度的慢速回归：
- 好感：向中位数（50）缓慢回归，天级半衰节奏，防止历史分数永久固化；
- 信任：同样向中位数回归，但速率更慢（长期维度更稳定）；
- 熟悉度：单调维度，不衰减。

短期情绪的恢复由 mood.py 的滑动窗口自然完成，不在此处理。
衰减在事件到达时惰性结算（lazy evaluation），不需要后台定时任务。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import SCORE_BASELINE, UserRelationState

_DAY_SECONDS = 86400.0


@dataclass(frozen=True)
class DecayConfig:
    """衰减配置。速率单位：每天向中位数移动的比例。"""

    affinity_regression_per_day: float = 0.02  # 每天回归 2% 的偏离量
    trust_regression_per_day: float = 0.005  # 信任更稳定
    max_idle_days: float = 365.0  # 单次结算最多按一年计，防止时间异常


def _regress(value: float, rate_per_day: float, days: float) -> float:
    """把 value 向 SCORE_BASELINE 回归。

    使用指数式回归：偏离量 * (1 - rate)^days，
    保证多次小步结算与一次大步结算结果一致（时间可加性）。
    """
    if days <= 0.0 or rate_per_day <= 0.0:
        return value
    keep = (1.0 - min(1.0, rate_per_day)) ** days
    return SCORE_BASELINE + (value - SCORE_BASELINE) * keep


def apply_decay(
    state: UserRelationState, now: float, config: DecayConfig | None = None
) -> UserRelationState:
    """按距上次事件的时间对 state 就地结算衰减，返回同一对象。

    ``last_event_at`` 为 0.0 也视为合法时间起点；新状态处于基线值时
    回归不产生任何变化，因此无需特殊跳过。
    """
    cfg = config or DecayConfig()
    if state.last_event_at < 0.0:
        return state
    elapsed_days = (now - state.last_event_at) / _DAY_SECONDS
    if elapsed_days <= 0.0:
        return state
    elapsed_days = min(elapsed_days, cfg.max_idle_days)
    state.affinity_score = _regress(
        state.affinity_score, cfg.affinity_regression_per_day, elapsed_days
    )
    for field_name in (
        "trust_reliability",
        "trust_benevolence",
        "trust_integrity",
        "trust_epistemic",
    ):
        setattr(
            state,
            field_name,
            _regress(
                getattr(state, field_name),
                cfg.trust_regression_per_day,
                elapsed_days,
            ),
        )
    state.refresh_trust_score()
    # 熟悉度不衰减。
    return state
