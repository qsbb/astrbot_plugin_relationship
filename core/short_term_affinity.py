"""Transient, intraday affinity momentum.

This layer models the human tendency for recent relationship-relevant events to
colour the next few interactions. It is deliberately memory-only and never
changes the persisted affinity score.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable


TREND_NEUTRAL = "neutral"
TREND_WARMING = "warming_up"
TREND_COOLING = "cooling_down"
TREND_SETTLING = "settling"


@dataclass(frozen=True)
class ShortTermAffinityConfig:
    enabled: bool = True
    half_life_seconds: float = 14400.0
    daily_threshold: float = 2.5
    momentum_threshold: float = 1.5
    hold_seconds: float = 7200.0


@dataclass(frozen=True)
class AffinityTrendDecision:
    style: str = TREND_NEUTRAL
    momentum: float = 0.0
    daily_net_delta: float = 0.0


@dataclass
class _TrendState:
    day_key: str
    daily_net_delta: float = 0.0
    positive_delta: float = 0.0
    negative_delta: float = 0.0
    momentum: float = 0.0
    last_event_at: float = -1.0
    last_direction: str = ""
    last_trend_at: float = -1.0


def _finite(value: object, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return number if math.isfinite(number) else fallback


def _day_key(now: float) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


class ShortTermAffinityTracker:
    """Track recent, person-scoped affinity changes without persistence."""

    def __init__(
        self,
        config: ShortTermAffinityConfig | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config or ShortTermAffinityConfig()
        self._clock = clock or time.time
        self._states: dict[str, _TrendState] = {}

    @property
    def config(self) -> ShortTermAffinityConfig:
        return self._config

    def update_config(self, config: ShortTermAffinityConfig) -> None:
        was_enabled = self._config.enabled
        self._config = config
        if was_enabled and not config.enabled:
            self._states.clear()

    def _now(self, now: float | None) -> float:
        value = (
            _finite(now, float("nan"))
            if now is not None
            else _finite(self._clock(), float("nan"))
        )
        return value if math.isfinite(value) else time.time()

    def _half_life(self) -> float:
        value = _finite(self._config.half_life_seconds, 14400.0)
        return value if value > 0 else 14400.0

    def _threshold(self, name: str, fallback: float) -> float:
        return max(0.0, _finite(getattr(self._config, name), fallback))

    def _hold_seconds(self) -> float:
        return max(0.0, _finite(self._config.hold_seconds, 7200.0))

    def _roll_day(self, state: _TrendState, now: float) -> None:
        current_day = _day_key(now)
        if state.day_key != current_day:
            state.day_key = current_day
            state.daily_net_delta = 0.0
            state.positive_delta = 0.0
            state.negative_delta = 0.0
            state.momentum = 0.0
            state.last_event_at = -1.0
            state.last_direction = ""
            state.last_trend_at = -1.0

    def _decayed_momentum(self, state: _TrendState, now: float) -> float:
        if state.last_event_at < 0.0:
            return 0.0
        elapsed = max(0.0, now - state.last_event_at)
        return state.momentum * 0.5 ** (elapsed / self._half_life())

    def _decision(self, state: _TrendState, now: float) -> AffinityTrendDecision:
        momentum_threshold = self._threshold("momentum_threshold", 1.5)
        daily_threshold = self._threshold("daily_threshold", 2.5)
        momentum = self._decayed_momentum(state, now)
        daily = state.daily_net_delta
        style = TREND_NEUTRAL
        if (
            daily >= daily_threshold
            and momentum > momentum_threshold
            and state.last_direction == "positive"
        ):
            style = TREND_WARMING
        elif (
            daily <= -daily_threshold
            and momentum < -momentum_threshold
            and state.last_direction == "negative"
        ):
            style = TREND_COOLING
        elif (
            state.last_trend_at >= 0.0
            and now - state.last_trend_at <= self._hold_seconds()
            and abs(momentum) >= momentum_threshold * 0.35
        ):
            style = TREND_SETTLING
        return AffinityTrendDecision(style, momentum, daily)

    def record(
        self, scope_key: str, delta: float, now: float | None = None
    ) -> AffinityTrendDecision:
        if not scope_key or not self._config.enabled:
            return AffinityTrendDecision()
        current = self._now(now)
        state = self._states.get(scope_key)
        if state is None:
            state = _TrendState(day_key=_day_key(current))
            self._states[scope_key] = state
        self._roll_day(state, current)
        if (
            state.last_event_at >= 0.0
            and current - state.last_event_at > self._half_life()
        ):
            momentum = 0.0
        else:
            momentum = self._decayed_momentum(state, current)
        change = _finite(delta)
        if change:
            state.daily_net_delta += change
            if change > 0:
                state.positive_delta += change
                state.last_direction = "positive"
            else:
                state.negative_delta += abs(change)
                state.last_direction = "negative"
            state.momentum = momentum + change
            state.last_event_at = current
            threshold = self._threshold("daily_threshold", 2.5)
            if abs(state.daily_net_delta) >= threshold and abs(
                state.momentum
            ) > self._threshold("momentum_threshold", 1.5):
                state.last_trend_at = current
        return self._decision(state, current)

    def peek(self, scope_key: str, now: float | None = None) -> AffinityTrendDecision:
        if not scope_key or not self._config.enabled:
            return AffinityTrendDecision()
        current = self._now(now)
        state = self._states.get(scope_key)
        if state is None:
            return AffinityTrendDecision()
        self._roll_day(state, current)
        return self._decision(state, current)

    def reset(self, scope_key: str = "") -> None:
        if scope_key:
            self._states.pop(scope_key, None)
        else:
            self._states.clear()

    def cleanup_stale(
        self, ttl_seconds: float | None = None, now: float | None = None
    ) -> int:
        current = self._now(now)
        ttl = (
            self._half_life() * 6.0
            if ttl_seconds is None
            else max(0.0, _finite(ttl_seconds, self._half_life() * 6.0))
        )
        stale = [
            key
            for key, state in self._states.items()
            if state.last_event_at >= 0.0 and current - state.last_event_at > ttl
        ]
        for key in stale:
            self._states.pop(key, None)
        return len(stale)
