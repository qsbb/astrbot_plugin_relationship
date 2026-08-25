"""关系状态管理器：事件账本、幂等、双层情绪与长期状态合并。

对外契约保持为 ``record / get_snapshot / reset``。本模块只记录事实、计算状态并
返回只读建议；不发送消息、不授予权限，也不登记或执行承诺工作流。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
import uuid
from datetime import datetime
from typing import Any, Callable

from .affect import AffectConfig, AffectDecision, ShortTermAffectTracker
from .affinity import AffinityCalculator, AffinityConfig
from .decay import DecayConfig, apply_decay
from .dynamics import (
    EVIDENCE_MASS_KEY,
    DynamicsConfig,
    accumulate_evidence_mass,
    event_strength,
    event_weight,
)
from .familiarity import FamiliarityCalculator, FamiliarityConfig
from .models import (
    HIGH_TRUST_EVENT_SOURCES,
    KIND_INITIAL_PRIOR,
    KIND_PROMISE_BROKEN,
    KIND_PROMISE_KEPT,
    KNOWN_KINDS,
    SCORE_MAX,
    SCORE_MIN,
    SOURCE_ADMIN,
    SOURCE_PLATFORM_MESSAGE,
    TRUSTED_SEMANTIC_SOURCES,
    InteractionEvent,
    GroupRelationState,
    GroupRelationshipAdvice,
    RelationshipEventRecord,
    RelationshipScope,
    RelationshipSnapshot,
    UserRelationState,
)
from .mood import MoodDecision, MoodTracker
from .policy import PolicyConfig, build_snapshot
from .short_term_affinity import (
    AffinityTrendDecision,
    ShortTermAffinityConfig,
    ShortTermAffinityTracker,
)
from .profiles import parse_state_key, validate_profile_id
from .repository import MemoryRepository, RelationshipRepository
from .trust import TrustCalculator, TrustConfig

MAX_EVENT_FUTURE_SKEW_SECONDS = 300.0

INITIAL_RELATIONSHIP_PRIORS: dict[str, tuple[float, float, float]] = {
    "neutral": (50.0, 50.0, 0.0),
    "acquainted": (56.0, 55.0, 15.0),
    "fond": (64.0, 60.0, 25.0),
}


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
        affect_tracker: ShortTermAffectTracker | None = None,
        affect_config: AffectConfig | None = None,
        affinity_trend_tracker: ShortTermAffinityTracker | None = None,
        affinity_trend_config: ShortTermAffinityConfig | None = None,
        dynamics_config: DynamicsConfig | None = None,
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
        self._affect = affect_tracker or ShortTermAffectTracker(affect_config)
        self._affinity_trend = affinity_trend_tracker or ShortTermAffinityTracker(
            affinity_trend_config
        )
        self._dynamics_config = dynamics_config or DynamicsConfig()
        self._clock = clock or time.time
        self._save_interval = max(0.0, float(save_interval_seconds))
        self._mood_enabled = bool(mood_enabled)
        self._ledger_limit = max(100, int(event_ledger_limit))
        self._logger = logger

        loaded_states = self._repo.load_all()
        self._states = {
            key: (
                GroupRelationState.from_dict(value.as_dict())
                if (parsed := parse_state_key(key))
                and parsed.get("kind") == "group"
                else value
            )
            for key, value in loaded_states.items()
        }
        load_events = getattr(self._repo, "load_events", None)
        self._events = list(load_events()) if callable(load_events) else []
        self._dedupe_keys = set()
        for record in self._events:
            self._dedupe_keys.update(self._record_dedupe_aliases(record))
        self._dirty = False
        self._last_save_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def persistence_write_blocked(self) -> bool:
        """Whether the repository is protecting data this version cannot write."""
        return bool(getattr(self._repo, "write_blocked", False))

    async def record(self, event: InteractionEvent) -> RelationshipSnapshot:
        """幂等记录事件并返回快照；账本永不保存消息正文。"""
        async with self._lock:
            clock_now = self._safe_clock()
            now = self._safe_event_timestamp(event.timestamp, clock_now)
            scope = event.scope
            state = self._materialize_bound_state(scope)
            business_now = max(now, state.last_event_at if state else 0.0)
            if event.is_command:
                decision = self._combined_peek(scope, business_now)
                self._maybe_save(business_now)
                return self._snapshot(scope, business_now, decision=decision)
            event_id, dedupe_key = self._event_identity(event, now)
            if self._identity_seen(event, event_id, dedupe_key):
                self._maybe_save(business_now)
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
            affect = self._affect.record(
                scope.pressure_key, normalized_event, now=business_now
            )
            state = self._states.setdefault(scope.user_key, UserRelationState())
            if normalized_event.is_command:
                return self._snapshot(scope, business_now, decision=decision)
            apply_decay(state, business_now, self._decay_config)
            applied_affinity = self._apply_deltas(normalized_event, state, business_now)
            affinity_trend = self._affinity_trend.record(
                self._affinity_trend_scope_key(scope),
                applied_affinity,
                now=business_now,
            )
            state.last_event_at = max(state.last_event_at, business_now)
            state.interaction_count += 1
            if scope.group_key:
                group_state = self._states.setdefault(
                    scope.group_key, GroupRelationState()
                )
                apply_decay(group_state, business_now, self._decay_config)
                group_applied_affinity = self._apply_deltas(
                    normalized_event,
                    group_state,
                    business_now,
                    enforce_user_gates=False,
                )
                self._affinity_trend.record(
                    scope.group_key,
                    group_applied_affinity,
                    now=business_now,
                )
                group_state.last_event_at = max(
                    group_state.last_event_at, business_now
                )
                group_state.interaction_count += 1
            self._dirty = True
            self._maybe_save(business_now)
            snapshot = build_snapshot(
                decision,
                state,
                self._policy_config,
                affect,
                affinity_trend,
            )
            self._attach_group_advice(snapshot, scope, business_now)
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
        self,
        bot_id: str,
        user_id: str,
        group_id: str | None,
        *,
        relationship_profile_id: str = "default",
        person_id: str = "",
        state_alias_keys: tuple[str, ...] = (),
    ) -> RelationshipSnapshot:
        """只读查询，不新增事件、不改变状态。"""
        async with self._lock:
            return self._snapshot(
                RelationshipScope(
                    bot_id,
                    user_id,
                    group_id,
                    person_id,
                    state_alias_keys,
                    relationship_profile_id,
                ),
                self._safe_clock(),
            )

    async def get_snapshot_for_scope(
        self, scope: RelationshipScope
    ) -> RelationshipSnapshot:
        """Profile-aware read-only query used by the AstrBot adapter."""
        async with self._lock:
            return self._snapshot(scope, self._safe_clock())

    async def set_relationship_type(
        self, scope: RelationshipScope, relationship_type: str
    ) -> None:
        """显式标记当前用户的关系性质（friend/close_friend/lover/exclusive）。

        只影响「情」注入的表达约束（是否放行恋人级亲密表达），
        不授予任何权限，也不改变好感/信任/熟悉度分数。
        """
        normalized = (relationship_type or "friend").strip() or "friend"
        async with self._lock:
            state = self._states.setdefault(scope.user_key, UserRelationState())
            state.relationship_type = normalized
            self._dirty = True
            self._save()

    async def reset(self, scope: RelationshipScope) -> None:
        """重置当前会话的双层情绪及该用户长期关系。"""
        async with self._lock:
            self._mood.reset(scope.session_key)
            self._mood.reset(scope.pressure_key)
            self._affect.reset(scope.pressure_key)
            self._affinity_trend.reset(self._affinity_trend_scope_key(scope))
            previous = self._states.pop(scope.user_key, None)
            for alias_key in scope.state_alias_keys:
                self._states.pop(alias_key, None)
            if previous is not None and previous.initial_prior_applied_at > 0:
                self._states[scope.user_key] = UserRelationState(
                    initial_prior=previous.initial_prior,
                    initial_prior_applied_at=previous.initial_prior_applied_at,
                )
            self._dirty = True
            self._save()

    async def bind_identity(
        self, relationship_key: str, alias_state_keys: tuple[str, ...]
    ) -> bool:
        """Move account states once into the canonical person key."""
        changed = await self.bind_identities(((relationship_key, alias_state_keys),))
        return bool(changed)

    async def bind_identities(
        self,
        bindings: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> tuple[str, ...]:
        """Atomically consume account states for one or more profile bindings."""
        async with self._lock:
            states_before = {
                key: UserRelationState.from_dict(value.as_dict())
                for key, value in self._states.items()
            }
            dirty_before = self._dirty
            try:
                changed = tuple(
                    relationship_key
                    for relationship_key, alias_state_keys in bindings
                    if self._bind_identity_unlocked(
                        relationship_key, alias_state_keys
                    )
                )
                if not changed:
                    return ()
                self._dirty = True
                self._save(raise_errors=True)
            except Exception:
                self._states = states_before
                self._dirty = dirty_before
                raise
            return changed

    async def merge_identity_states(
        self,
        bindings: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> tuple[str, ...]:
        """Explicitly merge verified account/person states into target persons."""
        async with self._lock:
            states_before = {
                key: UserRelationState.from_dict(value.as_dict())
                for key, value in self._states.items()
            }
            dirty_before = self._dirty
            try:
                changed = tuple(
                    relationship_key
                    for relationship_key, source_state_keys in bindings
                    if self._merge_identity_states_unlocked(
                        relationship_key, source_state_keys
                    )
                )
                if not changed:
                    return ()
                self._dirty = True
                self._save(raise_errors=True)
            except Exception:
                self._states = states_before
                self._dirty = dirty_before
                raise
            return changed

    async def unbind_identity_states(
        self,
        bindings: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        """Atomically move person states back to one explicitly selected account."""
        async with self._lock:
            states_before = {
                key: UserRelationState.from_dict(value.as_dict())
                for key, value in self._states.items()
            }
            dirty_before = self._dirty
            try:
                changed = tuple(
                    target_key
                    for target_key, source_key in bindings
                    if self._unbind_identity_state_unlocked(target_key, source_key)
                )
                if not changed:
                    return ()
                self._dirty = True
                self._save(raise_errors=True)
            except Exception:
                self._states = states_before
                self._dirty = dirty_before
                raise
            return changed

    async def validate_identity_unbind_states(
        self,
        bindings: tuple[tuple[str, str], ...],
    ) -> None:
        """Reject invalid or conflicting unbinds before identity data is removed."""
        async with self._lock:
            for target_key, source_key in bindings:
                self._validate_identity_unbind_state_unlocked(
                    target_key,
                    source_key,
                )

    async def delete_relationship_states(
        self, state_keys: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Delete exact long-term relationship states without touching policy lists."""
        async with self._lock:
            states_before = {
                key: UserRelationState.from_dict(value.as_dict())
                for key, value in self._states.items()
            }
            dirty_before = self._dirty
            try:
                deleted = tuple(
                    key
                    for value in state_keys
                    if (key := str(value or "").strip())
                    and parse_state_key(key) is not None
                    and self._states.pop(key, None) is not None
                )
                if not deleted:
                    return ()
                self._dirty = True
                self._save(raise_errors=True)
            except Exception:
                self._states = states_before
                self._dirty = dirty_before
                raise
            return deleted

    def recover_identity_merge_states(
        self,
        bindings: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> tuple[str, ...]:
        """Finish a durable merge intent during single-threaded plugin startup."""
        states_before = {
            key: UserRelationState.from_dict(value.as_dict())
            for key, value in self._states.items()
        }
        dirty_before = self._dirty
        try:
            changed = tuple(
                relationship_key
                for relationship_key, source_state_keys in bindings
                if self._merge_identity_states_unlocked(
                    relationship_key, source_state_keys
                )
            )
            if not changed:
                return ()
            self._dirty = True
            self._save(raise_errors=True)
        except Exception:
            self._states = states_before
            self._dirty = dirty_before
            raise
        return changed

    def recover_identity_unbind_states(
        self,
        bindings: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        """Finish a durable unbind intent during single-threaded plugin startup."""
        states_before = {
            key: UserRelationState.from_dict(value.as_dict())
            for key, value in self._states.items()
        }
        dirty_before = self._dirty
        try:
            changed = tuple(
                target_key
                for target_key, source_key in bindings
                if self._unbind_identity_state_unlocked(target_key, source_key)
            )
            if not changed:
                return ()
            self._dirty = True
            self._save(raise_errors=True)
        except Exception:
            self._states = states_before
            self._dirty = dirty_before
            raise
        return changed

    def _unbind_identity_state_unlocked(
        self, target_account_key: str, source_person_key: str
    ) -> bool:
        source_state = self._validate_identity_unbind_state_unlocked(
            target_account_key,
            source_person_key,
        )
        if source_state is None:
            return False
        self._states[target_account_key] = UserRelationState.from_dict(
            source_state.as_dict()
        )
        self._states.pop(source_person_key, None)
        return True

    def _validate_identity_unbind_state_unlocked(
        self, target_account_key: str, source_person_key: str
    ) -> UserRelationState | None:
        target_account_key = str(target_account_key or "").strip()
        source_person_key = str(source_person_key or "").strip()
        target = parse_state_key(target_account_key)
        source = parse_state_key(source_person_key)
        if (
            not target
            or target.get("kind") != "account"
            or not source
            or source.get("kind") != "person"
            or target.get("profile_id") != source.get("profile_id")
        ):
            raise ValueError("INVALID_UNBIND_STATE_BINDING")
        source_state = self._states.get(source_person_key)
        if source_state is None:
            return False
        target_state = self._states.get(target_account_key)
        if (
            target_state is not None
            and target_state.as_dict() != source_state.as_dict()
        ):
            raise ValueError("RESTORE_ACCOUNT_STATE_CONFLICT")
        return source_state

    def _merge_identity_states_unlocked(
        self, relationship_key: str, source_state_keys: tuple[str, ...]
    ) -> bool:
        relationship_key = str(relationship_key or "").strip()
        parsed = parse_state_key(relationship_key)
        if not parsed or parsed.get("kind") != "person":
            return False
        profile_id = parsed["profile_id"]
        sources = tuple(
            dict.fromkeys(
                key
                for value in source_state_keys
                if (key := str(value or "").strip()) and key != relationship_key
            )
        )
        sources = tuple(
            key
            for key in sources
            if (candidate := parse_state_key(key))
            and candidate.get("kind") in {"account", "person"}
            and candidate.get("profile_id") == profile_id
        )
        present_sources = tuple(key for key in sources if key in self._states)
        if not present_sources:
            return False

        candidates: list[UserRelationState] = []
        canonical = self._states.get(relationship_key)
        if canonical is not None:
            candidates.append(canonical)
        source_fingerprints: set[str] = set()
        for key in present_sources:
            state = self._states.get(key)
            if state is None:
                continue
            fingerprint = repr(state.as_dict())
            if fingerprint in source_fingerprints:
                continue
            source_fingerprints.add(fingerprint)
            candidates.append(state)
        if not candidates:
            return False
        self._states[relationship_key] = self._merge_states(
            candidates, additive_counters=True
        )
        for key in present_sources:
            self._states.pop(key, None)
        return True

    def _bind_identity_unlocked(
        self, relationship_key: str, alias_state_keys: tuple[str, ...]
    ) -> bool:
        relationship_key = str(relationship_key or "").strip()
        aliases = tuple(
            dict.fromkeys(
                str(value or "").strip() for value in alias_state_keys if value
            )
        )
        parsed = parse_state_key(relationship_key)
        if not parsed or parsed.get("kind") != "person" or not aliases:
            return False
        profile_id = parsed["profile_id"]
        aliases = tuple(
            key
            for key in aliases
            if (candidate := parse_state_key(key))
            and candidate.get("kind") == "account"
            and candidate.get("profile_id") == profile_id
        )
        if not aliases:
            return False
        canonical = self._states.get(relationship_key)
        if canonical is not None:
            removed = False
            for key in aliases:
                removed = self._states.pop(key, None) is not None or removed
            return removed
        candidates: list[UserRelationState] = []
        fingerprints: set[str] = set()
        for key in aliases:
            state = self._states.get(key)
            if state is None:
                continue
            fingerprint = repr(state.as_dict())
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            candidates.append(state)
        if not candidates:
            return False
        merged = self._merge_states(candidates)
        self._states[relationship_key] = merged
        for key in aliases:
            self._states.pop(key, None)
        return True

    async def apply_initial_prior(
        self,
        scope: RelationshipScope,
        prior: str,
        *,
        allow_active_relationship: bool = False,
        allow_whitelist_reapply: bool = False,
    ) -> RelationshipSnapshot:
        """Apply or, for a verified whitelist, replace a fixed admin prior.

        ``allow_active_relationship`` is reserved for an administrator-verified
        whitelist exception. It changes the three relationship scores while
        preserving interaction history and evidence metadata.

        ``allow_whitelist_reapply`` is a separate whitelist-only correction
        path. It replaces the current fixed prior without resetting interaction
        counters, daily limits, or evidence, and appends a new audit record.
        Ordinary callers remain one-shot and cannot use this path by default.
        """
        prior = str(prior or "").strip().lower()
        if prior not in INITIAL_RELATIONSHIP_PRIORS:
            raise ValueError("INVALID_INITIAL_PRIOR")
        if not scope.person_id:
            raise ValueError("NATURAL_PERSON_REQUIRED")
        async with self._lock:
            states_before = {
                key: UserRelationState.from_dict(value.as_dict())
                for key, value in self._states.items()
            }
            events_before = list(self._events)
            dedupe_before = set(self._dedupe_keys)
            dirty_before = self._dirty
            try:
                prior_record = max(
                    (
                        record
                        for record in self._events
                        if record.applied
                        and record.kind == KIND_INITIAL_PRIOR
                        and record.scope_key == scope.user_key
                    ),
                    key=lambda record: record.timestamp,
                    default=None,
                )
                state = self._materialize_bound_state(scope)
                prior_applied_at = max(
                    float(state.initial_prior_applied_at) if state is not None else 0.0,
                    float(prior_record.timestamp) if prior_record is not None else 0.0,
                )
                prior_already_applied = prior_applied_at > 0.0
                if prior_applied_at > 0.0 and not allow_whitelist_reapply:
                    raise ValueError("INITIAL_PRIOR_ALREADY_APPLIED")
                if (
                    prior_already_applied
                    and allow_whitelist_reapply
                    and state is not None
                    and state.initial_prior == prior
                ):
                    return self._snapshot(scope, self._safe_clock())
                relationship_active = state is not None and (
                    state.interaction_count > 0
                    or state.last_event_at > 0
                )
                if relationship_active and not allow_active_relationship:
                    raise ValueError("RELATIONSHIP_ALREADY_ACTIVE")

                now = self._safe_clock()
                affinity, trust, familiarity = INITIAL_RELATIONSHIP_PRIORS[prior]
                if state is None:
                    state = UserRelationState()
                state.affinity_score = affinity
                state.trust_score = trust
                state.trust_reliability = trust
                state.trust_benevolence = trust
                state.trust_integrity = trust
                state.trust_epistemic = trust
                state.familiarity_score = familiarity
                state.initial_prior = prior
                state.initial_prior_applied_at = max(prior_applied_at, now)
                self._states[scope.user_key] = state
                for alias_key in scope.state_alias_keys:
                    self._states.pop(alias_key, None)
                revision = prior_already_applied
                audit_id = (
                    f"initial-prior-revision:{scope.user_key}:{uuid.uuid4().hex}"
                    if revision
                    else f"initial-prior:{scope.user_key}"
                )
                audit_reason = (
                    f"admin_initial_prior_revision:{prior}"
                    if revision
                    else f"admin_initial_prior:{prior}"
                )
                event = InteractionEvent(
                    bot_id=scope.bot_id,
                    user_id=scope.user_id,
                    group_id=scope.group_id,
                    text="",
                    timestamp=now,
                    kind=KIND_INITIAL_PRIOR,
                    event_id=audit_id,
                    source=SOURCE_ADMIN,
                    confidence=1.0,
                    severity=1.0,
                    dedupe_key=audit_id,
                    person_id=scope.person_id,
                    state_alias_keys=scope.state_alias_keys,
                    relationship_profile_id=scope.relationship_profile_id,
                    whitelist_alias_ids=scope.whitelist_alias_ids,
                )
                event_id, dedupe_key = self._event_identity(event, now)
                self._append_event(
                    event,
                    event_id,
                    dedupe_key,
                    now,
                    True,
                    audit_reason,
                )
                self._dirty = True
                self._save(raise_errors=True)
            except Exception:
                self._states = states_before
                self._events = events_before
                self._dedupe_keys = dedupe_before
                self._dirty = dirty_before
                raise
            return self._snapshot(scope, now)

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
        affect_config: AffectConfig | None = None,
        affinity_trend_config: ShortTermAffinityConfig | None = None,
        dynamics_config: DynamicsConfig | None = None,
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
        if affect_config is not None:
            self._affect.update_config(affect_config)
        if affinity_trend_config is not None:
            self._affinity_trend.update_config(affinity_trend_config)
        if dynamics_config is not None:
            self._dynamics_config = dynamics_config
        if save_interval_seconds is not None:
            self._save_interval = max(0.0, float(save_interval_seconds))

    def _flush(self) -> None:
        if self._dirty:
            self._save()

    def _cleanup_stale_sessions(self, ttl_seconds: float | None = None) -> int:
        return (
            self._mood.cleanup_stale(ttl_seconds)
            + self._affect.cleanup_stale(ttl_seconds)
            + self._affinity_trend.cleanup_stale(ttl_seconds)
        )

    def _snapshot(
        self,
        scope: RelationshipScope,
        now: float,
        decision: MoodDecision | None = None,
        affect: AffectDecision | None = None,
        affinity_trend: AffinityTrendDecision | None = None,
    ) -> RelationshipSnapshot:
        decision = decision or self._combined_peek(scope, now)
        state = self._states.get(scope.user_key)
        if state is None:
            state = UserRelationState()
        else:
            state = UserRelationState.from_dict(state.as_dict())
            apply_decay(state, now, self._decay_config)
        snapshot = build_snapshot(
            decision,
            state,
            self._policy_config,
            affect or self._affect.peek(scope.pressure_key, now=now),
            affinity_trend
            or self._affinity_trend.peek(
                self._affinity_trend_scope_key(scope), now=now
            ),
        )
        self._attach_group_advice(snapshot, scope, now)
        return snapshot

    def get_group_state(self, scope: RelationshipScope) -> GroupRelationState:
        """Return a defensive copy of the independent group state."""
        if not scope.group_key:
            return GroupRelationState()
        state = self._states.get(scope.group_key)
        if state is None:
            return GroupRelationState()
        return GroupRelationState.from_dict(state.as_dict())

    async def get_group_snapshot(
        self,
        bot_id: str,
        group_id: str,
        *,
        relationship_profile_id: str = "default",
    ) -> GroupRelationshipAdvice:
        """Read only group relationship snapshot for permission/policy adapters."""
        async with self._lock:
            scope = RelationshipScope(
                bot_id=str(bot_id or ""),
                user_id="",
                group_id=str(group_id or "") or None,
                relationship_profile_id=relationship_profile_id,
            )
            snapshot = RelationshipSnapshot()
            self._attach_group_advice(snapshot, scope, self._safe_clock())
            return snapshot.group or GroupRelationshipAdvice()

    def _attach_group_advice(
        self, snapshot: RelationshipSnapshot, scope: RelationshipScope, now: float
    ) -> None:
        if not scope.group_key:
            return
        raw = self._states.get(scope.group_key)
        group_state = (
            GroupRelationState.from_dict(raw.as_dict())
            if raw is not None
            else GroupRelationState()
        )
        apply_decay(group_state, now, self._decay_config)
        group_snapshot = build_snapshot(
            MoodDecision(),
            group_state,
            self._policy_config,
            AffectDecision(),
            self._affinity_trend.peek(scope.group_key, now=now),
        )
        tier = self._group_tier(group_snapshot)
        snapshot.group = GroupRelationshipAdvice(
            affinity=group_snapshot.affinity,
            trust=group_snapshot.trust,
            familiarity=group_snapshot.familiarity,
            tier=tier,
            behavior=group_snapshot.behavior,
            prompt_fragment=self._group_prompt_fragment(tier, group_snapshot),
        )

    @staticmethod
    def _group_tier(snapshot: RelationshipSnapshot) -> str:
        affinity, trust, familiarity = (
            int(snapshot.affinity),
            int(snapshot.trust),
            int(snapshot.familiarity),
        )
        if affinity < 35 or trust < 35:
            return "guarded"
        if affinity >= 80 and trust >= 75 and familiarity >= 60:
            return "inner_circle"
        if affinity >= 65 and trust >= 60 and familiarity >= 30:
            return "close"
        if familiarity >= 20 or (affinity + trust) / 2 >= 55:
            return "familiar"
        return "neutral"

    @staticmethod
    def _group_prompt_fragment(
        tier: str, snapshot: RelationshipSnapshot
    ) -> str:
        if tier == "guarded":
            return "这个群的整体关系较谨慎；公开回复应优先清晰、克制并尊重群规。"
        if tier == "inner_circle":
            return "这个群的整体关系较熟悉；可以自然承接群聊氛围，但仍尊重每位成员和群规。"
        if tier == "close":
            return "这个群的整体关系较友好；可以自然参与话题，不越过成员边界。"
        if tier == "familiar":
            return "你对这个群已有一定熟悉度；保持自然、友好并遵守群规。"
        return "这是一个普通群聊；保持礼貌、清晰，不因群体氛围擅自承诺或越权。"

    @staticmethod
    def _affinity_trend_scope_key(scope: RelationshipScope) -> str:
        """Keep public-group momentum separate from private continuity."""
        if not scope.group_id:
            return scope.user_key
        return f"{scope.user_key}:group:{scope.group_id}"

    def _record_mood(
        self, event: InteractionEvent, scope: RelationshipScope, now: float
    ) -> MoodDecision:
        if (
            event.is_command
            or event.is_semantic
            or not self._mood_enabled
        ):
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
            person_id=event.person_id,
            state_alias_keys=event.state_alias_keys,
            relationship_profile_id=event.relationship_profile_id,
            whitelist_alias_ids=event.whitelist_alias_ids,
        )

    def _materialize_bound_state(
        self, scope: RelationshipScope
    ) -> UserRelationState | None:
        """Consume same-profile account states without maintaining live mirrors."""
        canonical = self._states.get(scope.user_key)
        if not scope.person_id:
            return canonical
        aliases = tuple(
            key
            for key in dict.fromkeys(scope.state_alias_keys)
            if (parsed := parse_state_key(key))
            and parsed.get("kind") == "account"
            and parsed.get("profile_id") == scope.relationship_profile_id
        )
        if canonical is not None:
            removed = False
            for key in aliases:
                removed = self._states.pop(key, None) is not None or removed
            self._dirty = self._dirty or removed
            return canonical

        candidates: list[UserRelationState] = []
        fingerprints: set[str] = set()
        for key in aliases:
            state = self._states.get(key)
            if state is None:
                continue
            fingerprint = repr(state.as_dict())
            if fingerprint not in fingerprints:
                fingerprints.add(fingerprint)
                candidates.append(state)
        if not candidates:
            return None
        canonical = self._merge_states(candidates)
        self._states[scope.user_key] = canonical
        for key in aliases:
            self._states.pop(key, None)
        self._dirty = True
        return canonical

    @staticmethod
    def _merge_states(
        states: list[UserRelationState], *, additive_counters: bool = False
    ) -> UserRelationState:
        weights = [max(1, state.interaction_count) for state in states]
        total_weight = float(sum(weights))

        def weighted(field: str) -> float:
            return sum(
                float(getattr(state, field)) * weight
                for state, weight in zip(states, weights)
            ) / total_weight

        latest = max(states, key=lambda state: state.last_event_at)
        prior_state = max(
            (state for state in states if state.initial_prior_applied_at > 0),
            key=lambda state: state.initial_prior_applied_at,
            default=None,
        )
        same_day_states = [
            state for state in states if state.daily_anchor_day == latest.daily_anchor_day
        ]
        daily_positive = (
            sum(state.daily_affinity_positive_used for state in same_day_states)
            if additive_counters
            else max(state.daily_affinity_positive_used for state in states)
        )
        daily_negative = (
            sum(state.daily_affinity_negative_used for state in same_day_states)
            if additive_counters
            else max(state.daily_affinity_negative_used for state in states)
        )
        merged = UserRelationState(
            affinity_score=weighted("affinity_score"),
            trust_reliability=weighted("trust_reliability"),
            trust_benevolence=weighted("trust_benevolence"),
            trust_integrity=weighted("trust_integrity"),
            trust_epistemic=weighted("trust_epistemic"),
            familiarity_score=weighted("familiarity_score"),
            daily_affinity_positive_used=daily_positive,
            daily_affinity_negative_used=daily_negative,
            daily_anchor_day=latest.daily_anchor_day,
            interaction_count=sum(state.interaction_count for state in states),
            last_event_at=latest.last_event_at,
            extra=dict(latest.extra),
            initial_prior=prior_state.initial_prior if prior_state else "",
            initial_prior_applied_at=(
                prior_state.initial_prior_applied_at if prior_state else 0.0
            ),
        )
        masses: list[float] = []
        for state in states:
            try:
                value = float(state.extra.get(EVIDENCE_MASS_KEY, 0.0))
            except (TypeError, ValueError, OverflowError):
                value = 0.0
            masses.append(value if math.isfinite(value) and value > 0.0 else 0.0)
        merged.extra[EVIDENCE_MASS_KEY] = (
            sum(masses) if additive_counters else max(masses, default=0.0)
        )
        merged.refresh_trust_score()
        return merged

    @staticmethod
    def _event_namespace(
        profile_id: str, bot_id: str, user_id: str, group_id: str | None
    ) -> str:
        return (
            f"{profile_id}\x1f{bot_id}\x1f{user_id}\x1f{group_id or ''}\x1f"
        )

    @staticmethod
    def _record_dedupe_aliases(record: RelationshipEventRecord) -> set[str]:
        aliases = {record.dedupe_key or record.event_id}
        namespace = RelationshipStateManager._event_namespace(
            record.relationship_profile_id,
            record.bot_id,
            record.user_id,
            record.group_id,
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
        namespace = self._event_namespace(
            event.relationship_profile_id,
            event.bot_id,
            event.user_id,
            event.group_id,
        )
        raw_id = event.event_id.strip()
        raw_dedupe = event.dedupe_key.strip()
        for record in self._events:
            record_namespace = self._event_namespace(
                record.relationship_profile_id,
                record.bot_id,
                record.user_id,
                record.group_id,
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
        namespace = RelationshipStateManager._event_namespace(
            event.relationship_profile_id,
            event.bot_id,
            event.user_id,
            event.group_id,
        )
        raw_event_id = event.event_id.strip()
        if not raw_event_id:
            material = "|".join((namespace, event.kind, str(now), event.text))
            raw_event_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        event_id = namespace + raw_event_id
        raw_dedupe = event.dedupe_key.strip() or raw_event_id
        return event_id, namespace + raw_dedupe

    @staticmethod
    def _validate_event(event: InteractionEvent) -> tuple[bool, str]:
        try:
            validate_profile_id(event.relationship_profile_id)
        except ValueError:
            return False, "invalid_relationship_profile"
        if event.kind not in KNOWN_KINDS:
            return False, "unknown_kind"
        if event.kind == KIND_INITIAL_PRIOR:
            return False, "reserved_admin_operation"
        try:
            confidence = float(event.confidence)
            severity = float(event.severity)
        except (TypeError, ValueError, OverflowError):
            return False, "invalid_event_strength"
        if not math.isfinite(confidence) or not math.isfinite(severity):
            return False, "invalid_event_strength"
        if event.is_semantic and event_strength(event) <= 0.0:
            return False, "zero_event_strength"
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
                relationship_profile_id=event.relationship_profile_id,
                scope_key=event.scope.user_key,
                kind=event.kind,
                source=event.source,
                confidence=self._safe_unit(event.confidence),
                severity=self._safe_unit(event.severity),
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

    @staticmethod
    def _safe_unit(value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if not math.isfinite(number):
            return 0.0
        return max(0.0, min(1.0, number))

    def _apply_deltas(
        self,
        event: InteractionEvent,
        state: UserRelationState,
        now: float,
        *,
        enforce_user_gates: bool = True,
    ) -> float:
        affinity_delta = self._affinity.compute(
            event,
            state,
            bypass_relationship_gates=not enforce_user_gates,
        )
        trust_delta = self._trust.compute(event, state)
        familiarity_delta = self._familiarity.compute(event, state)
        try:
            evidence_mass = float(state.extra.get(EVIDENCE_MASS_KEY, 0.0))
        except (TypeError, ValueError, OverflowError):
            evidence_mass = 0.0
        if not math.isfinite(evidence_mass) or evidence_mass < 0.0:
            evidence_mass = 0.0
        weight = event_weight(event, evidence_mass, self._dynamics_config)

        today = _day_of(now)
        if state.daily_anchor_day != today:
            state.daily_anchor_day = today
            state.daily_affinity_positive_used = 0.0
            state.daily_affinity_negative_used = 0.0
        want = affinity_delta.affinity * weight
        applied = 0.0
        if want > 0.0 and enforce_user_gates:
            want = self._limit_weighted_affinity(event, state, want)
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
            delta = getattr(trust_delta, field_name) * weight
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
        state.extra[EVIDENCE_MASS_KEY] = accumulate_evidence_mass(
            evidence_mass, event
        )
        return applied

    def _limit_weighted_affinity(
        self, event: InteractionEvent, state: UserRelationState, value: float
    ) -> float:
        cfg = self._affinity.config
        is_whitelisted = any(
            identity in cfg.whitelist_user_ids
            for identity in event.relationship_whitelist_ids
        )
        if not is_whitelisted:
            return min(value, max(0.0, cfg.non_whitelist_ceiling - state.affinity_score))
        if state.affinity_score >= cfg.high_affinity_threshold:
            return 0.0
        if (
            state.trust_score < cfg.whitelist_trust_gate
            or state.familiarity_score < cfg.whitelist_familiarity_gate
        ):
            return min(value, 0.1)
        return value

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

    def _save(self, *, raise_errors: bool = False) -> bool:
        try:
            save = getattr(self._repo, "save", None)
            if callable(save):
                save(self._states, self._events)
            else:
                self._repo.save_all(self._states)  # type: ignore[attr-defined]
            self._dirty = False
            return True
        except OSError as exc:  # pragma: no cover
            if self._logger is not None:
                self._logger.warning("[relationship] 持久化失败: %s", exc)
            if raise_errors:
                raise
            return False
