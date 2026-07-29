"""Short-lived, session-scoped warmth and guardedness."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

from . import models
from .dynamics import event_strength, is_trusted_semantic_event
from .models import InteractionEvent

STANCE_NEUTRAL = "neutral"
STANCE_WARM = "warm"
STANCE_GUARDED = "guarded"

_POSITIVE_KINDS = frozenset(
    {
        models.KIND_PRAISE,
        models.KIND_HELP_RECEIVED,
        models.KIND_PROMISE_KEPT,
    }
)
_NEGATIVE_KINDS = frozenset(
    {
        models.KIND_OFFENSE,
        models.KIND_PROMISE_BROKEN,
    }
)


@dataclass(frozen=True)
class AffectConfig:
    enabled: bool = True
    half_life_seconds: float = 1800.0
    positive_gain: float = 24.0
    negative_gain: float = 32.0
    stance_threshold: float = 15.0


@dataclass(frozen=True)
class AffectDecision:
    warmth: float = 0.0
    guardedness: float = 0.0
    stance: str = STANCE_NEUTRAL


@dataclass
class _AffectState:
    warmth: float = 0.0
    guardedness: float = 0.0
    updated_at: float = 0.0
    last_seen_at: float = 0.0


def _finite_float(value: object, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return number if math.isfinite(number) else fallback


class ShortTermAffectTracker:
    """Maintain independent transient affect per user-in-session scope."""

    def __init__(
        self,
        config: AffectConfig | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config or AffectConfig()
        self._clock = clock or time.time
        self._states: dict[str, _AffectState] = {}

    @property
    def config(self) -> AffectConfig:
        return self._config

    def update_config(self, config: AffectConfig) -> None:
        """Apply configuration without rewriting active values.

        Disabling the tracker clears transient state so re-enabling cannot revive an
        old reaction.
        """

        was_enabled = bool(self._config.enabled)
        self._config = config
        if was_enabled and not config.enabled:
            self._states.clear()

    def _now(self, now: float | None) -> float:
        if now is not None:
            value = _finite_float(now, float("nan"))
            if math.isfinite(value):
                return value
        try:
            value = float(self._clock())
        except (TypeError, ValueError, OverflowError):
            return time.time()
        return value if math.isfinite(value) else time.time()

    def _half_life(self) -> float:
        value = _finite_float(
            self._config.half_life_seconds, AffectConfig.half_life_seconds
        )
        return value if value > 0.0 else AffectConfig.half_life_seconds

    @staticmethod
    def _gain(value: object, fallback: float) -> float:
        return max(0.0, _finite_float(value, fallback))

    def _decayed_values(self, state: _AffectState, now: float) -> tuple[float, float]:
        elapsed = max(0.0, now - state.updated_at)
        factor = 0.5 ** (elapsed / self._half_life())
        return state.warmth * factor, state.guardedness * factor

    def _decision(self, warmth: float, guardedness: float) -> AffectDecision:
        threshold = max(
            0.0,
            _finite_float(
                self._config.stance_threshold, AffectConfig.stance_threshold
            ),
        )
        if guardedness > 0.0 and guardedness >= threshold:
            stance = STANCE_GUARDED
        elif warmth > 0.0 and warmth >= threshold:
            stance = STANCE_WARM
        else:
            stance = STANCE_NEUTRAL
        return AffectDecision(warmth=warmth, guardedness=guardedness, stance=stance)

    def record(
        self,
        scope_key: str,
        event: InteractionEvent,
        now: float | None = None,
    ) -> AffectDecision:
        """Record one accepted event and return the resulting transient stance."""

        if not scope_key or not self._config.enabled:
            return AffectDecision()

        current = self._now(now)
        state = self._states.get(scope_key)
        reactive = (
            not event.is_command
            and is_trusted_semantic_event(event)
            and event.kind in (_POSITIVE_KINDS | _NEGATIVE_KINDS)
            and event_strength(event) > 0.0
        )
        if state is None and not reactive:
            return AffectDecision()
        if state is None:
            state = _AffectState(updated_at=current, last_seen_at=current)
            self._states[scope_key] = state

        business_now = max(current, state.updated_at)
        warmth, guardedness = self._decayed_values(state, business_now)
        strength = event_strength(event) if reactive else 0.0
        if event.kind in _POSITIVE_KINDS and reactive:
            warmth = min(
                100.0,
                warmth
                + self._gain(self._config.positive_gain, AffectConfig.positive_gain)
                * strength,
            )
        elif event.kind in _NEGATIVE_KINDS and reactive:
            guardedness = min(
                100.0,
                guardedness
                + self._gain(self._config.negative_gain, AffectConfig.negative_gain)
                * strength,
            )

        state.warmth = warmth
        state.guardedness = guardedness
        state.updated_at = business_now
        state.last_seen_at = max(state.last_seen_at, business_now)
        return self._decision(warmth, guardedness)

    def peek(self, scope_key: str, now: float | None = None) -> AffectDecision:
        """Return a decayed view without creating or refreshing state."""

        if not scope_key or not self._config.enabled:
            return AffectDecision()
        state = self._states.get(scope_key)
        if state is None:
            return AffectDecision()
        warmth, guardedness = self._decayed_values(state, self._now(now))
        return self._decision(warmth, guardedness)

    def reset(self, scope_key: str = "") -> None:
        """Reset one scope, or all transient affect when no scope is given."""

        if scope_key:
            self._states.pop(scope_key, None)
        else:
            self._states.clear()

    def cleanup_stale(
        self, ttl_seconds: float | None = None, now: float | None = None
    ) -> int:
        """Remove inactive scopes and return the number removed."""

        current = self._now(now)
        default_ttl = self._half_life() * 6.0
        ttl = (
            default_ttl
            if ttl_seconds is None
            else max(0.0, _finite_float(ttl_seconds, default_ttl))
        )
        stale = [
            key
            for key, state in self._states.items()
            if current - state.last_seen_at > ttl
        ]
        for key in stale:
            self._states.pop(key, None)
        return len(stale)
