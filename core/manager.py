"""关系状态管理器：事件账本、幂等、双层情绪与长期状态合并。

对外契约保持为 ``record / get_snapshot / reset``。本模块只记录事实、计算状态并
返回只读建议；不发送消息、不授予权限，也不登记或执行承诺工作流。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from datetime import datetime
from typing import Any, Callable

from .affinity import AffinityCalculator, AffinityConfig
from .decay import DecayConfig, apply_decay
from .familiarity import FamiliarityCalculator, FamiliarityConfig
from .followup import FollowupConfig, FollowupDecision, FollowupGuard
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
from .trust import TrustCalculator, TrustConfig

MAX_EVENT_FUTURE_SKEW_SECONDS = 300.0


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
        followup_config: FollowupConfig | None = None,
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
        self._followup = FollowupGuard(
            followup_config or FollowupConfig(), clock=self._clock
        )
        self._save_interval = max(0.0, float(save_interval_seconds))
        self._mood_enabled = bool(mood_enabled)
        self._ledger_limit = max(100, int(event_ledger_limit))
        self._logger = logger

        self._states = self._repo.load_all()
        load_events = getattr(self._repo, "load_events", None)
        self._events = list(load_events()) if callable(load_events) else []
        self._dedupe_keys = set()
        for record in self._events:
            self._dedupe_keys.update(self._record_dedupe_aliases(record))
        self._dirty = False
        self._last_save_at = 0.0
        self._lock = asyncio.Lock()

    async def record(self, event: InteractionEvent) -> RelationshipSnapshot:
        """幂等记录事件并返回快照；账本永不保存消息正文。"""
        async with self._lock:
            clock_now = self._safe_clock()
            now = self._safe_event_timestamp(event.timestamp, clock_now)
            scope = event.scope
            state = self._states.get(scope.user_key)
            business_now = max(now, state.last_event_at if state else 0.0)
            if event.is_command:
                decision = self._combined_peek(scope, business_now)
                return self._snapshot(scope, business_now, decision=decision)
            event_id, dedupe_key = self._event_identity(event, now)
            if self._identity_seen(event, event_id, dedupe_key):
                return self._snapshot(scope, business_now)

            normalized_event = self._event_with_timestamp(event, business_now)
            applied, reason = self._validate_event(normalized_event)
            self._append_event(
                normalized_event, event_id, dedupe_key, business_now, applied, reason
            )
            if not applied:
                self._dirty = True
                self._maybe_save(business_now)
                return self._snapshot(scope, business_now)

            decision = self._record_mood(normalized_event, scope, business_now)
            state = self._states.setdefault(scope.user_key, UserRelationState())
            if normalized_event.is_command:
                return self._snapshot(scope, business_now, decision=decision)
            apply_decay(state, business_now, self._decay_config)
            self._apply_deltas(normalized_event, state, business_now)
            state.last_event_at = max(state.last_event_at, business_now)
            state.interaction_count += 1
            self._dirty = True
            self._maybe_save(business_now)
            snapshot = build_snapshot(
                decision,
                state,
                self._policy_config,
                followup=self._followup.peek(scope.pressure_key),
            )
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
            return self._snapshot(
                RelationshipScope(bot_id, user_id, group_id), self._safe_clock()
            )

    async def reset(self, scope: RelationshipScope) -> None:
        """重置当前会话的双层情绪及该用户长期关系。"""
        async with self._lock:
            self._mood.reset(scope.session_key)
            self._mood.reset(scope.pressure_key)
            self._followup.reset(scope.pressure_key)
            self._states.pop(scope.user_key, None)
            self._dirty = True
            self._save()

    async def record_bot_reply(
        self, scope: RelationshipScope, text: str
    ) -> FollowupDecision:
        """记录 bot 本轮实际回复，统计服务式追问收尾的连续轮次。

        只统计文本特征，不保存回复正文；非追问收尾会清零连续计数。
        """
        async with self._lock:
            return self._followup.record_reply(scope.pressure_key, text or "")

    async def followup_state(self, scope: RelationshipScope) -> FollowupDecision:
        """只读查询当前追问抑制档位。"""
        async with self._lock:
            return self._followup.peek(scope.pressure_key)

    def followup_stats(self, scope: RelationshipScope) -> dict[str, object]:
        """同步读取追问统计，供命令与页面自检使用。"""
        return self._followup.stats(scope.pressure_key)

    def update_runtime_config(
        self,
        *,
        mood_enabled: bool | None = None,
        mood_kwargs: dict[str, int] | None = None,
        affinity_config: AffinityConfig | None = None,
        trust_config: TrustConfig | None = None,
        familiarity_config: FamiliarityConfig | None = None,
        decay_config: DecayConfig | None = None,
        policy_config: PolicyConfig | None = None,
        followup_config: FollowupConfig | None = None,
        save_interval_seconds: float | None = None,
    ) -> None:
        """热应用配置变更：各计算器保留已有状态并自然收敛到新阈值。"""
        if mood_enabled is not None:
            self._mood_enabled = bool(mood_enabled)
        if mood_kwargs is not None:
            self._mood.update_config(**mood_kwargs)
        if affinity_config is not None:
            self._affinity.update_config(affinity_config)
        if trust_config is not None:
            self._trust.update_config(trust_config)
        if familiarity_config is not None:
            self._familiarity.update_config(familiarity_config)
        if decay_config is not None:
            self._decay_config = decay_config
        if policy_config is not None:
            self._policy_config = policy_config
        if followup_config is not None:
            self._followup.update_config(followup_config)
        if save_interval_seconds is not None:
            self._save_interval = max(0.0, float(save_interval_seconds))

    def _flush(self) -> None:
        if self._dirty:
            self._save()

    def _cleanup_stale_sessions(self, ttl_seconds: float | None = None) -> int:
        return self._mood.cleanup_stale(ttl_seconds)

    def _snapshot(
        self,
        scope: RelationshipScope,
        now: float,
        decision: MoodDecision | None = None,
    ) -> RelationshipSnapshot:
        decision = decision or self._combined_peek(scope, now)
        state = self._states.get(scope.user_key)
        if state is None:
            state = UserRelationState()
        else:
            state = UserRelationState.from_dict(state.as_dict())
            apply_decay(state, now, self._decay_config)
        return build_snapshot(
            decision,
            state,
            self._policy_config,
            followup=self._followup.peek(scope.pressure_key),
        )

    def _record_mood(
        self, event: InteractionEvent, scope: RelationshipScope, now: float
    ) -> MoodDecision:
        if event.is_command or not self._mood_enabled:
            return (
                MoodDecision()
                if not self._mood_enabled
                else self._combined_peek(scope, now)
            )
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

    def _safe_clock(self) -> float:
        try:
            value = float(self._clock())
        except (TypeError, ValueError, OverflowError):
            return time.time()
        return value if math.isfinite(value) else time.time()

    @staticmethod
    def _safe_event_timestamp(timestamp: float, now: float) -> float:
        try:
            value = float(timestamp)
        except (TypeError, ValueError, OverflowError):
            return now
        if (
            not math.isfinite(value)
            or value <= 0
            or value > now + MAX_EVENT_FUTURE_SKEW_SECONDS
        ):
            return now
        return value

    @staticmethod
    def _event_with_timestamp(
        event: InteractionEvent, timestamp: float
    ) -> InteractionEvent:
        return InteractionEvent(
            bot_id=event.bot_id,
            user_id=event.user_id,
            group_id=event.group_id,
            text=event.text,
            timestamp=timestamp,
            kind=event.kind,
            event_id=event.event_id,
            source=event.source,
            confidence=event.confidence,
            severity=event.severity,
            dedupe_key=event.dedupe_key,
            evidence_refs=event.evidence_refs,
        )

    @staticmethod
    def _record_dedupe_aliases(record: RelationshipEventRecord) -> set[str]:
        aliases = {record.dedupe_key or record.event_id}
        namespace = (
            f"{record.bot_id}\x1f{record.user_id}\x1f{record.group_id or ''}\x1f"
        )
        raw_id = record.event_id
        if raw_id.startswith(namespace):
            raw_id = raw_id[len(namespace) :]
        raw_dedupe = record.dedupe_key
        if raw_dedupe.startswith(namespace):
            raw_dedupe = raw_dedupe[len(namespace) :]
        if raw_id:
            aliases.add(raw_id)
            aliases.add(namespace + raw_id)
        if raw_dedupe:
            aliases.add(raw_dedupe)
            aliases.add(namespace + raw_dedupe)
        return {value for value in aliases if value}

    def _identity_seen(
        self, event: InteractionEvent, event_id: str, dedupe_key: str
    ) -> bool:
        namespace = f"{event.bot_id}\x1f{event.user_id}\x1f{event.group_id or ''}\x1f"
        raw_id = event.event_id.strip()
        raw_dedupe = event.dedupe_key.strip()
        for record in self._events:
            record_namespace = (
                f"{record.bot_id}\x1f{record.user_id}\x1f{record.group_id or ''}\x1f"
            )
            if dedupe_key == record.dedupe_key or event_id == record.event_id:
                return True
            # v2 账本使用未命名空间的 ID；仅在同一关系作用域内兼容匹配，
            # 避免旧账本中的跨用户同名键阻塞新用户事件。
            if record_namespace == namespace and (
                raw_id
                in {record.event_id, record.event_id.removeprefix(record_namespace)}
                or raw_dedupe
                in {record.dedupe_key, record.dedupe_key.removeprefix(record_namespace)}
            ):
                return True
        return False

    @staticmethod
    def _event_identity(event: InteractionEvent, now: float) -> tuple[str, str]:
        namespace = f"{event.bot_id}\x1f{event.user_id}\x1f{event.group_id or ''}\x1f"
        event_id = event.event_id.strip()
        if not event_id:
            material = "|".join((namespace, event.kind, str(now), event.text))
            event_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        else:
            event_id = namespace + event_id
        dedupe = event.dedupe_key.strip()
        return event_id, namespace + (dedupe or event_id)

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
        self._dedupe_keys.update(self._record_dedupe_aliases(self._events[-1]))
        if len(self._events) > self._ledger_limit:
            self._events = self._events[-self._ledger_limit :]
            self._dedupe_keys = set()
            for item in self._events:
                self._dedupe_keys.update(self._record_dedupe_aliases(item))

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
                "daily_affinity_positive_used"
                if want > 0
                else "daily_affinity_negative_used"
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
                setattr(
                    state,
                    field_name,
                    self._clamp_float(getattr(state, field_name) + delta),
                )
        state.refresh_trust_score()

        gain = max(0.0, familiarity_delta.familiarity)
        if gain:
            state.familiarity_score = self._clamp_float(state.familiarity_score + gain)

    @staticmethod
    def _clamp_float(value: float) -> float:
        return max(float(SCORE_MIN), min(float(SCORE_MAX), value))

    def _maybe_save(self, now: float) -> None:
        if self._dirty and (
            self._save_interval <= 0.0
            or now - self._last_save_at >= self._save_interval
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
