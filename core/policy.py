"""将关系状态映射为结构化、只读的表达建议。"""

from __future__ import annotations

from dataclasses import dataclass

from .affect import (
    STANCE_GUARDED,
    STANCE_WARM,
    AffectDecision,
)
from .mood import MOOD_ANNOYED, MOOD_LAZY, MoodDecision
from .models import (
    RELATIONSHIP_TYPES_ALLOWING_INTIMATE,
    BehaviorAdvice,
    RelationshipSnapshot,
    UserRelationState,
    clamp_score,
)
from .short_term_affinity import (
    TREND_COOLING,
    TREND_SETTLING,
    TREND_WARMING,
    AffinityTrendDecision,
)

_HIGH = 70
_LOW = 30
# Keep the more playful/attentive style aligned with the public
# ``inner_circle`` tier.  The intermediate ``close``/"朋友" range remains
# explicitly friend-safe even when its short-term trend is warming.
_WARM_AFFINITY_MIN = 80
_WARM_TRUST_MIN = 75
_WARM_FAMILIARITY_MIN = 60


@dataclass(frozen=True)
class PolicyConfig:
    enable_prompt_fragment: bool = True
    enable_style_hint: bool = True


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
    "fond": "可适度亲近和关心，但这不等同于恋爱、占有或排他关系。",
    "distant": "保持礼貌，不必刻意热络。",
}
_AFFECT_FRAGMENTS = {
    STANCE_GUARDED: "本轮语气应更克制有分寸，但不要敌意、惩罚或翻旧账。",
    STANCE_WARM: "本轮语气可显得更温和、亲近和上心，但不要越界或擅自承诺。",
}
_TREND_FRAGMENTS = {
    TREND_WARMING: "最近的互动让关系自然升温；回复可以更上心地承接，但不得据此提升关系身份、夸张示好或越过边界。",
    TREND_COOLING: "最近的互动让关系暂时降温；回复应更谨慎、克制并保留分寸，但不能冷暴力、讽刺或惩罚对方。",
    TREND_SETTLING: "前面出现过明显的关系波动，正在恢复平稳；回复自然一点，不要把上一轮情绪继续放大。",
}
_RELATIONSHIP_BOUNDARY_FRAGMENT = (
    "关系状态只表示互动中的熟悉、好感和信任，不等于恋爱、主从、占有或排他关系；"
    "不要把朋友式互动升级成亲密关系，也不要作归属式或排他性承诺。"
)
# 仅当管理员显式标记为情侣/恋人（lover）或专属联结（exclusive）时，才允许恋人级亲密表达。
# 家人/朋友/对手/队友/挚友即使高好感也不升级为恋人关系。
_RELATIONSHIP_INTIMATE_FRAGMENT = (
    "关系已被明确标记为情侣或专属联结；可以在对方明确接受的前提下自然表达亲密，"
    "但仍尊重对方边界，不作强迫或排他性承诺。"
)


def build_snapshot(
    decision: MoodDecision,
    state: UserRelationState,
    config: PolicyConfig | None = None,
    affect: AffectDecision | None = None,
    affinity_trend: AffinityTrendDecision | None = None,
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
    affect = affect or AffectDecision()
    affinity_trend = affinity_trend or AffinityTrendDecision()
    warm_style_allowed = (
        affinity >= _WARM_AFFINITY_MIN
        and trust >= _WARM_TRUST_MIN
        and familiarity >= _WARM_FAMILIARITY_MIN
    )

    tone = "natural"
    length = "normal"
    initiative = "normal"
    if cfg.enable_style_hint:
        if familiarity_tier == "old_friend" and warm_style_allowed:
            tone, initiative = "warm_playful", "high"
        elif familiarity_tier == "stranger" or affinity_tier == "distant":
            tone, initiative = "polite_reserved", "low"
        if affect.stance == STANCE_GUARDED:
            tone = "polite_reserved"
            if affinity_trend.style == TREND_COOLING:
                tone, initiative = "cool_polite", "low"
        elif affect.stance == STANCE_WARM:
            if warm_style_allowed:
                tone = "warm_attentive"
            elif affinity_tier != "distant":
                tone, initiative = "friendly_attentive", "normal"
            if warm_style_allowed and affinity_trend.style == TREND_WARMING:
                initiative = "high"
        elif affinity_trend.style == TREND_WARMING:
            if warm_style_allowed:
                tone, initiative = "warm_attentive", "high"
            elif affinity_tier != "distant":
                tone, initiative = "friendly_attentive", "normal"
        elif affinity_trend.style == TREND_COOLING:
            tone, initiative = "cool_polite", "low"
        if decision.mood == MOOD_ANNOYED:
            tone, length, initiative = "cool_polite", "minimal", "low"
        elif decision.mood == MOOD_LAZY:
            tone, length, initiative = "short_casual", "short", "low"

    fragments: list[str] = []
    if cfg.enable_prompt_fragment and not decision.should_silence:
        mood_fragment = _MOOD_FRAGMENTS.get(decision.mood)
        if mood_fragment:
            fragments.append(mood_fragment)
        else:
            affect_fragment = _AFFECT_FRAGMENTS.get(affect.stance)
            if affect_fragment:
                fragments.append(affect_fragment)
            trend_fragment = _TREND_FRAGMENTS.get(affinity_trend.style)
            if trend_fragment:
                fragments.append(trend_fragment)
        fragments.append(_FAMILIARITY_FRAGMENTS[familiarity_tier])
        affinity_fragment = _AFFINITY_FRAGMENTS.get(affinity_tier)
        if affinity_fragment:
            fragments.append(affinity_fragment)
        # 关系类型分级：仅管理员显式标记为 lover/exclusive（情侣/专属联结）才放行亲密表达。
        # 家人/朋友/对手/队友/挚友即使高好感也不升级为恋人关系，仍注入对应边界。
        relationship_type = str(getattr(state, "relationship_type", "friend") or "friend").strip()
        if relationship_type in RELATIONSHIP_TYPES_ALLOWING_INTIMATE:
            fragments.append(_RELATIONSHIP_INTIMATE_FRAGMENT)
        else:
            fragments.append(_RELATIONSHIP_BOUNDARY_FRAGMENT)
        if min(trust_dimensions.values()) <= _LOW:
            fragments.append("涉及重要事实或行动时应先核验，不根据关系状态作出承诺。")

    behavior = BehaviorAdvice(
        tone=tone,
        length=length,
        initiative=initiative,
        boundary="polite_safe",
        silence_suggested=decision.should_silence,
        silence_reason=decision.reason if decision.should_silence else "",
        prompt_fragments=tuple(fragments),
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
        relationship_type=str(getattr(state, "relationship_type", "friend") or "friend"),
    )
