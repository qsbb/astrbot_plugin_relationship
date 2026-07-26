"""关系状态管理器：事件账本、幂等、双层情绪与长期状态合并。

对外契约保持为 ``record / get_snapshot / reset``。本模块只记录事实、计算状态并
返回只读建议；不发送消息、不授予权限，也不登记或执行承诺工作流。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime
from typing import Any, Callable

from .affinity import AffinityCalculator
from .decay import DecayConfig, apply_decay
from .familiarity import FamiliarityCalculator
from .models import (
    HIGH_TRUST_EVENT_SOURCES,
    KIND_PROMISE_BROKEN,
    KIND_PROMISE_KEPT,
    KNOWN_KINDS,
    SCORE_MAX,
    SCORE_MIN,
    SOURCE_PLATFORM_MESSAGE,
    TRUSTED_SEMANTIC_SOURCES,
    InteractionEvent,
    RelationshipEventRecord,
    RelationshipScope,
    RelationshipSnapshot,
    UserRelationState,
)
from .mood import MoodDecision, MoodTracker
from .policy import PolicyConfig, build_snapshot
from .repository import MemoryRepository, RelationshipRepository
from .trust import TrustCalculator


def _day_of(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


class RelationshipStateManager:
    """关系状态域唯一对外入口。"""

    def __init__(
        self,
        repository: RelationshipRepository | None = None,
        mood_tracker: MoodTracker | None = None,
        affinity: AffinityCalculator | None = None,
        trust: TrustCalculator | None = None,
        familiarity: FamiliarityCalculator | None = None,
        decay_config: DecayConfig | None = None,
        policy_config: PolicyConfig | None = None,
        clock: Callable[[], float] | None = None,
        save_interval_seconds: float = 30.0,
        mood_enabled: bool = True,
        event_ledger_limit: int = 2000,
        logger: Any = None,
    ) -> None:
        self._repo = repository or MemoryRepository()
        self._mood = mood_tracker or MoodTracker()
        self._affinity = affinity or AffinityCalculator()
        self._trust = trust or TrustCalculator()
        self._familiarity = familiarity or FamiliarityCalculator()
        self._decay_config = decay_config or DecayConfig()
        self._policy_config = policy_config or PolicyConfig()
        self._clock = clock or time.time
        self._save_interval = max(0.0, float(save_interval_seconds))
        self._mood_enabled = bool(mood_enabled)
        self._ledger_limit = max(100, int(event_ledger_limit))
        self._logger = logger

        self._states = self._repo.load_all()
        load_events = getattr(self._repo, "load_events", None)
        self._events = list(load_events()) if callable(load_events) else []
        self._dedupe_keys = {
            record.dedupe_key or record.event_id
            for record in self._events
            if record.dedupe_key or record.event_id
        }
        self._dirty = False
        self._last_save_at = 0.0
        self._lock = asyncio.Lock()

    async def record(self, event: InteractionEvent) -> RelationshipSnapshot:
        """幂等记录事件并返回快照；账本永不保存消息正文。"""
        async with self._lock:
            now = float(event.timestamp) if event.timestamp else self._clock()
            scope = event.scope
            event_id, dedupe_key = self._event_identity(event, now)
            if dedupe_key in self._dedupe_keys:
                return self._snapshot(scope, now)

            applied, reason = self._validate_event(event)
            self._append_event(event, event_id, dedupe_key, now, applied, reason)
            if not applied:
                self._dirty = True
                self._maybe_save(now)
                return self._snapshot(scope, now)

            decision = self._record_mood(event, scope, now)
            state = self._states.setdefault(scope.user_key, UserRelationState())
            apply_decay(state, now, self._decay_config)
            self._apply_deltas(event, state, now)
            state.last_event_at = now
            if not event.is_command:
                state.interaction_count += 1
            self._dirty = True
            self._maybe_save(now)
            snapshot = build_snapshot(decision, state, self._policy_config)
            if self._logger is not None:
                self._logger.debug(
                    "[relationship] record event=%s kind=%s applied=%s affinity=%d trust=%d",
                    event_id,
                    event.kind,
                    applied,
                    snapshot.affinity,
                    snapshot.trust,
                )
            return snapshot

    async def get_snapshot(
        self, bot_id: str, user_id: str, group_id: str | None
    ) -> RelationshipSnapshot:
        """只读查询，不新增事件、不改变状态。"""
        async with self._lock:
            return self._snapshot(RelationshipScope(bot_id, user_id, group_id), self._clock())

    async def reset(self, scope: RelationshipScope) -> None:
        """重置当前会话的双层情绪及该用户长期关系。"""
        async with self._lock:
            self._mood.reset(scope.session_key)
            self._mood.reset(scope.pressure_key)
            self._states.pop(scope.user_key, None)
            self._dirty = True
            self._save()

    def _flush(self) -> None:
        if self._dirty:
            self._save()

    def _cleanup_stale_sessions(self, ttl_seconds: float | None = None) -> int:
        return self._mood.cleanup_stale(ttl_seconds)

    def _snapshot(self, scope: RelationshipScope, now: float) -> RelationshipSnapshot:
        decision = self._combined_peek(scope, now)
        state = self._states.get(scope.user_key)
        if state is None:
            state = UserRelationState()
        else:
            state = UserRelationState.from_dict(state.as_dict())
            apply_decay(state, now, self._decay_config)
        return build_snapshot(decision, state, self._policy_config)

    def _record_mood(
        self, event: InteractionEvent, scope: RelationshipScope, now: float
    ) -> MoodDecision:
        if event.is_command or not self._mood_enabled:
            return MoodDecision() if not self._mood_enabled else self._combined_peek(scope, now)
        session = self._mood.evaluate(scope.session_key, event.text, now=now)
        pressure = self._mood.evaluate(scope.pressure_key, event.text, now=now)
        return self._combine_mood(session, pressure)

    def _combined_peek(self, scope: RelationshipScope, now: float) -> MoodDecision:
        if not self._mood_enabled:
            return MoodDecision()
        return self._combine_mood(
            self._mood.peek(scope.session_key, now=now),
            self._mood.peek(scope.pressure_key, now=now),
        )

    @staticmethod
    def _combine_mood(session: MoodDecision, pressure: MoodDecision) -> MoodDecision:
        chosen = session if session.willingness <= pressure.willingness else pressure
        return MoodDecision(
            mood=chosen.mood,
            willingness=min(session.willingness, pressure.willingness),
            should_silence=session.should_silence or pressure.should_silence,
            reason=f"会话疲劳：{session.reason}；用户压力：{pressure.reason}",
            interaction_count=session.interaction_count,
            repeat_count=max(session.repeat_count, pressure.repeat_count),
            streak_count=max(session.streak_count, pressure.streak_count),
        )

    @staticmethod
    def _event_identity(event: InteractionEvent, now: float) -> tuple[str, str]:
        event_id = event.event_id.strip()
        if not event_id:
            material = "|".join(
                (event.bot_id, event.user_id, event.group_id or "", event.kind, str(now), event.text)
            )
            event_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        return event_id, event.dedupe_key.strip() or event_id

    @staticmethod
    def _validate_event(event: InteractionEvent) -> tuple[bool, str]:
        if event.kind not in KNOWN_KINDS:
            return False, "unknown_kind"
        if event.is_semantic and event.source not in TRUSTED_SEMANTIC_SOURCES:
            return False, "untrusted_semantic_source"
        if event.kind in {KIND_PROMISE_KEPT, KIND_PROMISE_BROKEN}:
            if event.source not in HIGH_TRUST_EVENT_SOURCES:
                return False, "insufficient_promise_evidence_source"
            if event.source != "direct" and not event.evidence_refs:
                return False, "missing_evidence_refs"
        if event.source == SOURCE_PLATFORM_MESSAGE and event.is_semantic:
            return False, "platform_text_cannot_assert_semantics"
        return True, ""

    def _append_event(
        self,
        event: InteractionEvent,
        event_id: str,
        dedupe_key: str,
        now: float,
        applied: bool,
        reason: str,
    ) -> None:
        self._events.append(
            RelationshipEventRecord(
                event_id=event_id,
                timestamp=now,
                bot_id=event.bot_id,
                user_id=event.user_id,
                group_id=event.group_id,
                kind=event.kind,
                source=event.source,
                confidence=max(0.0, min(1.0, event.confidence)),
                severity=max(0.0, event.severity),
                dedupe_key=dedupe_key,
                evidence_refs=tuple(event.evidence_refs),
                applied=applied,
                rejection_reason=reason,
            )
        )
        self._dedupe_keys.add(dedupe_key)
        if len(self._events) > self._ledger_limit:
            self._events = self._events[-self._ledger_limit :]
            self._dedupe_keys = {
                item.dedupe_key or item.event_id for item in self._events
            }

    def _apply_deltas(
        self, event: InteractionEvent, state: UserRelationState, now: float
    ) -> None:
        affinity_delta = self._affinity.compute(event, state)
        trust_delta = self._trust.compute(event, state)
        familiarity_delta = self._familiarity.compute(event, state)

        today = _day_of(now)
        if state.daily_anchor_day != today:
            state.daily_anchor_day = today
            state.daily_affinity_positive_used = 0.0
            state.daily_affinity_negative_used = 0.0
        want = affinity_delta.affinity
        if want:
            cap = max(
                0.0,
                self._affinity.config.daily_cap
                if want > 0
                else self._affinity.config.daily_negative_cap,
            )
            used_name = (
                "daily_affinity_positive_used" if want > 0 else "daily_affinity_negative_used"
            )
            remaining = max(0.0, cap - getattr(state, used_name))
            applied = min(abs(want), remaining) * (1.0 if want > 0 else -1.0)
            setattr(state, used_name, getattr(state, used_name) + abs(applied))
            state.affinity_score = self._clamp_float(state.affinity_score + applied)

        for field_name in (
            "trust_reliability",
            "trust_benevolence",
            "trust_integrity",
            "trust_epistemic",
        ):
            delta = getattr(trust_delta, field_name)
            if delta:
                setattr(state, field_name, self._clamp_float(getattr(state, field_name) + delta))
        state.refresh_trust_score()

        gain = max(0.0, familiarity_delta.familiarity)
        if gain:
            state.familiarity_score = self._clamp_float(state.familiarity_score + gain)

    @staticmethod
    def _clamp_float(value: float) -> float:
        return max(float(SCORE_MIN), min(float(SCORE_MAX), value))

    def _maybe_save(self, now: float) -> None:
        if self._dirty and (
            self._save_interval <= 0.0 or now - self._last_save_at >= self._save_interval
        ):
            self._save()
            self._last_save_at = now

    def _save(self) -> None:
        try:
            save = getattr(self._repo, "save", None)
            if callable(save):
                save(self._states, self._events)
            else:
                self._repo.save_all(self._states)  # type: ignore[attr-defined]
            self._dirty = False
        except OSError as exc:  # pragma: no cover
            if self._logger is not None:
                self._logger.warning("[relationship] 持久化失败: %s", exc)
