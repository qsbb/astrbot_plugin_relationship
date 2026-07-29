"""Focused tests for transient affect and evidence-weighted dynamics."""

from __future__ import annotations

import math

import pytest

from core import models
from core.affect import (
    STANCE_GUARDED,
    STANCE_NEUTRAL,
    STANCE_WARM,
    AffectConfig,
    ShortTermAffectTracker,
)
from core.dynamics import (
    DynamicsConfig,
    accumulate_evidence_mass,
    event_strength,
    event_weight,
)
from core.models import InteractionEvent


def _event(
    kind: str = models.KIND_MESSAGE,
    *,
    source: str = models.SOURCE_DIRECT,
    confidence: float = 1.0,
    severity: float = 1.0,
    text: str = "hello",
) -> InteractionEvent:
    return InteractionEvent(
        bot_id="bot",
        user_id="user",
        group_id=None,
        text=text,
        timestamp=1000.0,
        kind=kind,
        source=source,
        confidence=confidence,
        severity=severity,
    )


def test_event_strength_clamps_ranges_and_rejects_non_finite_values() -> None:
    assert event_strength(_event(confidence=2.0, severity=3.0)) == 1.0
    assert event_strength(_event(confidence=-1.0, severity=1.0)) == 0.0
    assert event_strength(_event(confidence=math.nan, severity=1.0)) == 0.0
    assert event_strength(_event(confidence=1.0, severity=math.inf)) == 0.0


def test_trusted_semantic_weight_is_smooth_and_ordinary_events_stay_compatible() -> None:
    semantic = _event(
        kind=models.KIND_PRAISE, confidence=0.5, severity=0.4
    )
    cfg = DynamicsConfig(early_boost=0.25, evidence_half_life=12.0)

    assert event_strength(semantic) == pytest.approx(0.2)
    assert event_weight(semantic, 0.0, cfg) == pytest.approx(0.25)
    assert event_weight(semantic, 12.0, cfg) == pytest.approx(0.225)

    weights = [event_weight(semantic, mass, cfg) for mass in (0, 6, 12, 24, 120)]
    assert weights == sorted(weights, reverse=True)
    assert all(weight > 0.2 for weight in weights)
    assert event_weight(semantic, 10_000.0, cfg) == pytest.approx(0.2)

    ordinary = _event(confidence=math.nan, severity=math.inf)
    assert event_weight(ordinary, 0.0, cfg) == 1.0


def test_untrusted_or_zero_strength_semantics_have_no_weight_or_evidence_mass() -> None:
    untrusted = _event(
        kind=models.KIND_PRAISE,
        source=models.SOURCE_PLATFORM_MESSAGE,
    )
    zero = _event(kind=models.KIND_PRAISE, confidence=0.0)

    assert event_weight(untrusted, 3.0) == 0.0
    assert accumulate_evidence_mass(3.0, untrusted) == 3.0
    assert event_weight(zero, 3.0) == 0.0
    assert accumulate_evidence_mass(3.0, zero) == 3.0

    trusted = _event(kind=models.KIND_PRAISE, confidence=0.5, severity=0.4)
    assert accumulate_evidence_mass(math.nan, trusted) == pytest.approx(0.2)


def test_positive_affect_increases_warmth_and_decays_by_half_life() -> None:
    tracker = ShortTermAffectTracker()
    decision = tracker.record("private:user", _event(models.KIND_PRAISE), now=0.0)

    assert decision.warmth == 24.0
    assert decision.guardedness == 0.0
    assert decision.stance == STANCE_WARM

    halfway = tracker.peek("private:user", now=1800.0)
    assert halfway.warmth == pytest.approx(12.0)
    assert halfway.guardedness == 0.0
    assert halfway.stance == STANCE_NEUTRAL


def test_positive_and_negative_affect_are_independent_with_guarded_priority() -> None:
    tracker = ShortTermAffectTracker(
        AffectConfig(half_life_seconds=10_000.0)
    )
    tracker.record("scope", _event(models.KIND_PRAISE), now=100.0)
    decision = tracker.record("scope", _event(models.KIND_OFFENSE), now=100.0)

    assert decision.warmth == 24.0
    assert decision.guardedness == 32.0
    assert decision.stance == STANCE_GUARDED


def test_untrusted_ordinary_command_and_non_finite_events_do_not_add_affect() -> None:
    tracker = ShortTermAffectTracker()
    assert tracker.record("scope", _event(), now=0.0).stance == STANCE_NEUTRAL
    assert tracker.record(
        "scope",
        _event(models.KIND_PRAISE, source=models.SOURCE_PLATFORM_MESSAGE),
        now=0.0,
    ).warmth == 0.0
    assert tracker.record(
        "scope",
        _event(models.KIND_COMMAND, text="/rel status"),
        now=0.0,
    ).warmth == 0.0
    assert tracker.record(
        "scope",
        _event(models.KIND_PRAISE, confidence=math.nan),
        now=0.0,
    ).warmth == 0.0
    assert tracker._states == {}


def test_scope_isolation_read_only_peek_reset_and_cleanup() -> None:
    tracker = ShortTermAffectTracker()
    tracker.record("group:one:user:a", _event(models.KIND_PRAISE), now=10.0)
    tracker.record("group:one:user:b", _event(models.KIND_OFFENSE), now=20.0)

    assert tracker.peek("group:one:user:a", now=20.0).warmth > 0.0
    assert tracker.peek("group:one:user:a", now=20.0).guardedness == 0.0
    assert tracker.peek("group:one:user:b", now=20.0).stance == STANCE_GUARDED

    before = tracker._states["group:one:user:a"].last_seen_at
    tracker.peek("group:one:user:a", now=100.0)
    assert tracker._states["group:one:user:a"].last_seen_at == before

    tracker.reset("group:one:user:b")
    assert tracker.peek("group:one:user:b", now=100.0).stance == STANCE_NEUTRAL
    assert tracker.cleanup_stale(ttl_seconds=50.0, now=100.0) == 1
    assert tracker._states == {}


def test_config_update_applies_new_gain_and_disabling_clears_state() -> None:
    tracker = ShortTermAffectTracker()
    tracker.update_config(
        AffectConfig(positive_gain=10.0, stance_threshold=5.0)
    )
    decision = tracker.record("scope", _event(models.KIND_PRAISE), now=0.0)
    assert decision.warmth == 10.0
    assert decision.stance == STANCE_WARM

    tracker.update_config(AffectConfig(enabled=False))
    assert tracker._states == {}
    assert tracker.peek("scope", now=1.0).stance == STANCE_NEUTRAL
