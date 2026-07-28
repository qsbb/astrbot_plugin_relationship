"""关系状态域核心数据模型。

0.2.0 保持 ``record / get_snapshot / reset`` 契约不变，并加入：
- 可审计但不保存消息原文的事件元数据；
- 分维度信任；
- 结构化行为建议；
- 群会话疲劳与用户压力的独立作用域。
"""

from __future__ import annotations

from dataclasses import dataclass, field

KIND_MESSAGE = "message"
KIND_COMMAND = "command"
KIND_MENTION = "mention"
KIND_PRAISE = "praise"
KIND_OFFENSE = "offense"
KIND_HELP_RECEIVED = "help_received"
KIND_PROMISE_KEPT = "promise_kept"
KIND_PROMISE_BROKEN = "promise_broken"
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


def clamp_score(value: float) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, int(round(value))))


@dataclass(frozen=True)
class RelationshipScope:
    bot_id: str
    user_id: str
    group_id: str | None = None

    @property
    def user_key(self) -> str:
        return f"{self.bot_id}:user:{self.user_id}"

    @property
    def session_key(self) -> str:
        if self.group_id:
            return f"{self.bot_id}:group:{self.group_id}"
        return f"{self.bot_id}:private:{self.user_id}"

    @property
    def pressure_key(self) -> str:
        """当前会话内某个用户造成的独立互动压力。"""
        return f"{self.session_key}:user:{self.user_id}"

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

    @property
    def scope(self) -> RelationshipScope:
        return RelationshipScope(self.bot_id, self.user_id, self.group_id)

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
        )
        extra = data.get("extra")
        if isinstance(extra, dict):
            state.extra = {str(k): float(v) for k, v in extra.items()}
        state.refresh_trust_score()
        return state
