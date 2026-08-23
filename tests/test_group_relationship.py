"""Independent group relationship state and prompt hints."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from core.manager import RelationshipStateManager
from core.models import InteractionEvent, RelationshipScope, KIND_PRAISE
from core.prompts import build_injection_block
from core.profiles import group_state_key, parse_state_key
from core.repository import JsonRepository


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _event(
    *,
    user_id: str,
    event_id: str,
    group_id: str = "g1",
    ts: float = 1000.0,
    kind: str = KIND_PRAISE,
):
    return InteractionEvent(
        bot_id="bot",
        user_id=user_id,
        group_id=group_id,
        text="hello",
        timestamp=ts,
        event_id=event_id,
        kind=kind,
    )


def test_group_key_is_not_an_identity_state():
    key = group_state_key("default", "bot", "g/one")
    assert parse_state_key(key) == {
        "kind": "group",
        "profile_id": "default",
        "bot_id": "bot",
        "group_id": "g/one",
    }


def test_group_score_is_shared_by_members_and_isolated_between_groups():
    manager = RelationshipStateManager(save_interval_seconds=0)
    first = _run(manager.record(_event(user_id="u1", event_id="a")))
    second = _run(manager.record(_event(user_id="u2", event_id="b", ts=1100)))
    other = _run(
        manager.record(_event(user_id="u1", event_id="c", group_id="g2", ts=1200))
    )

    assert first.group is not None
    assert second.group is not None
    assert second.group.affinity > first.group.affinity
    assert other.group is not None
    assert other.group.affinity < second.group.affinity
    assert "group:" in next(key for key in manager._states if ":group:" in key)
    assert all(":group:" not in key for key in manager._states if ":account:" in key)


def test_group_prompt_hint_is_additive_to_member_prompt():
    manager = RelationshipStateManager(save_interval_seconds=0)
    snapshot = _run(manager.record(_event(user_id="u1", event_id="a")))
    block = build_injection_block(snapshot, is_group=True)
    assert "群聊" in block
    assert "普通群聊" in block


def test_group_snapshot_is_read_only_and_does_not_create_user_state():
    manager = RelationshipStateManager(save_interval_seconds=0)
    scope = RelationshipScope("bot", "u1", "g1")
    advice = _run(manager.get_group_snapshot("bot", "g1"))
    assert advice.affinity == 50
    assert scope.user_key not in manager._states
    assert scope.group_key not in manager._states


def test_group_state_round_trips_without_identity_merge():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "relationship.json"
        repo = JsonRepository(path)
        manager = RelationshipStateManager(repository=repo, save_interval_seconds=0)
        _run(manager.record(_event(user_id="u1", event_id="a")))
        restored = RelationshipStateManager(repository=JsonRepository(path))
        key = group_state_key("default", "bot", "g1")
        assert key in restored._states
        assert type(restored._states[key]).__name__ == "GroupRelationState"
        assert restored._states[key].interaction_count == 1
