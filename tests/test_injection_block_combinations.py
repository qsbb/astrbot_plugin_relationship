"""最终 build_injection_block 级别的组合回归。

覆盖 friend/lover × 私聊/群聊 × 昵称/明确关系询问，确保最终注入块
内部不存在互相矛盾的规则（例如既放行恋人亲密、又声明“不等于恋爱”）。
"""

from __future__ import annotations

from core.models import (
    INTIMATE_BOUNDARY_RULE,
    NON_INTIMATE_BOUNDARY_RULE,
    RELATIONSHIP_TYPES_ALLOWING_INTIMATE,
    BehaviorAdvice,
    GroupRelationshipAdvice,
    RelationshipSnapshot,
    normalize_relationship_type,
)
from core.mood import MoodDecision
from core.policy import build_snapshot
from core.prompts import (
    INJECT_MARKER,
    build_injection_block,
)
from core.models import UserRelationState

_NON_INTIMATE_TYPES = ("friend", "close_friend", "family", "teammate", "rival")
_INTIMATE_TYPES = tuple(sorted(RELATIONSHIP_TYPES_ALLOWING_INTIMATE))


def _snapshot(relationship_type: str, *, affinity: float = 95.0) -> RelationshipSnapshot:
    return build_snapshot(
        MoodDecision(),
        UserRelationState(
            affinity_score=affinity,
            trust_score=80.0,
            familiarity_score=90.0,
            relationship_type=relationship_type,
        ),
    )


def test_intimate_never_contradicts_itself():
    """lover/exclusive 的最终块不得同时出现“不等于恋爱”式否定。"""
    for relationship_type in _INTIMATE_TYPES:
        block = build_injection_block(_snapshot(relationship_type))
        assert block, relationship_type
        assert "已被明确标记为情侣或专属联结" in block, relationship_type
        assert "不等于恋爱" not in block, relationship_type
        assert "把朋友式互动升级成亲密关系" not in block, relationship_type


def test_non_intimate_keeps_boundary_in_final_block():
    """friend/挚友/家人/队友/对手的最终块仍保留非恋爱边界。"""
    for relationship_type in _NON_INTIMATE_TYPES:
        block = build_injection_block(_snapshot(relationship_type))
        assert block, relationship_type
        assert "不等于恋爱" in block, relationship_type
        assert "把朋友式互动升级成亲密关系" in block, relationship_type
        assert "已被明确标记为情侣或专属联结" not in block, relationship_type


def test_intimate_and_non_intimate_blocks_are_mutually_exclusive():
    """恋人放行规则与非恋人边界规则在任何关系性质下都只出现一条。"""
    for relationship_type in _INTIMATE_TYPES + _NON_INTIMATE_TYPES:
        block = build_injection_block(_snapshot(relationship_type))
        has_intimate = "已被明确标记为情侣或专属联结" in block
        has_boundary = "不等于恋爱" in block
        assert has_intimate != has_boundary, relationship_type


def test_nickname_default_is_not_defensive():
    """普通称呼不触发主动防御/否认；块里给出默认按称呼或玩笑处理的指引。"""
    for relationship_type in ("friend", "lover"):
        block = build_injection_block(_snapshot(relationship_type))
        assert "默认只当作称呼或玩笑自然回应" in block, relationship_type
        assert "不必主动防御、否认或纠正" in block, relationship_type
        # 只有明确询问关系时才说明边界。
        assert "明确询问" in block, relationship_type


def test_private_block_has_no_public_group_restriction():
    block = build_injection_block(_snapshot("friend"), is_group=False)
    assert "公开场合" not in block
    assert "当前处于群聊" not in block


def test_group_block_limits_disclosure_not_expression():
    """群聊只限制公开披露，不要求把表达压成朋友式。"""
    block = build_injection_block(_snapshot("lover"), is_group=True)
    assert "公开场合" in block
    assert "不要在公开场合确认、升级或宣称亲密关系" in block
    assert "不要求把表达压成朋友式" in block
    # 群聊仍保留恋人级亲密放行，不把 lover 降级成朋友。
    assert "已被明确标记为情侣或专属联结" in block
    assert "不等于恋爱" not in block


def test_group_member_tone_remains_authoritative_for_direct_address():
    """群关系提示是附加信息，成员自身语气仍是权威。"""
    snapshot = _snapshot("friend")
    snapshot.group = GroupRelationshipAdvice(
        tier="close",
        prompt_fragment="这个群的整体关系较友好；可以自然参与话题，不越过成员边界。",
    )
    block = build_injection_block(snapshot, is_group=True)
    assert "这个群的整体关系较友好" in block
    assert "不等于恋爱" in block


def test_block_has_single_marker_and_no_duplicate_rules():
    block = build_injection_block(_snapshot("lover"), is_group=True)
    assert block.count(INJECT_MARKER) == 1
    # 边界规则不应重复出现。
    assert block.count("已被明确标记为情侣或专属联结") == 1
    assert block.count("默认只当作称呼或玩笑自然回应") == 1


def test_explicit_relationship_question_rule_present_for_both_types():
    """明确关系询问时，两种关系性质都应能按各自边界说明，而不是一味否认。"""
    for relationship_type in ("friend", "lover"):
        block = build_injection_block(_snapshot(relationship_type))
        assert "要求恋爱、排他、归属承诺" in block, relationship_type


def test_all_relationship_aliases_normalize_to_canonical_values():
    aliases = {
        "爱人": "lover",
        "专属": "exclusive",
        "lover": "lover",
        "EXCLUSIVE": "exclusive",
        "朋友": "friend",
        "挚友": "close_friend",
    }
    for raw, expected in aliases.items():
        assert normalize_relationship_type(raw) == expected
    assert normalize_relationship_type("unknown-value") == "friend"


def test_legacy_state_aliases_keep_intimate_boundary_after_loading():
    from core.models import UserRelationState

    for raw, expected in (("爱人", "lover"), ("专属", "exclusive")):
        state = UserRelationState.from_dict({"relationship_type": raw})
        snapshot = build_snapshot(MoodDecision(), state)
        block = build_injection_block(snapshot)
        assert snapshot.relationship_type == expected
        assert INTIMATE_BOUNDARY_RULE in block
        assert NON_INTIMATE_BOUNDARY_RULE not in block


def test_lover_warm_attentive_tone_does_not_deny_romantic_expression():
    snapshot = RelationshipSnapshot(
        relationship_type="lover",
        behavior=BehaviorAdvice(tone="warm_attentive"),
        prompt_fragment=INTIMATE_BOUNDARY_RULE,
    )
    block = build_injection_block(snapshot)
    assert "语气温和、上心，贴合当前关系分寸" in block
    assert "不把关心写成恋爱" not in block
    assert INTIMATE_BOUNDARY_RULE in block
    assert NON_INTIMATE_BOUNDARY_RULE not in block


def test_current_relationship_type_replaces_conflicting_legacy_boundaries():
    cases = (
        ("lover", NON_INTIMATE_BOUNDARY_RULE, INTIMATE_BOUNDARY_RULE),
        ("friend", INTIMATE_BOUNDARY_RULE, NON_INTIMATE_BOUNDARY_RULE),
    )
    for relationship_type, stale_rule, current_rule in cases:
        snapshot = RelationshipSnapshot(
            relationship_type=relationship_type,
            prompt_fragment=f"其他关系提示。{stale_rule}",
        )
        block = build_injection_block(snapshot)
        assert current_rule in block
        assert stale_rule not in block
