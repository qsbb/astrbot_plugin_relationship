from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.affinity import AffinityCalculator, AffinityConfig
from core.dynamics import EVIDENCE_MASS_KEY, DynamicsConfig
from core.manager import RelationshipStateManager
from core.models import (
    KIND_PRAISE,
    SOURCE_DIRECT,
    InteractionEvent,
    RelationshipScope,
    UserRelationState,
)
from core.repository import JsonRepository, MemoryRepository


def _run(coro):
    return asyncio.run(coro)


def _event(
    *,
    profile: str = "default",
    user_id: str = "u1",
    event_id: str = "event-1",
    kind: str = "message",
    confidence: float = 1.0,
    severity: float = 1.0,
    person_id: str = "",
    aliases: tuple[str, ...] = (),
) -> InteractionEvent:
    return InteractionEvent(
        bot_id="bot",
        user_id=user_id,
        group_id=None,
        text="hello",
        timestamp=1000.0,
        kind=kind,
        event_id=event_id,
        source=SOURCE_DIRECT,
        confidence=confidence,
        severity=severity,
        person_id=person_id,
        state_alias_keys=aliases,
        relationship_profile_id=profile,
    )


def _manager(**kwargs) -> RelationshipStateManager:
    defaults = {
        "repository": MemoryRepository(),
        "save_interval_seconds": 0.0,
        "clock": lambda: 1000.0,
    }
    defaults.update(kwargs)
    return RelationshipStateManager(**defaults)


def test_same_account_and_event_id_are_isolated_between_profiles() -> None:
    manager = _manager()

    _run(manager.record(_event(profile="persona-a", event_id="same")))
    _run(manager.record(_event(profile="persona-b", event_id="same")))

    assert set(manager._states) == {
        "persona:persona-a:account:bot:user:u1",
        "persona:persona-b:account:bot:user:u1",
    }
    assert len(manager._events) == 2
    assert manager._events[0].event_id != manager._events[1].event_id


def test_natural_person_shares_accounts_only_inside_one_profile() -> None:
    manager = _manager()
    aliases_a = (
        "persona:persona-a:account:bot:user:u1",
        "persona:persona-a:account:bot:user:u2",
    )
    aliases_b = (
        "persona:persona-b:account:bot:user:u1",
        "persona:persona-b:account:bot:user:u2",
    )

    _run(
        manager.record(
            _event(
                profile="persona-a",
                person_id="summer",
                aliases=aliases_a,
                event_id="a-1",
            )
        )
    )
    _run(
        manager.record(
            _event(
                profile="persona-a",
                user_id="u2",
                person_id="summer",
                aliases=aliases_a,
                event_id="a-2",
            )
        )
    )
    _run(
        manager.record(
            _event(
                profile="persona-b",
                person_id="summer",
                aliases=aliases_b,
                event_id="b-1",
            )
        )
    )

    assert manager._states[
        "persona:persona-a:person:summer"
    ].interaction_count == 2
    assert manager._states[
        "persona:persona-b:person:summer"
    ].interaction_count == 1
    assert not set(aliases_a + aliases_b).intersection(manager._states)


def test_unbind_identity_moves_each_profile_to_selected_account() -> None:
    manager = _manager()
    source_a = "persona:persona-a:person:summer"
    source_b = "persona:persona-b:person:summer"
    target_a = "persona:persona-a:account:bot:user:u1"
    target_b = "persona:persona-b:account:bot:user:u1"
    manager._states[source_a] = UserRelationState(interaction_count=3)
    manager._states[source_b] = UserRelationState(interaction_count=7)

    changed = _run(
        manager.unbind_identity_states(
            ((target_a, source_a), (target_b, source_b))
        )
    )

    assert changed == (target_a, target_b)
    assert manager._states[target_a].interaction_count == 3
    assert manager._states[target_b].interaction_count == 7
    assert source_a not in manager._states
    assert source_b not in manager._states


def test_unbind_identity_rejects_conflicting_account_state_and_rolls_back() -> None:
    manager = _manager()
    source = "persona:default:person:summer"
    target = "persona:default:account:bot:user:u1"
    manager._states[source] = UserRelationState(interaction_count=5)
    manager._states[target] = UserRelationState(interaction_count=2)

    with pytest.raises(ValueError, match="RESTORE_ACCOUNT_STATE_CONFLICT"):
        _run(manager.unbind_identity_states(((target, source),)))

    assert manager._states[source].interaction_count == 5
    assert manager._states[target].interaction_count == 2


def test_unbind_identity_preflight_rejects_conflict_without_mutation() -> None:
    manager = _manager()
    source = "persona:default:person:summer"
    target = "persona:default:account:bot:user:u1"
    manager._states[source] = UserRelationState(interaction_count=5)
    manager._states[target] = UserRelationState(interaction_count=2)

    with pytest.raises(ValueError, match="RESTORE_ACCOUNT_STATE_CONFLICT"):
        _run(manager.validate_identity_unbind_states(((target, source),)))

    assert manager._states[source].interaction_count == 5
    assert manager._states[target].interaction_count == 2


def test_delete_relationship_states_removes_only_requested_profiles() -> None:
    manager = _manager()
    profile_a = "persona:persona-a:person:summer"
    profile_b = "persona:persona-b:person:summer"
    manager._states[profile_a] = UserRelationState(interaction_count=3)
    manager._states[profile_b] = UserRelationState(interaction_count=7)

    deleted = _run(manager.delete_relationship_states((profile_a,)))

    assert deleted == (profile_a,)
    assert profile_a not in manager._states
    assert manager._states[profile_b].interaction_count == 7


