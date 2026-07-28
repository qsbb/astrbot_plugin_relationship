"""将关系状态映射为结构化、只读的表达建议。"""

from __future__ import annotations

from dataclasses import dataclass

from .followup import LEVEL_HARD, LEVEL_SOFT, FollowupDecision
from .mood import MOOD_ANNOYED, MOOD_LAZY, MoodDecision
from .models import BehaviorAdvice, RelationshipSnapshot, UserRelationState, clamp_score

_HIGH = 70
_LOW = 30


@dataclass(frozen=True)
class PolicyConfig:
    enable_prompt_fragment: bool = True
    enable_style_hint: bool = True
    enable_followup_guard: bool = True


# 服务式追问收尾的建议档位
FOLLOWUP_ALLOW = "allow"
FOLLOWUP_AVOID = "avoid"
FOLLOWUP_FORBID = "forbid"


def _familiarity_tier(value: int) -> str:
    if value >= _HIGH:
        return "old_friend"
    if value >= _LOW:
        return "acquaintance"
    return "stranger"


def _affinity_tier(value: int) -> str:
    if value >= _HIGH:
        return "fond"
    if value <= _LOW:
        return "distant"
    return "neutral"


_MOOD_FRAGMENTS = {
    MOOD_LAZY: "表达可以更简短随意，但仍应完整回答必要信息。",
    MOOD_ANNOYED: "表达可以克制简短，但仍须礼貌、准确且不伤害对方。",
}
_FAMILIARITY_FRAGMENTS = {
    "old_friend": "可采用熟人间自然轻松的口吻。",
    "acquaintance": "保持自然，不必过分客套，也不要自来熟。",
    "stranger": "保持友好并注意分寸。",
}
_AFFINITY_FRAGMENTS = {
    "fond": "可适度亲近和关心。",
    "distant": "保持礼貌，不必刻意热络。",
}


def _followup_advice(
    followup: FollowupDecision | None, enabled: bool
) -> tuple[str, int]:
    """把追问统计映射为建议档位；关闭开关时不给出抑制建议。"""
    streak = followup.streak if followup else 0
    if not enabled:
        return FOLLOWUP_ALLOW, streak
    level = followup.level if followup else None
    if level == LEVEL_HARD:
        return FOLLOWUP_FORBID, streak
    if level == LEVEL_SOFT:
        return FOLLOWUP_AVOID, streak
    return FOLLOWUP_AVOID, streak


def build_snapshot(
    decision: MoodDecision,
    state: UserRelationState,
    config: PolicyConfig | None = None,
    followup: FollowupDecision | None = None,
) -> RelationshipSnapshot:
    """构造数据快照；所有行为字段仅为建议，不产生任何执行效果。"""
    cfg = config or PolicyConfig()
    affinity = clamp_score(state.affinity_score)
    familiarity = clamp_score(state.familiarity_score)
    trust_dimensions = {
        "reliability": clamp_score(state.trust_reliability),
        "benevolence": clamp_score(state.trust_benevolence),
        "integrity": clamp_score(state.trust_integrity),
        "epistemic": clamp_score(state.trust_epistemic),
    }
    trust = clamp_score(sum(trust_dimensions.values()) / len(trust_dimensions))
    familiarity_tier = _familiarity_tier(familiarity)
    affinity_tier = _affinity_tier(affinity)

    tone = "natural"
    length = "normal"
    initiative = "normal"
    if cfg.enable_style_hint:
        if decision.mood == MOOD_ANNOYED:
            tone, length, initiative = "cool_polite", "minimal", "low"
        elif decision.mood == MOOD_LAZY:
            tone, length, initiative = "short_casual", "short", "low"
        elif familiarity_tier == "old_friend" and affinity_tier == "fond":
            tone, initiative = "warm_playful", "high"
        elif familiarity_tier == "stranger" or affinity_tier == "distant":
            tone, initiative = "polite_reserved", "low"

    fragments: list[str] = []
    if cfg.enable_prompt_fragment and not decision.should_silence:
        mood_fragment = _MOOD_FRAGMENTS.get(decision.mood)
        if mood_fragment:
            fragments.append(mood_fragment)
        fragments.append(_FAMILIARITY_FRAGMENTS[familiarity_tier])
        affinity_fragment = _AFFINITY_FRAGMENTS.get(affinity_tier)
        if affinity_fragment:
            fragments.append(affinity_fragment)
        if min(trust_dimensions.values()) <= _LOW:
            fragments.append("涉及重要事实或行动时应先核验，不根据关系状态作出承诺。")

    followup_advice, followup_streak = _followup_advice(
        followup, cfg.enable_followup_guard
    )
    behavior = BehaviorAdvice(
        tone=tone,
        length=length,
        initiative=initiative,
        boundary="polite_safe",
        silence_suggested=decision.should_silence,
        silence_reason=decision.reason if decision.should_silence else "",
        prompt_fragments=tuple(fragments),
        followup=followup_advice,
    )
    return RelationshipSnapshot(
        mood=decision.mood,
        willingness=decision.willingness,
        affinity=affinity,
        trust=trust,
        familiarity=familiarity,
        response_style=tone,
        should_silence=behavior.silence_suggested,
        prompt_fragment=" ".join(fragments),
        trust_dimensions=trust_dimensions,
        behavior=behavior,
        followup_streak=followup_streak,
    )
