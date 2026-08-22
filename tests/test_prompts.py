from __future__ import annotations

from core.models import RelationshipSnapshot
from core.prompts import build_injection_block


def test_relationship_prompt_has_non_romantic_boundary() -> None:
    block = build_injection_block(RelationshipSnapshot())

    assert "不等于恋爱" in block
    assert "归属式承诺" in block
    assert "归你" in block


def test_group_relationship_prompt_adds_public_boundary() -> None:
    private = build_injection_block(RelationshipSnapshot())
    group = build_injection_block(RelationshipSnapshot(), is_group=True)

    assert "公开场合" not in private
    assert "公开场合" in group
    assert "不要在公开场合确认、升级或宣称亲密关系" in group