def test_initial_prior_is_fixed_audited_and_cannot_be_reapplied_after_reset() -> None:
    manager = _manager()
    scope = RelationshipScope(
        bot_id="bot",
        user_id="u1",
        person_id="summer",
        relationship_profile_id="persona-a",
    )

    snapshot = _run(manager.apply_initial_prior(scope, "fond"))

    state = manager._states["persona:persona-a:person:summer"]
    assert snapshot.affinity == 64
    assert snapshot.trust == 60
    assert snapshot.familiarity == 25
    assert state.initial_prior == "fond"
    assert manager._events[-1].kind == "initial_prior"
    assert manager._events[-1].source == "admin"
    assert manager._events[-1].scope_key == scope.user_key

    with pytest.raises(ValueError, match="INITIAL_PRIOR_ALREADY_APPLIED"):
        _run(manager.apply_initial_prior(scope, "neutral"))
    _run(manager.reset(scope))
    with pytest.raises(ValueError, match="INITIAL_PRIOR_ALREADY_APPLIED"):
        _run(manager.apply_initial_prior(scope, "acquainted"))

    other_profile = RelationshipScope(
        bot_id="bot",
        user_id="u1",
        person_id="summer",
        relationship_profile_id="persona-b",
    )
    assert _run(manager.apply_initial_prior(other_profile, "neutral")).affinity == 50


def test_initial_prior_rejects_an_active_relationship_and_numeric_levels() -> None:
    manager = _manager()
    scope = RelationshipScope(
        bot_id="bot",
        user_id="u1",
        person_id="summer",
        relationship_profile_id="persona-a",
    )
    _run(
        manager.record(
            _event(profile="persona-a", person_id="summer", event_id="active")
        )
    )

    with pytest.raises(ValueError, match="RELATIONSHIP_ALREADY_ACTIVE"):
        _run(manager.apply_initial_prior(scope, "fond"))
    with pytest.raises(ValueError, match="INVALID_INITIAL_PRIOR"):
        _run(manager.apply_initial_prior(scope, "64"))


def test_initial_prior_rolls_back_when_persistence_fails() -> None:
    class FailingRepository(MemoryRepository):
        def save(self, states, events) -> None:
            del states, events
            raise OSError("disk unavailable")

    manager = _manager(repository=FailingRepository())
    scope = RelationshipScope(
        bot_id="bot",
        user_id="u1",
        person_id="summer",
        relationship_profile_id="persona-a",
    )

    with pytest.raises(OSError, match="disk unavailable"):
        _run(manager.apply_initial_prior(scope, "fond"))

    assert scope.user_key not in manager._states
    assert manager._events == []


def test_rejected_initial_prior_does_not_materialize_active_alias() -> None:
    manager = _manager()
    alias = "persona:persona-a:account:bot:user:u1"
    manager._states[alias] = UserRelationState(
        interaction_count=1,
        last_event_at=900.0,
    )
    scope = RelationshipScope(
        bot_id="bot",
        user_id="u1",
        person_id="summer",
        state_alias_keys=(alias,),
        relationship_profile_id="persona-a",
    )

    with pytest.raises(ValueError, match="RELATIONSHIP_ALREADY_ACTIVE"):
        _run(manager.apply_initial_prior(scope, "fond"))

    assert alias in manager._states
    assert scope.user_key not in manager._states
    assert manager._dirty is False


def test_identity_binding_rolls_back_when_persistence_fails() -> None:
    class FailingRepository(MemoryRepository):
        def save(self, states, events) -> None:
            del states, events
            raise OSError("disk unavailable")

    repository = FailingRepository()
    alias = "persona:default:account:bot:user:u1"
    repository._data[alias] = UserRelationState(
        interaction_count=2,
        last_event_at=900.0,
    )
    manager = _manager(repository=repository)

    with pytest.raises(OSError, match="disk unavailable"):
        _run(
            manager.bind_identity(
                "persona:default:person:summer",
                (alias,),
            )
        )

    assert alias in manager._states
    assert "persona:default:person:summer" not in manager._states
    assert manager._dirty is False


def test_semantic_strength_and_early_plasticity_are_applied_once() -> None:
    manager = _manager(
        affinity=AffinityCalculator(
            AffinityConfig(
                praise_gain=4.0,
                daily_cap=100.0,
                non_whitelist_ceiling=100.0,
            )
        ),
        dynamics_config=DynamicsConfig(early_boost=0.25, evidence_half_life=12.0),
    )

    snapshot = _run(
        manager.record(
            _event(
                kind=KIND_PRAISE,
                confidence=0.5,
                severity=0.5,
            )
        )
    )

    state = manager._states["persona:default:account:bot:user:u1"]
    assert snapshot.affinity == 51
    assert state.affinity_score == pytest.approx(51.25)
    assert state.extra[EVIDENCE_MASS_KEY] == pytest.approx(0.25)


def test_non_finite_semantic_strength_is_rejected_and_safely_audited() -> None:
    manager = _manager()

    snapshot = _run(
        manager.record(
            _event(kind=KIND_PRAISE, confidence=float("nan"), severity=float("inf"))
        )
    )

    assert snapshot.affinity == 50
    assert manager._states == {}
    assert manager._events[0].applied is False
    assert manager._events[0].rejection_reason == "invalid_event_strength"
    assert manager._events[0].confidence == 0.0
    assert manager._events[0].severity == 0.0


def test_v3_migration_assigns_history_to_one_profile_and_keeps_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "relationship_state.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "users": {
                    "person:user:summer": {
                        "affinity_score": 72.0,
                        "interaction_count": 8,
                    }
                },
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    repository = JsonRepository(path, legacy_profile_id="legacy-owner")
    states = repository.load_all()

    assert set(states) == {"persona:legacy-owner:person:summer"}
    assert states["persona:legacy-owner:person:summer"].affinity_score == 72.0
    assert (tmp_path / "relationship_state.json.v3.bak").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 4
