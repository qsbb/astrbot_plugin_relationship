"""关系状态域核心数据模型。

0.2.0 保持 ``record / get_snapshot / reset`` 契约不变，并加入：
- 可审计但不保存消息原文的事件元数据；
- 分维度信任；
- 结构化行为建议；
- 群会话疲劳与用户压力的独立作用域。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profiles import (
    DEFAULT_PROFILE_ID,
    account_state_key,
    person_state_key,
    pressure_state_key,
    session_state_key,
    group_state_key,
)

KIND_MESSAGE = "message"
KIND_COMMAND = "command"
KIND_MENTION = "mention"
KIND_PRAISE = "praise"
KIND_OFFENSE = "offense"
KIND_HELP_RECEIVED = "help_received"
KIND_PROMISE_KEPT = "promise_kept"
KIND_PROMISE_BROKEN = "promise_broken"
KIND_INITIAL_PRIOR = "initial_prior"
KIND_RESET = "reset"

KNOWN_KINDS = frozenset(
    {
        KIND_MESSAGE,
        KIND_COMMAND,
        KIND_MENTION,
        KIND_PRAISE,
        KIND_OFFENSE,
        KIND_HELP_RECEIVED,
        KIND_PROMISE_KEPT,
        KIND_PROMISE_BROKEN,
        KIND_INITIAL_PRIOR,
        KIND_RESET,
    }
)
SEMANTIC_KINDS = frozenset(
    {
        KIND_PRAISE,
        KIND_OFFENSE,
        KIND_HELP_RECEIVED,
        KIND_PROMISE_KEPT,
        KIND_PROMISE_BROKEN,
        KIND_INITIAL_PRIOR,
    }
)

SOURCE_DIRECT = "direct"  # 其他插件通过契约明确提交，兼容 0.1.0
SOURCE_PLATFORM_MESSAGE = "platform_message"  # 原始聊天消息，不可自证语义事件
SOURCE_RULE = "rule"  # 本地确定性规则，仅允许低风险语义事件
SOURCE_VERIFIED = "verified_action"  # 已验证工具/工作流结果
SOURCE_ADMIN = "admin"  # 管理员明确操作
TRUSTED_SEMANTIC_SOURCES = frozenset(
    {SOURCE_DIRECT, SOURCE_RULE, SOURCE_VERIFIED, SOURCE_ADMIN}
)
HIGH_TRUST_EVENT_SOURCES = frozenset({SOURCE_DIRECT, SOURCE_VERIFIED, SOURCE_ADMIN})

SCORE_MIN = 0
SCORE_MAX = 100
SCORE_BASELINE = 50

# 关系性质：由管理员显式标记，不由好感度推导。
# 键为内部存储值，值为中文展示标签。
# 只有 RELATIONSHIP_TYPES_ALLOWING_INTIMATE 内的类型才放行恋人级亲密表达，
# 其余（家人/朋友/对手/队友/挚友）无论好感度多高都保持相应边界。
RELATIONSHIP_TYPE_LABELS = {
    "friend": "朋友",
    "close_friend": "挚友",
    "family": "家人",
    "teammate": "队友",
    "rival": "对手",
    "lover": "情侣",
    "exclusive": "专属联结",
}
RELATIONSHIP_TYPE_ALIASES = {
    "friend": "friend", "朋友": "friend",
    "close_friend": "close_friend", "挚友": "close_friend", "密友": "close_friend",
    "family": "family", "家人": "family", "亲人": "family",
    "teammate": "teammate", "队友": "teammate",
    "rival": "rival", "对手": "rival",
    "lover": "lover", "恋人": "lover", "情侣": "lover", "爱人": "lover",
    "exclusive": "exclusive", "专属联结": "exclusive", "专属": "exclusive",
}
# 放行恋人级亲密表达的关系类型集合。同时包含英文内部值和历史中文别名，
# 兼容早期版本直接写入中文值的旧数据。
RELATIONSHIP_TYPES_ALLOWING_INTIMATE = frozenset(
    {"lover", "exclusive", "恋人", "情侣", "专属联结"}
)


def clamp_score(value: float) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, int(round(value))))


@dataclass(frozen=True)
class RelationshipScope:
    bot_id: str
    user_id: str
    group_id: str | None = None
    person_id: str = ""
    state_alias_keys: tuple[str, ...] = ()
    relationship_profile_id: str = DEFAULT_PROFILE_ID
    whitelist_alias_ids: tuple[str, ...] = ()

    @property
    def user_key(self) -> str:
        if self.person_id:
            return person_state_key(self.relationship_profile_id, self.person_id)
        return account_state_key(
            self.relationship_profile_id, self.bot_id, self.user_id
        )

    @property
    def session_key(self) -> str:
        return session_state_key(
            self.relationship_profile_id,
            self.bot_id,
            self.user_id,
            self.group_id,
        )

    @property
    def pressure_key(self) -> str:
        """当前会话内某个用户造成的独立互动压力。"""
        return pressure_state_key(self.session_key, self.user_id)

    @property
    def group_key(self) -> str:
        """Long-lived relationship key for the current group."""
        if not self.group_id:
            return ""
        return group_state_key(
            self.relationship_profile_id, self.bot_id, self.group_id
        )

    @property
    def is_private(self) -> bool:
        return not self.group_id


@dataclass
class InteractionEvent:
    """一次互动事件，是 Manager 唯一输入。

    ``source`` 决定语义事件能否修改长期状态。原始平台消息即使文本声称
    “我履约了”，也不能直接产生高权重信任变化。
    """

    bot_id: str
    user_id: str
    group_id: str | None
    text: str
    timestamp: float
    kind: str = KIND_MESSAGE
    event_id: str = ""
    source: str = SOURCE_DIRECT
    confidence: float = 1.0
    severity: float = 1.0
    dedupe_key: str = ""
    evidence_refs: tuple[str, ...] = ()
    person_id: str = ""
    state_alias_keys: tuple[str, ...] = ()
    relationship_profile_id: str = DEFAULT_PROFILE_ID
    whitelist_alias_ids: tuple[str, ...] = ()

    @property
    def scope(self) -> RelationshipScope:
        return RelationshipScope(
            bot_id=self.bot_id,
            user_id=self.user_id,
            group_id=self.group_id,
            person_id=self.person_id,
            state_alias_keys=self.state_alias_keys,
            relationship_profile_id=self.relationship_profile_id,
            whitelist_alias_ids=self.whitelist_alias_ids,
        )

    @property
    def relationship_user_id(self) -> str:
        return self.person_id or self.user_id

    @property
    def relationship_whitelist_ids(self) -> tuple[str, ...]:
        identities = tuple(
            dict.fromkeys(
                value
                for raw_value in (
                    self.relationship_user_id,
                    *self.whitelist_alias_ids,
                )
                if (value := str(raw_value or "").strip())
            )
        )
        return identities + tuple(
            f"{self.relationship_profile_id}/{identity}" for identity in identities
        )

    @property
    def is_command(self) -> bool:
        return self.kind == KIND_COMMAND or (self.text or "").lstrip().startswith("/")

    @property
    def is_semantic(self) -> bool:
        return self.kind in SEMANTIC_KINDS


@dataclass(frozen=True)
class RelationshipEventRecord:
    """不含消息正文的审计事件。"""

    event_id: str
    timestamp: float
    bot_id: str
    user_id: str
    group_id: str | None
    relationship_profile_id: str
    scope_key: str
    kind: str
    source: str
    confidence: float
    severity: float
    dedupe_key: str
    evidence_refs: tuple[str, ...]
    applied: bool
    rejection_reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "group_id": self.group_id,
            "relationship_profile_id": self.relationship_profile_id,
            "scope_key": self.scope_key,
            "kind": self.kind,
            "source": self.source,
            "confidence": self.confidence,
            "severity": self.severity,
            "dedupe_key": self.dedupe_key,
            "evidence_refs": list(self.evidence_refs),
            "applied": self.applied,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "RelationshipEventRecord":
        refs = data.get("evidence_refs", [])
        return cls(
            event_id=str(data.get("event_id", "")),
            timestamp=float(data.get("timestamp", 0.0)),  # type: ignore[arg-type]
            bot_id=str(data.get("bot_id", "")),
            user_id=str(data.get("user_id", "")),
            group_id=(str(data["group_id"]) if data.get("group_id") else None),
            relationship_profile_id=str(
                data.get("relationship_profile_id", DEFAULT_PROFILE_ID)
            ),
            scope_key=str(data.get("scope_key", "")),
            kind=str(data.get("kind", KIND_MESSAGE)),
            source=str(data.get("source", SOURCE_DIRECT)),
            confidence=float(data.get("confidence", 1.0)),  # type: ignore[arg-type]
            severity=float(data.get("severity", 1.0)),  # type: ignore[arg-type]
            dedupe_key=str(data.get("dedupe_key", "")),
            evidence_refs=tuple(str(v) for v in refs) if isinstance(refs, list) else (),
            applied=bool(data.get("applied", True)),
            rejection_reason=str(data.get("rejection_reason", "")),
        )


@dataclass
class DimensionDelta:
    affinity: float = 0.0
    trust_reliability: float = 0.0
    trust_benevolence: float = 0.0
    trust_integrity: float = 0.0
    trust_epistemic: float = 0.0
    familiarity: float = 0.0
    reason: str = ""

    @property
    def trust(self) -> float:
        values = (
            self.trust_reliability,
            self.trust_benevolence,
            self.trust_integrity,
            self.trust_epistemic,
        )
        return sum(values) / 4.0

    def is_zero(self) -> bool:
        return not (
            self.affinity
            or self.trust_reliability
            or self.trust_benevolence
            or self.trust_integrity
            or self.trust_epistemic
            or self.familiarity
        )


@dataclass(frozen=True)
class BehaviorAdvice:
    """供“言”等消费方使用的结构化表达建议，不授予任何权限。"""

    tone: str = "natural"
    length: str = "normal"
    initiative: str = "normal"
    boundary: str = "polite_safe"
    silence_suggested: bool = False
    silence_reason: str = ""
    prompt_fragments: tuple[str, ...] = ()
    # relationship.snapshot@1 兼容字段；追问抑制已迁到“言”，固定为 allow。
    followup: str = "allow"

    def as_dict(self) -> dict[str, object]:
        return {
            "tone": self.tone,
            "length": self.length,
            "initiative": self.initiative,
            "boundary": self.boundary,
            "silence_suggested": self.silence_suggested,
            "silence_reason": self.silence_reason,
            "prompt_fragments": list(self.prompt_fragments),
            "followup": self.followup,
        }


@dataclass(frozen=True)
class GroupRelationshipAdvice:
    """Derived relationship hint for one group; never a permission grant."""

    affinity: int = SCORE_BASELINE
    trust: int = SCORE_BASELINE
    familiarity: int = 0
    tier: str = "neutral"
    behavior: BehaviorAdvice = field(default_factory=BehaviorAdvice)
    prompt_fragment: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "affinity": self.affinity,
            "trust": self.trust,
            "familiarity": self.familiarity,
            "tier": self.tier,
            "behavior": self.behavior.as_dict(),
            "prompt_fragment": self.prompt_fragment,
        }


@dataclass
class RelationshipSnapshot:
    mood: str = "normal"
    willingness: int = 100
    affinity: int = SCORE_BASELINE
    trust: int = SCORE_BASELINE
    familiarity: int = 0
    response_style: str = ""
    should_silence: bool = False  # 兼容字段：仅代表建议，不直接阻断事件
    prompt_fragment: str = ""
    trust_dimensions: dict[str, int] = field(default_factory=dict)
    behavior: BehaviorAdvice = field(default_factory=BehaviorAdvice)
    # relationship.snapshot@1 兼容字段；不再由“情”推进。
    followup_streak: int = 0
    group: GroupRelationshipAdvice | None = None
    # 关系性质：由管理员显式标记，不由好感度推导。
    relationship_type: str = "friend"

    def as_dict(self) -> dict[str, object]:
        return {
            "mood": self.mood,
            "willingness": self.willingness,
            "affinity": self.affinity,
            "trust": self.trust,
            "familiarity": self.familiarity,
            "response_style": self.response_style,
            "should_silence": self.should_silence,
            "prompt_fragment": self.prompt_fragment,
            "trust_dimensions": dict(self.trust_dimensions),
            "behavior": self.behavior.as_dict(),
            "followup_streak": self.followup_streak,
            "relationship_type": self.relationship_type,
            "group": self.group.as_dict() if self.group is not None else None,
        }


@dataclass
class UserRelationState:
    affinity_score: float = float(SCORE_BASELINE)
    trust_score: float = float(SCORE_BASELINE)  # 兼容聚合缓存
    trust_reliability: float = float(SCORE_BASELINE)
    trust_benevolence: float = float(SCORE_BASELINE)
    trust_integrity: float = float(SCORE_BASELINE)
    trust_epistemic: float = float(SCORE_BASELINE)
    familiarity_score: float = 0.0
    daily_affinity_positive_used: float = 0.0
    daily_affinity_negative_used: float = 0.0
    daily_anchor_day: str = ""
    interaction_count: int = 0
    last_event_at: float = 0.0
    initial_prior: str = ""
    initial_prior_applied_at: float = 0.0
    # 关系性质：由管理员显式标记，不由好感度推导。
    # friend / close_friend / lover / exclusive，默认 friend。
    # 只有 lover 或 exclusive 才允许恋人级亲密表达。
    relationship_type: str = "friend"
    extra: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """兼容旧代码仅传入聚合 ``trust_score`` 的构造方式。"""
        dimensions = (
            self.trust_reliability,
            self.trust_benevolence,
            self.trust_integrity,
            self.trust_epistemic,
        )
        if self.trust_score != SCORE_BASELINE and all(
            value == SCORE_BASELINE for value in dimensions
        ):
            self.trust_reliability = self.trust_score
            self.trust_benevolence = self.trust_score
            self.trust_integrity = self.trust_score
            self.trust_epistemic = self.trust_score

    def refresh_trust_score(self) -> None:
        self.trust_score = (
            sum(
                (
                    self.trust_reliability,
                    self.trust_benevolence,
                    self.trust_integrity,
                    self.trust_epistemic,
                )
            )
            / 4.0
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "affinity_score": self.affinity_score,
            "trust_score": self.trust_score,
            "trust_reliability": self.trust_reliability,
            "trust_benevolence": self.trust_benevolence,
            "trust_integrity": self.trust_integrity,
            "trust_epistemic": self.trust_epistemic,
            "familiarity_score": self.familiarity_score,
            "daily_affinity_positive_used": self.daily_affinity_positive_used,
            "daily_affinity_negative_used": self.daily_affinity_negative_used,
            "daily_anchor_day": self.daily_anchor_day,
            "interaction_count": self.interaction_count,
            "last_event_at": self.last_event_at,
            "initial_prior": self.initial_prior,
            "initial_prior_applied_at": self.initial_prior_applied_at,
            "relationship_type": self.relationship_type,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "UserRelationState":
        legacy_trust = float(data.get("trust_score", SCORE_BASELINE))  # type: ignore[arg-type]
        legacy_used = float(data.get("daily_affinity_used", 0.0))  # type: ignore[arg-type]
        state = cls(
            affinity_score=float(data.get("affinity_score", SCORE_BASELINE)),  # type: ignore[arg-type]
            trust_score=legacy_trust,
            trust_reliability=float(data.get("trust_reliability", legacy_trust)),  # type: ignore[arg-type]
            trust_benevolence=float(data.get("trust_benevolence", legacy_trust)),  # type: ignore[arg-type]
            trust_integrity=float(data.get("trust_integrity", legacy_trust)),  # type: ignore[arg-type]
            trust_epistemic=float(data.get("trust_epistemic", legacy_trust)),  # type: ignore[arg-type]
            familiarity_score=float(data.get("familiarity_score", 0.0)),  # type: ignore[arg-type]
            daily_affinity_positive_used=float(
                data.get("daily_affinity_positive_used", legacy_used)
            ),
            daily_affinity_negative_used=float(
                data.get("daily_affinity_negative_used", 0.0)
            ),
            daily_anchor_day=str(data.get("daily_anchor_day", "")),
            interaction_count=int(data.get("interaction_count", 0)),  # type: ignore[arg-type]
            last_event_at=float(data.get("last_event_at", 0.0)),  # type: ignore[arg-type]
            initial_prior=str(data.get("initial_prior", "")),
            initial_prior_applied_at=float(
                data.get("initial_prior_applied_at", 0.0)  # type: ignore[arg-type]
            ),
            relationship_type=str(data.get("relationship_type", "friend")),
        )
        extra = data.get("extra")
        if isinstance(extra, dict):
            state.extra = {str(k): float(v) for k, v in extra.items()}
        state.refresh_trust_score()
        return state


@dataclass
class GroupRelationState(UserRelationState):
    """Persistent state for one group, never merged with a person/account."""

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "GroupRelationState":
        state = UserRelationState.from_dict(data)
        return cls(
            affinity_score=state.affinity_score,
            trust_score=state.trust_score,
            trust_reliability=state.trust_reliability,
            trust_benevolence=state.trust_benevolence,
            trust_integrity=state.trust_integrity,
            trust_epistemic=state.trust_epistemic,
            familiarity_score=state.familiarity_score,
            daily_affinity_positive_used=state.daily_affinity_positive_used,
            daily_affinity_negative_used=state.daily_affinity_negative_used,
            daily_anchor_day=state.daily_anchor_day,
            interaction_count=state.interaction_count,
            last_event_at=state.last_event_at,
            initial_prior=state.initial_prior,
            initial_prior_applied_at=state.initial_prior_applied_at,
            extra=dict(state.extra),
        )
