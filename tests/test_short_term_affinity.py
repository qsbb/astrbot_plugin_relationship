"""Regression tests for intraday affinity momentum and its safety boundaries."""

from __future__ import annotations

from core.policy import PolicyConfig, build_snapshot
from core.short_term_affinity import (
    TREND_COOLING,
    TREND_NEUTRAL,
    TREND_SETTLING,
    TREND_WARMING,
    AffinityTrendDecision,
    ShortTermAffinityConfig,
    ShortTermAffinityTracker,
)
from core.mood import MoodDecision
from core.affect import AffectDecision
from core.models import InteractionEvent, UserRelationState
from core.manager import RelationshipStateManager
from core.repository import MemoryRepository


def test_fast_positive_changes_warm_and_slow_changes_do_not() -> None:
    tracker = ShortTermAffinityTracker(
        ShortTermAffinityConfig(
            half_life_seconds=3600.0,
            daily_threshold=2.5,
            momentum_threshold=1.5,
        )
    )
    assert tracker.record("person:one", 1.5, now=100.0).style == TREND_NEUTRAL
    assert tracker.record("person:one", 1.5, now=110.0).style == TREND_WARMING

    slow = ShortTermAffinityTracker(
        ShortTermAffinityConfig(
            half_life_seconds=3600.0,
            daily_threshold=2.5,
            momentum_threshold=1.5,
        )
    )
    slow.record("person:one", 1.5, now=100.0)
    assert slow.record("person:one", 1.5, now=100.0 + 12 * 3600).style == TREND_NEUTRAL


def test_fast_negative_changes_cool_and_then_settle() -> None:
    tracker = ShortTermAffinityTracker(
        ShortTermAffinityConfig(
            half_life_seconds=3600.0,
            daily_threshold=2.5,
            momentum_threshold=1.5,
            hold_seconds=7200.0,
        )
    )
    assert tracker.record("person:one", -2.0, now=100.0).style == TREND_NEUTRAL
    assert tracker.record("person:one", -2.0, now=110.0).style == TREND_COOLING
    assert tracker.peek("person:one", now=110.0 + 5400).style == TREND_SETTLING
    assert tracker.peek("person:one", now=110.0 + 4 * 3600).style == TREND_NEUTRAL


def test_day_rollover_clears_daily_accumulation_and_scope_isolation() -> None:
    tracker = ShortTermAffinityTracker(
        ShortTermAffinityConfig(daily_threshold=2.0, momentum_threshold=1.0)
    )
    tracker.record("person:one", 2.0, now=0.0)
    assert tracker.record("person:one", 1.0, now=1.0).style == TREND_WARMING
    assert tracker.peek("account:other", now=1.0).style == TREND_NEUTRAL
    next_day = 24 * 3600 + 1.0
    assert tracker.peek("person:one", now=next_day).style == TREND_NEUTRAL


def test_disabled_tracker_is_memory_only_and_resettable() -> None:
    tracker = ShortTermAffinityTracker(ShortTermAffinityConfig(enabled=False))
    assert tracker.record("scope", 10.0, now=1.0).style == TREND_NEUTRAL
    assert tracker._states == {}
    tracker.update_config(ShortTermAffinityConfig(enabled=True))
    tracker.record("scope", 2.0, now=1.0)
    tracker.reset("scope")
    assert tracker.peek("scope", now=2.0).style == TREND_NEUTRAL


def test_policy_trend_changes_only_expression_advice() -> None:
    state = UserRelationState()
    mood = MoodDecision()
    affect = AffectDecision()
    warm = build_snapshot(
        mood,
        state,
        PolicyConfig(),
        affect,
        AffinityTrendDecision(TREND_WARMING, 2.0, 3.0),
    )
    cool = build_snapshot(
        mood,
        state,
        PolicyConfig(),
        affect,
        AffinityTrendDecision(TREND_COOLING, -2.0, -3.0),
    )
    assert warm.behavior.tone == "warm_attentive"
    assert warm.behavior.initiative == "high"
    assert cool.behavior.tone == "cool_polite"
    assert cool.behavior.initiative == "low"
    assert warm.affinity == cool.affinity == 50
    assert warm.should_silence is cool.should_silence is False
    assert "\u6700\u8fd1" in warm.prompt_fragment
    assert "\u964d\u6e29" in cool.prompt_fragment


def test_manager_uses_applied_affinity_delta_for_recent_style():
    import asyncio

    async def run():
        manager = RelationshipStateManager(
            repository=MemoryRepository(), save_interval_seconds=0.0
        )
        snapshots = []
        for index, timestamp in enumerate((1000.0, 1010.0), 1):
            snapshots.append(
                await manager.record(
                    InteractionEvent(
                        bot_id="bot",
                        user_id="user",
                        group_id=None,
                        text="",
                        timestamp=timestamp,
                        kind="praise",
                        event_id=f"praise-{index}",
                        source="direct",
                        confidence=1.0,
                        severity=1.0,
                    )
                )
            )
        return snapshots

    first, second = asyncio.run(run())
    assert first.behavior.initiative == "low"
    assert second.behavior.initiative == "high"
    assert second.affinity > first.affinity
    assert second.should_silence is False
