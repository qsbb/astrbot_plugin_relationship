"""凝心溯溪-情 核心逻辑测试。

不依赖 AstrBot 运行时，时间与随机源全部注入，可离线运行：
    python -m unittest discover -s tests -v
或  python -m pytest -q
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import models  # noqa: E402
from core.affinity import AffinityCalculator, AffinityConfig  # noqa: E402
from core.config import (  # noqa: E402
    affinity_config,
    decay_config,
    familiarity_config,
    mood_kwargs,
    trust_config,
)
from core.decay import DecayConfig, apply_decay  # noqa: E402
from core.familiarity import FamiliarityCalculator, FamiliarityConfig  # noqa: E402
from core.manager import RelationshipStateManager  # noqa: E402
from core.models import (  # noqa: E402
    InteractionEvent,
    RelationshipScope,
    UserRelationState,
)
from core.mood import (  # noqa: E402
    MOOD_ANNOYED,
    MOOD_LAZY,
    MOOD_NORMAL,
    MoodTracker,
)
from core.policy import build_snapshot  # noqa: E402
from core.repository import SCHEMA_VERSION, JsonRepository, MemoryRepository  # noqa: E402
from core.trust import TrustCalculator  # noqa: E402


class _FixedRng:
    """固定随机源：value < silence_chance 时必静默。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _event(
    text: str = "你好",
    ts: float = 1000.0,
    kind: str = models.KIND_MESSAGE,
    user_id: str = "u1",
    group_id: str | None = "g1",
    **kwargs,
) -> InteractionEvent:
    return InteractionEvent(
        bot_id="bot",
        user_id=user_id,
        group_id=group_id,
        text=text,
        timestamp=ts,
        kind=kind,
        **kwargs,
    )


# ----------------------------------------------------------------------
# mood：迁移行为回归
# ----------------------------------------------------------------------


class MoodRegressionTest(unittest.TestCase):
    """与旧插件 MoodTracker v0.6.0 行为一致性回归。"""

    def test_normal_when_calm(self) -> None:
        tracker = MoodTracker()
        decision = tracker.evaluate("s", "早上好", now=100.0)
        self.assertEqual(decision.mood, MOOD_NORMAL)
        self.assertEqual(decision.willingness, 100)
        self.assertFalse(decision.should_silence)

    def test_frequency_penalty_lowers_willingness(self) -> None:
        tracker = MoodTracker(window_seconds=300, frequent_after=6)
        decision = None
        for i in range(12):
            decision = tracker.evaluate("s", f"消息{i}", now=100.0 + i)
        assert decision is not None
        self.assertLess(decision.willingness, 100)
        self.assertIn(decision.mood, (MOOD_LAZY, MOOD_ANNOYED))

    def test_repeat_text_penalty(self) -> None:
        tracker = MoodTracker(frequent_after=100, streak_after=100)
        decision = None
        for i in range(4):
            decision = tracker.evaluate("s", "在吗在吗", now=100.0 + i * 20)
        assert decision is not None
        # 第 4 次重复：excess=3 → 45 分惩罚
        self.assertEqual(decision.repeat_count, 4)
        self.assertLessEqual(decision.willingness, 55)

    def test_window_recovery(self) -> None:
        tracker = MoodTracker(window_seconds=300, frequent_after=6, streak_after=100)
        for i in range(12):
            tracker.evaluate("s", f"m{i}", now=100.0 + i)
        # 时间流逝超出窗口后意愿恢复
        later = tracker.evaluate("s", "新话题", now=100.0 + 12 + 400)
        self.assertEqual(later.mood, MOOD_NORMAL)
        self.assertEqual(later.interaction_count, 1)

    def test_command_never_hard_silenced(self) -> None:
        rng = _FixedRng(0.0)  # 必中静默概率
        tracker = MoodTracker(
            frequent_after=1, streak_after=1, silence_chance_percent=100, rng=rng
        )
        decision = None
        for i in range(30):
            decision = tracker.evaluate("s", "/rel status", now=100.0 + i)
        assert decision is not None
        self.assertFalse(decision.should_silence)

    def test_urgent_never_hard_silenced(self) -> None:
        rng = _FixedRng(0.0)
        tracker = MoodTracker(
            frequent_after=1, streak_after=1, silence_chance_percent=100, rng=rng
        )
        decision = None
        for i in range(30):
            decision = tracker.evaluate("s", "救命 急用", now=100.0 + i)
        assert decision is not None
        self.assertFalse(decision.should_silence)

    def test_consecutive_silence_cap(self) -> None:
        rng = _FixedRng(0.0)
        tracker = MoodTracker(
            frequent_after=1,
            streak_after=1,
            silence_chance_percent=100,
            max_consecutive_silences=2,
            rng=rng,
        )
        silences = []
        for i in range(30):
            d = tracker.evaluate("s", f"骚扰消息骚扰消息{i % 2}", now=100.0 + i)
            silences.append(d.should_silence)
        # 连续静默不超过 2 次
        self.assertLessEqual(sum(silences), 2)
        # record_reply 解除计数后允许再次静默
        tracker.record_reply("s")
        d = tracker.evaluate("s", "骚扰消息骚扰消息0", now=140.0)
        self.assertTrue(d.should_silence)

    def test_peek_is_read_only(self) -> None:
        tracker = MoodTracker()
        for i in range(5):
            tracker.evaluate("s", f"m{i}", now=100.0 + i)
        before = tracker.stats("s", now=110.0)
        peek = tracker.peek("s", now=110.0)
        after = tracker.stats("s", now=110.0)
        self.assertEqual(before, after)
        self.assertFalse(peek.should_silence)

    def test_scope_isolation(self) -> None:
        tracker = MoodTracker(frequent_after=1, streak_after=100)
        for i in range(10):
            tracker.evaluate("group:g1", f"m{i}", now=100.0 + i)
        other = tracker.evaluate("group:g2", "第一条", now=115.0)
        self.assertEqual(other.mood, MOOD_NORMAL)
        self.assertEqual(other.interaction_count, 1)


# ----------------------------------------------------------------------
# config / affinity / familiarity / trust
# ----------------------------------------------------------------------


class ConfigTest(unittest.TestCase):
    def test_invalid_values_fall_back_or_are_bounded(self) -> None:
        mood = mood_kwargs({"MOOD_WINDOW_SECONDS": "bad", "MOOD_LAZY_SCORE": 999})
        self.assertEqual(mood["window_seconds"], 300)
        self.assertEqual(mood["lazy_score"], 100)
        cfg = affinity_config(
            {
                "AFFINITY_MESSAGE_GAIN": float("nan"),
                "AFFINITY_OFFENSE_PENALTY": 5,
                "AFFINITY_DAILY_CAP": -1,
            }
        )
        self.assertEqual(cfg.message_gain, 0.2)
        self.assertEqual(cfg.offense_penalty, 0.0)
        self.assertEqual(cfg.daily_cap, 0.0)

    def test_int_inf_nan_and_domain_configs_are_safe(self) -> None:
        self.assertEqual(
            mood_kwargs({"MOOD_WINDOW_SECONDS": float("inf")})["window_seconds"], 300
        )
        self.assertEqual(
            mood_kwargs({"MOOD_WINDOW_SECONDS": float("nan")})["window_seconds"], 300
        )

        trust = trust_config(
            {
                "TRUST_PROMISE_KEPT_GAIN": -3,
                "TRUST_PROMISE_BROKEN_PENALTY": 5,
                "TRUST_OFFENSE_PENALTY": -999,
            }
        )
        self.assertEqual(trust.promise_kept_gain, 0.0)
        self.assertEqual(trust.promise_broken_penalty, 0.0)
        self.assertEqual(trust.offense_penalty, -100.0)

        familiarity = familiarity_config(
            {
                "FAMILIARITY_BASE_GAIN": -1,
                "FAMILIARITY_DIMINISH_CURVE": 0,
                "FAMILIARITY_COOLDOWN_SECONDS": 10**9,
            }
        )
        self.assertEqual(familiarity.base_gain, 0.0)
        self.assertEqual(familiarity.diminish_curve, 0.01)
        self.assertEqual(familiarity.cooldown_seconds, 86400.0)

        decay = decay_config(
            {
                "DECAY_AFFINITY_REGRESSION_PER_DAY": -1,
                "DECAY_TRUST_REGRESSION_PER_DAY": 2,
            }
        )
        self.assertEqual(decay.affinity_regression_per_day, 0.0)
        self.assertEqual(decay.trust_regression_per_day, 1.0)


class AffinityTest(unittest.TestCase):
    def test_praise_and_offense(self) -> None:
        calc = AffinityCalculator()
        state = UserRelationState()
        up = calc.compute(_event(kind=models.KIND_PRAISE), state)
        down = calc.compute(_event(kind=models.KIND_OFFENSE), state)
        self.assertGreater(up.affinity, 0)
        self.assertLess(down.affinity, 0)

    def test_command_excluded(self) -> None:
        calc = AffinityCalculator()
        delta = calc.compute(_event(text="/rel status"), UserRelationState())
        self.assertTrue(delta.is_zero())

    def test_message_cooldown(self) -> None:
        calc = AffinityCalculator(AffinityConfig(message_cooldown_seconds=60.0))
        state = UserRelationState()
        first = calc.compute(_event(ts=1000.0), state)
        state.last_event_at = 1000.0
        second = calc.compute(_event(ts=1010.0), state)
        self.assertGreater(first.affinity, 0)
        self.assertEqual(second.affinity, 0.0)

    def test_non_whitelist_stays_in_friend_band(self) -> None:
        calc = AffinityCalculator(
            AffinityConfig(message_cooldown_seconds=0.0, non_whitelist_ceiling=68.0)
        )
        state = UserRelationState(affinity_score=68.0)
        delta = calc.compute(_event(user_id="ordinary"), state)
        self.assertEqual(delta.affinity, 0.0)

    def test_whitelist_requires_trust_and_familiarity(self) -> None:
        calc = AffinityCalculator(
            AffinityConfig(
                message_cooldown_seconds=0.0,
                whitelist_user_ids=("trusted",),
                whitelist_trust_gate=65.0,
                whitelist_familiarity_gate=25.0,
            )
        )
        immature = UserRelationState(trust_score=60.0, familiarity_score=10.0)
        ready = UserRelationState(trust_score=70.0, familiarity_score=30.0)
        self.assertLessEqual(
            calc.compute(_event(user_id="trusted"), immature).affinity, 0.1
        )
        self.assertGreater(calc.compute(_event(user_id="trusted"), ready).affinity, 0.1)

    def test_positive_semantic_event_respects_ceiling_and_gates(self) -> None:
        calc = AffinityCalculator(
            AffinityConfig(praise_gain=10.0, non_whitelist_ceiling=68.0)
        )
        delta = calc.compute(
            _event(kind=models.KIND_PRAISE), UserRelationState(affinity_score=67.0)
        )
        self.assertEqual(delta.affinity, 1.0)
        gated = AffinityCalculator(
            AffinityConfig(praise_gain=10.0, whitelist_user_ids=("trusted",))
        )
        delta = gated.compute(
            _event(kind=models.KIND_PRAISE, user_id="trusted"),
            UserRelationState(affinity_score=60.0),
        )
        self.assertEqual(delta.affinity, 0.1)

    def test_high_affinity_cannot_be_message_farmed(self) -> None:
        calc = AffinityCalculator(
            AffinityConfig(
                message_cooldown_seconds=0.0,
                whitelist_user_ids=("trusted",),
                high_affinity_threshold=75.0,
            )
        )
        state = UserRelationState(
            affinity_score=80.0, trust_score=80.0, familiarity_score=80.0
        )
        self.assertEqual(calc.compute(_event(user_id="trusted"), state).affinity, 0.0)


class FamiliarityTest(unittest.TestCase):
    def test_monotonic_and_diminishing(self) -> None:
        calc = FamiliarityCalculator(FamiliarityConfig(cooldown_seconds=0.0))
        low = calc.compute(_event(), UserRelationState(familiarity_score=0.0))
        high = calc.compute(_event(), UserRelationState(familiarity_score=80.0))
        self.assertGreater(low.familiarity, high.familiarity)
        self.assertGreaterEqual(high.familiarity, 0.0)

    def test_cap_at_full(self) -> None:
        calc = FamiliarityCalculator(FamiliarityConfig(cooldown_seconds=0.0))
        delta = calc.compute(_event(), UserRelationState(familiarity_score=100.0))
        self.assertEqual(delta.familiarity, 0.0)


class TrustTest(unittest.TestCase):
    def test_only_explicit_events(self) -> None:
        calc = TrustCalculator()
        state = UserRelationState()
        none = calc.compute(_event(kind=models.KIND_MESSAGE), state)
        kept = calc.compute(_event(kind=models.KIND_PROMISE_KEPT), state)
        broken = calc.compute(_event(kind=models.KIND_PROMISE_BROKEN), state)
        self.assertTrue(none.is_zero())
        self.assertGreater(kept.trust, 0)
        self.assertLess(broken.trust, 0)


# ----------------------------------------------------------------------
# decay
# ----------------------------------------------------------------------


class DecayTest(unittest.TestCase):
    def test_regression_toward_baseline(self) -> None:
        state = UserRelationState(affinity_score=90.0, trust_score=20.0)
        state.last_event_at = 0.0
        apply_decay(state, now=30 * 86400.0, config=DecayConfig())
        self.assertLess(state.affinity_score, 90.0)
        self.assertGreater(state.affinity_score, 50.0)
        self.assertGreater(state.trust_score, 20.0)
        self.assertLess(state.trust_score, 50.0)

    def test_familiarity_not_decayed(self) -> None:
        state = UserRelationState(familiarity_score=60.0)
        state.last_event_at = 0.0
        apply_decay(state, now=100 * 86400.0)
        self.assertEqual(state.familiarity_score, 60.0)

    def test_time_additivity(self) -> None:
        """分两次结算与一次结算结果一致。"""
        cfg = DecayConfig()
        a = UserRelationState(affinity_score=90.0)
        a.last_event_at = 0.0
        apply_decay(a, now=10 * 86400.0, config=cfg)
        a.last_event_at = 10 * 86400.0
        apply_decay(a, now=20 * 86400.0, config=cfg)

        b = UserRelationState(affinity_score=90.0)
        b.last_event_at = 0.0
        apply_decay(b, now=20 * 86400.0, config=cfg)
        self.assertAlmostEqual(a.affinity_score, b.affinity_score, places=6)


# ----------------------------------------------------------------------
# repository：持久化与版本迁移
# ----------------------------------------------------------------------


class RepositoryTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = JsonRepository(Path(tmp) / "rel.json")
            state = UserRelationState(
                affinity_score=66.5, trust_score=40.0, familiarity_score=12.0
            )
            repo.save_all({"bot:user:u1": state})
            loaded = repo.load_all()
            key = "persona:default:account:bot:user:u1"
            self.assertIn(key, loaded)
            self.assertAlmostEqual(loaded[key].affinity_score, 66.5)
            payload = json.loads((Path(tmp) / "rel.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], SCHEMA_VERSION)

    def test_missing_file(self) -> None:
        repo = JsonRepository(Path(tempfile.gettempdir()) / "not_exist_rel_x.json")
        self.assertEqual(repo.load_all(), {})

    def test_migrate_version0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rel.json"
            # 版本 0：裸 users dict，无 schema_version
            path.write_text(
                json.dumps({"bot:user:u1": {"affinity_score": 77.0}}),
                encoding="utf-8",
            )
            loaded = JsonRepository(path).load_all()
            key = "persona:default:account:bot:user:u1"
            self.assertAlmostEqual(loaded[key].affinity_score, 77.0)

    def test_future_schema_is_safe_empty_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rel.json"
            path.write_text(
                json.dumps(
                    {"schema_version": 999, "users": {"bad": {"affinity_score": 99}}}
                ),
                encoding="utf-8",
            )
            repo = JsonRepository(path)
            self.assertEqual(repo.load_all(), {})
            self.assertTrue(repo.write_blocked)
            with self.assertRaises(OSError):
                repo.save_all({"bot:user:u1": UserRelationState()})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["schema_version"], 999
            )

    def test_negative_schema_is_preserved_and_write_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rel.json"
            original = {
                "schema_version": -1,
                "users": {"unknown": {"affinity_score": 99}},
            }
            path.write_text(json.dumps(original), encoding="utf-8")

            repo = JsonRepository(path)
            self.assertEqual(repo.load_all(), {})
            self.assertTrue(repo.write_blocked)
            with self.assertRaises(OSError):
                repo.save_all({"bot:user:u1": UserRelationState()})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_corrupted_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rel.json"
            path.write_text("{broken json", encoding="utf-8")
            self.assertEqual(JsonRepository(path).load_all(), {})


# ----------------------------------------------------------------------
# manager：合并、限幅、作用域隔离
# ----------------------------------------------------------------------


class ManagerTest(unittest.TestCase):
    def _manager(self, **kwargs) -> RelationshipStateManager:
        defaults = dict(
            repository=MemoryRepository(),
            save_interval_seconds=0.0,
            clock=lambda: 1000.0,
        )
        defaults.update(kwargs)
        return RelationshipStateManager(**defaults)

    def test_record_returns_snapshot(self) -> None:
        mgr = self._manager()
        snap = _run(mgr.record(_event(kind=models.KIND_PRAISE)))
        self.assertGreaterEqual(snap.affinity, 50)
        self.assertEqual(snap.mood, MOOD_NORMAL)
        self.assertIsInstance(snap.prompt_fragment, str)

    def test_bound_accounts_share_without_live_alias_mirrors(self) -> None:
        mgr = self._manager()
        _run(mgr.record(_event(user_id="u1", event_id="u1-first")))
        _run(mgr.record(_event(user_id="u2", event_id="u2-first", ts=1200.0)))
        aliases = (
            "persona:default:account:bot:user:u1",
            "persona:default:account:bot:user:u2",
        )

        canonical_key = "persona:default:person:summer"
        self.assertTrue(_run(mgr.bind_identity(canonical_key, aliases)))

        canonical = mgr._states[canonical_key]
        interaction_count = canonical.interaction_count
        self.assertNotIn(aliases[0], mgr._states)
        self.assertNotIn(aliases[1], mgr._states)

        _run(
            mgr.record(
                _event(
                    user_id="u1",
                    event_id="u1-linked",
                    ts=1400.0,
                    person_id="summer",
                    state_alias_keys=aliases,
                )
            )
        )
        updated = mgr._states[canonical_key]
        self.assertGreater(updated.interaction_count, interaction_count)
        self.assertNotIn(aliases[0], mgr._states)
        self.assertNotIn(aliases[1], mgr._states)

    def test_explicit_identity_merge_combines_person_and_account_states(self) -> None:
        mgr = self._manager()
        target = "persona:default:person:summer"
        source = "persona:default:person:work-account"
        account = "persona:default:account:bot:user:u2"
        mgr._states[target] = UserRelationState(
            affinity_score=60.0,
            daily_affinity_positive_used=1.5,
            daily_affinity_negative_used=0.25,
            daily_anchor_day="1970-01-01",
            interaction_count=10,
            last_event_at=1000.0,
            extra={"trusted_semantic_evidence_mass": 3.0},
        )
        mgr._states[source] = UserRelationState(
            affinity_score=40.0,
            daily_affinity_positive_used=0.5,
            daily_affinity_negative_used=0.75,
            daily_anchor_day="1970-01-01",
            interaction_count=2,
            last_event_at=900.0,
            extra={"trusted_semantic_evidence_mass": 2.0},
        )
        mgr._states[account] = UserRelationState(
            affinity_score=50.0,
            interaction_count=3,
            last_event_at=950.0,
        )

        changed = _run(mgr.merge_identity_states(((target, (source, account)),)))

        self.assertEqual(changed, (target,))
        self.assertEqual(mgr._states[target].interaction_count, 15)
        self.assertAlmostEqual(mgr._states[target].affinity_score, 55.3333333333)
        self.assertEqual(mgr._states[target].daily_affinity_positive_used, 2.0)
        self.assertEqual(mgr._states[target].daily_affinity_negative_used, 1.0)
        self.assertEqual(
            mgr._states[target].extra["trusted_semantic_evidence_mass"], 5.0
        )
        self.assertNotIn(source, mgr._states)
        self.assertNotIn(account, mgr._states)

    def test_normal_binding_keeps_existing_canonical_state_authoritative(self) -> None:
        mgr = self._manager()
        target = "persona:default:person:summer"
        account = "persona:default:account:bot:user:u2"
        mgr._states[target] = UserRelationState(
            affinity_score=65.0,
            interaction_count=10,
            last_event_at=1000.0,
        )
        mgr._states[account] = UserRelationState(
            affinity_score=10.0,
            interaction_count=5,
            last_event_at=900.0,
        )

        self.assertTrue(_run(mgr.bind_identity(target, (account,))))

        self.assertEqual(mgr._states[target].interaction_count, 10)
        self.assertEqual(mgr._states[target].affinity_score, 65.0)
        self.assertNotIn(account, mgr._states)

    def test_whitelist_override_applies_prior_once_without_erasing_history(
        self,
    ) -> None:
        mgr = self._manager()
        scope = RelationshipScope(
            bot_id="bot",
            user_id="u1",
            person_id="summer",
            relationship_profile_id="default",
        )
        key = scope.user_key
        mgr._states[key] = UserRelationState(
            affinity_score=72.0,
            trust_score=68.0,
            familiarity_score=44.0,
            daily_affinity_positive_used=2.5,
            interaction_count=37,
            last_event_at=900.0,
            extra={"trusted_semantic_evidence_mass": 6.0},
        )

        snapshot = _run(
            mgr.apply_initial_prior(scope, "fond", allow_active_relationship=True)
        )

        state = mgr._states[key]
        self.assertEqual(snapshot.affinity, 64)
        self.assertEqual(state.trust_score, 60)
        self.assertEqual(state.familiarity_score, 25)
        self.assertEqual(state.interaction_count, 37)
        self.assertEqual(state.last_event_at, 900.0)
        self.assertEqual(state.daily_affinity_positive_used, 2.5)
        self.assertEqual(state.extra["trusted_semantic_evidence_mass"], 6.0)
        with self.assertRaisesRegex(ValueError, "INITIAL_PRIOR_ALREADY_APPLIED"):
            _run(
                mgr.apply_initial_prior(
                    scope, "acquainted", allow_active_relationship=True
                )
            )

    def test_active_relationship_prior_still_requires_explicit_override(self) -> None:
        mgr = self._manager()
        scope = RelationshipScope(
            bot_id="bot",
            user_id="u1",
            person_id="summer",
            relationship_profile_id="default",
        )
        mgr._states[scope.user_key] = UserRelationState(
            interaction_count=1, last_event_at=900.0
        )

        with self.assertRaisesRegex(ValueError, "RELATIONSHIP_ALREADY_ACTIVE"):
            _run(mgr.apply_initial_prior(scope, "fond"))

        self.assertEqual(mgr._states[scope.user_key].interaction_count, 1)
        self.assertEqual(mgr._states[scope.user_key].initial_prior, "")

    def test_merged_prior_marker_cannot_be_overridden_without_original_event(self) -> None:
        mgr = self._manager()
        target = "persona:default:person:summer"
        source = "persona:default:person:old-identity"
        mgr._states[source] = UserRelationState(
            affinity_score=64.0,
            trust_score=60.0,
            familiarity_score=25.0,
            initial_prior="fond",
            initial_prior_applied_at=900.0,
        )
        self.assertEqual(
            _run(mgr.merge_identity_states(((target, (source,)),))),
            (target,),
        )
        scope = RelationshipScope(
            bot_id="bot",
            user_id="u1",
            person_id="summer",
            relationship_profile_id="default",
        )

        with self.assertRaisesRegex(ValueError, "INITIAL_PRIOR_ALREADY_APPLIED"):
            _run(
                mgr.apply_initial_prior(
                    scope,
                    "acquainted",
                    allow_active_relationship=True,
                )
            )

    def test_explicit_merge_counts_identical_independent_person_states(self) -> None:
        mgr = self._manager()
        target = "persona:default:person:summer"
        source = "persona:default:person:work-account"
        state = UserRelationState(
            affinity_score=55.0,
            interaction_count=4,
            last_event_at=1000.0,
        )
        mgr._states[target] = UserRelationState.from_dict(state.as_dict())
        mgr._states[source] = UserRelationState.from_dict(state.as_dict())

        self.assertEqual(
            _run(mgr.merge_identity_states(((target, (source,)),))), (target,)
        )

        self.assertEqual(mgr._states[target].interaction_count, 8)
        self.assertNotIn(source, mgr._states)

    def test_person_id_is_used_for_affinity_whitelist(self) -> None:
        calculator = AffinityCalculator(
            AffinityConfig(
                praise_gain=2.0,
                whitelist_user_ids=("summer",),
                non_whitelist_ceiling=50.0,
            )
        )
        state = models.UserRelationState()

        delta = calculator.compute(
            _event(
                user_id="raw-platform-id",
                person_id="summer",
                kind=models.KIND_PRAISE,
            ),
            state,
        )

        self.assertGreater(delta.affinity, 0.0)

    def test_bound_account_uid_alias_is_used_for_affinity_whitelist(self) -> None:
        calculator = AffinityCalculator(
            AffinityConfig(
                praise_gain=2.0,
                whitelist_user_ids=("default/raw-platform-id",),
                non_whitelist_ceiling=50.0,
            )
        )
        event = _event(
            user_id="another-platform-id",
            person_id="summer",
            whitelist_alias_ids=("raw-platform-id", "raw-platform-id", ""),
            kind=models.KIND_PRAISE,
        )
        state = models.UserRelationState(
            affinity_score=50.0,
            trust_score=80.0,
            familiarity_score=40.0,
        )

        self.assertEqual(
            event.relationship_whitelist_ids,
            (
                "summer",
                "raw-platform-id",
                "default/summer",
                "default/raw-platform-id",
            ),
        )
        self.assertEqual(
            event.scope.whitelist_alias_ids,
            ("raw-platform-id", "raw-platform-id", ""),
        )
        self.assertEqual(
            RelationshipStateManager._event_with_timestamp(
                event, 1234.0
            ).whitelist_alias_ids,
            event.whitelist_alias_ids,
        )
        self.assertEqual(calculator.compute(event, state).affinity, 2.0)

    def test_unverified_current_uid_is_not_a_person_whitelist_alias(self) -> None:
        calculator = AffinityCalculator(
            AffinityConfig(
                praise_gain=2.0,
                whitelist_user_ids=("unverified-uid",),
                non_whitelist_ceiling=50.0,
                whitelist_trust_gate=0.0,
                whitelist_familiarity_gate=0.0,
            )
        )
        event = _event(
            user_id="unverified-uid",
            person_id="summer",
            kind=models.KIND_PRAISE,
        )
        state = models.UserRelationState(
            affinity_score=50.0,
            trust_score=80.0,
            familiarity_score=40.0,
        )

        self.assertEqual(
            event.relationship_whitelist_ids,
            ("summer", "default/summer"),
        )
        self.assertEqual(calculator.compute(event, state).affinity, 0.0)

    def test_daily_affinity_cap(self) -> None:
        mgr = self._manager(
            affinity=AffinityCalculator(AffinityConfig(praise_gain=2.0, daily_cap=5.0))
        )
        # 同一天内狂刷夸奖：好感最多 +5
        for i in range(10):
            snap = _run(mgr.record(_event(kind=models.KIND_PRAISE, ts=1000.0 + i * 10)))
        self.assertEqual(snap.affinity, 55)

    def test_future_timestamp_cannot_bypass_message_affinity_cooldown(self) -> None:
        repo = MemoryRepository()
        mgr = self._manager(
            repository=repo,
            affinity=AffinityCalculator(
                AffinityConfig(message_gain=1.0, message_cooldown_seconds=60.0)
            ),
            familiarity=FamiliarityCalculator(FamiliarityConfig(base_gain=0.0)),
        )
        _run(mgr.record(_event(ts=1000.0, event_id="normal-1")))
        _run(mgr.record(_event(ts=1000000.0, event_id="normal-future")))

        state = mgr._states[RelationshipScope("bot", "u1", "g1").user_key]
        self.assertEqual(state.affinity_score, 51.0)
        self.assertEqual(state.last_event_at, 1000.0)
        self.assertEqual(
            [record.timestamp for record in repo.load_events()], [1000.0, 1000.0]
        )

    def test_future_timestamp_cannot_bypass_familiarity_cooldown(self) -> None:
        repo = MemoryRepository()
        mgr = self._manager(
            repository=repo,
            affinity=AffinityCalculator(
                AffinityConfig(message_gain=0.0, message_cooldown_seconds=0.0)
            ),
            familiarity=FamiliarityCalculator(
                FamiliarityConfig(base_gain=1.0, cooldown_seconds=120.0)
            ),
        )
        _run(mgr.record(_event(ts=1000.0, event_id="familiarity-1")))
        _run(mgr.record(_event(ts=1000000.0, event_id="familiarity-future")))

        state = mgr._states[RelationshipScope("bot", "u1", "g1").user_key]
        self.assertEqual(state.familiarity_score, 1.0)
        self.assertEqual(state.last_event_at, 1000.0)
        self.assertEqual(
            [record.timestamp for record in repo.load_events()], [1000.0, 1000.0]
        )

    def test_positive_and_negative_affinity_caps_are_independent(self) -> None:
        mgr = self._manager(
            affinity=AffinityCalculator(
                AffinityConfig(
                    praise_gain=3.0,
                    offense_penalty=-4.0,
                    daily_cap=5.0,
                    daily_negative_cap=2.0,
                )
            )
        )
        for i in range(3):
            _run(
                mgr.record(
                    _event(
                        kind=models.KIND_PRAISE,
                        ts=1000.0 + i,
                        event_id=f"p{i}",
                    )
                )
            )
        for i in range(3):
            snap = _run(
                mgr.record(
                    _event(
                        kind=models.KIND_OFFENSE,
                        ts=1010.0 + i,
                        event_id=f"o{i}",
                    )
                )
            )
        self.assertEqual(snap.affinity, 53)

    def test_daily_cap_resets_next_day(self) -> None:
        mgr = self._manager(
            affinity=AffinityCalculator(AffinityConfig(praise_gain=2.0, daily_cap=5.0)),
            clock=lambda: 1000.0 + 86400.0 * 2,
        )
        for i in range(10):
            _run(mgr.record(_event(kind=models.KIND_PRAISE, ts=1000.0 + i * 10)))
        # 次日额度恢复
        snap = _run(
            mgr.record(_event(kind=models.KIND_PRAISE, ts=1000.0 + 86400.0 * 2))
        )
        self.assertGreater(snap.affinity, 55)

    def test_user_scope_isolated(self) -> None:
        mgr = self._manager()
        for i in range(5):
            _run(
                mgr.record(
                    _event(kind=models.KIND_PRAISE, user_id="u1", ts=1000.0 + i * 200)
                )
            )
        snap_other = _run(mgr.get_snapshot("bot", "u2", "g1"))
        self.assertEqual(snap_other.affinity, 50)
        self.assertEqual(snap_other.familiarity, 0)

    def test_session_scope_group_vs_private(self) -> None:
        """同一用户：群聊刷疲劳不影响私聊情绪。"""
        mgr = self._manager(
            mood_tracker=MoodTracker(frequent_after=1, streak_after=100)
        )
        for i in range(10):
            _run(mgr.record(_event(group_id="g1", ts=1000.0 + i)))
        snap_private = _run(mgr.record(_event(group_id=None, ts=1015.0)))
        self.assertEqual(snap_private.mood, MOOD_NORMAL)

    def test_mood_does_not_touch_affinity(self) -> None:
        """维度隔离：狂轰滥炸把情绪打到 annoyed，好感不下降。"""
        mgr = self._manager(mood_tracker=MoodTracker(frequent_after=1, streak_after=1))
        snap = None
        for i in range(20):
            snap = _run(mgr.record(_event(text="在吗在吗在吗", ts=1000.0 + i)))
        assert snap is not None
        self.assertGreaterEqual(snap.affinity, 50)

    def test_command_not_counted(self) -> None:
        mgr = self._manager(mood_tracker=MoodTracker(frequent_after=1, streak_after=1))
        for i in range(20):
            snap = _run(
                mgr.record(
                    _event(text="/rel status", kind=models.KIND_COMMAND, ts=1000.0 + i)
                )
            )
        self.assertEqual(snap.mood, MOOD_NORMAL)
        self.assertFalse(snap.should_silence)
        inner = _run(mgr.get_snapshot("bot", "u1", "g1"))
        self.assertEqual(inner.familiarity, 0)

    def test_reset(self) -> None:
        mgr = self._manager()
        _run(mgr.record(_event(kind=models.KIND_PRAISE)))
        _run(mgr.reset(RelationshipScope(bot_id="bot", user_id="u1", group_id="g1")))
        snap = _run(mgr.get_snapshot("bot", "u1", "g1"))
        self.assertEqual(snap.affinity, 50)
        self.assertEqual(snap.familiarity, 0)

    def test_persistence_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rel.json"
            mgr1 = self._manager(repository=JsonRepository(path))
            for i in range(3):
                _run(mgr1.record(_event(kind=models.KIND_PRAISE, ts=1000.0 + i * 200)))
            mgr1._flush()
            # 模拟重启
            mgr2 = self._manager(repository=JsonRepository(path))
            snap = _run(mgr2.get_snapshot("bot", "u1", "g1"))
            self.assertGreater(snap.affinity, 50)
            self.assertGreater(snap.familiarity, 0)
            # 短期情绪不持久化
            self.assertEqual(snap.mood, MOOD_NORMAL)

    def test_get_snapshot_read_only(self) -> None:
        mgr = self._manager()
        _run(mgr.record(_event()))
        s1 = _run(mgr.get_snapshot("bot", "u1", "g1"))
        s2 = _run(mgr.get_snapshot("bot", "u1", "g1"))
        self.assertEqual(s1.as_dict(), s2.as_dict())

    def test_command_has_no_ledger_or_persistence_side_effect(self) -> None:
        repo = MemoryRepository()
        mgr = self._manager(repository=repo)
        before = _run(mgr.get_snapshot("bot", "u1", "g1")).as_dict()
        after = _run(
            mgr.record(_event(text="/rel status", kind=models.KIND_COMMAND))
        ).as_dict()
        self.assertEqual(before, after)
        self.assertEqual(repo.load_events(), [])

    def test_event_ledger_is_idempotent_and_omits_text(self) -> None:
        repo = MemoryRepository()
        mgr = self._manager(repository=repo)
        event = _event(
            text="不应进入账本的原文",
            kind=models.KIND_PRAISE,
            event_id="evt-1",
            evidence_refs=("workflow:42",),
        )
        first = _run(mgr.record(event))
        second = _run(mgr.record(event))
        self.assertEqual(first.as_dict(), second.as_dict())
        records = repo.load_events()
        self.assertEqual(len(records), 1)
        self.assertNotIn("text", records[0].as_dict())
        self.assertEqual(records[0].evidence_refs, ("workflow:42",))

    def test_event_identity_is_namespaced_and_future_timestamp_is_clamped(self) -> None:
        repo = MemoryRepository()
        mgr = self._manager(repository=repo)
        _run(mgr.record(_event(user_id="u1", event_id="same", ts=10**20)))
        _run(mgr.record(_event(user_id="u2", event_id="same", ts=10**20)))
        records = repo.load_events()
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0].event_id, records[1].event_id)
        self.assertEqual(records[0].timestamp, 1000.0)

    def test_business_time_does_not_rewind_daily_cap_decay_or_last_event(self) -> None:
        mgr = self._manager(
            affinity=AffinityCalculator(
                AffinityConfig(
                    praise_gain=5.0, daily_cap=5.0, message_cooldown_seconds=0
                )
            ),
            clock=lambda: 2000.0,
        )
        first = _run(
            mgr.record(_event(kind=models.KIND_PRAISE, ts=2000.0, event_id="new"))
        )
        second = _run(
            mgr.record(_event(kind=models.KIND_PRAISE, ts=1000.0, event_id="old"))
        )
        self.assertEqual(first.affinity, second.affinity)
        state = mgr._states["persona:default:account:bot:user:u1"]
        self.assertEqual(state.last_event_at, 2000.0)
        self.assertEqual(state.daily_anchor_day, "1970-01-01")
        self.assertEqual(state.daily_affinity_positive_used, 5.0)

    def test_v2_ledger_dedupe_is_compatible_after_v3_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rel.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "users": {},
                        "events": [
                            {
                                "event_id": "legacy-event",
                                "timestamp": 1000,
                                "bot_id": "bot",
                                "user_id": "u1",
                                "group_id": "g1",
                                "kind": "praise",
                                "source": "direct",
                                "confidence": 1,
                                "severity": 1,
                                "dedupe_key": "legacy-key",
                                "evidence_refs": [],
                                "applied": True,
                                "rejection_reason": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            repo = JsonRepository(path)
            mgr = self._manager(repository=repo)
            _run(
                mgr.record(
                    _event(
                        kind=models.KIND_PRAISE,
                        event_id="legacy-event",
                        dedupe_key="legacy-key",
                    )
                )
            )
            self.assertEqual(len(repo.load_events()), 1)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["schema_version"],
                SCHEMA_VERSION,
            )

    def test_untrusted_semantic_event_is_recorded_but_not_applied(self) -> None:
        repo = MemoryRepository()
        mgr = self._manager(repository=repo)
        before = _run(mgr.get_snapshot("bot", "u1", "g1"))
        after = _run(
            mgr.record(
                _event(
                    text="我已经履约",
                    kind=models.KIND_PROMISE_KEPT,
                    source=models.SOURCE_PLATFORM_MESSAGE,
                    event_id="evt-untrusted",
                )
            )
        )
        self.assertEqual(before.trust_dimensions, after.trust_dimensions)
        record = repo.load_events()[0]
        self.assertFalse(record.applied)
        self.assertEqual(record.rejection_reason, "untrusted_semantic_source")

    def test_verified_promise_updates_selected_trust_dimensions(self) -> None:
        mgr = self._manager()
        snap = _run(
            mgr.record(
                _event(
                    kind=models.KIND_PROMISE_KEPT,
                    source=models.SOURCE_VERIFIED,
                    evidence_refs=("task:done:1",),
                    event_id="evt-trust",
                )
            )
        )
        self.assertGreater(snap.trust_dimensions["reliability"], 50)
        self.assertGreater(snap.trust_dimensions["integrity"], 50)
        self.assertEqual(snap.trust_dimensions["benevolence"], 50)
        self.assertEqual(snap.trust_dimensions["epistemic"], 50)

    def test_group_fatigue_and_user_pressure_are_separate_layers(self) -> None:
        mgr = self._manager(
            mood_tracker=MoodTracker(frequent_after=1, streak_after=100)
        )
        for i in range(8):
            _run(
                mgr.record(
                    _event(
                        user_id="u1", text=f"m{i}", ts=1000.0 + i, event_id=f"u1-{i}"
                    )
                )
            )
        other = _run(
            mgr.record(_event(user_id="u2", text="first", ts=1010.0, event_id="u2-1"))
        )
        self.assertLess(other.willingness, 100)
        pressure = mgr._mood.peek(
            "persona:default:session:bot:group:g1:user:u2", now=1010.0
        )
        self.assertEqual(pressure.interaction_count, 1)

    def test_mood_can_be_disabled_without_disabling_long_term(self) -> None:
        mgr = self._manager(
            mood_enabled=False,
            mood_tracker=MoodTracker(frequent_after=1, streak_after=1),
        )
        for i in range(20):
            snap = _run(
                mgr.record(
                    _event(kind=models.KIND_PRAISE, text="重复消息", ts=1000.0 + i)
                )
            )
        self.assertEqual(snap.mood, MOOD_NORMAL)
        self.assertEqual(snap.willingness, 100)
        self.assertGreater(snap.affinity, 50)


# ----------------------------------------------------------------------
# policy
# ----------------------------------------------------------------------


class PolicyTest(unittest.TestCase):
    def test_fragment_has_no_forbidden_words(self) -> None:
        from core.mood import MoodDecision

        for mood in (MOOD_NORMAL, MOOD_LAZY, MOOD_ANNOYED):
            for affinity in (10.0, 50.0, 90.0):
                snap = build_snapshot(
                    MoodDecision(mood=mood, willingness=50),
                    UserRelationState(
                        affinity_score=affinity,
                        trust_score=20.0,
                        familiarity_score=80.0,
                    ),
                )
                for word in ("系统", "分数", "本指令", "score"):
                    self.assertNotIn(word, snap.prompt_fragment)

    def test_silence_skips_fragment(self) -> None:
        from core.mood import MoodDecision

        snap = build_snapshot(
            MoodDecision(mood=MOOD_ANNOYED, willingness=10, should_silence=True),
            UserRelationState(),
        )
        self.assertTrue(snap.should_silence)
        self.assertEqual(snap.prompt_fragment, "")

    def test_style_mapping(self) -> None:
        from core.mood import MoodDecision

        lazy = build_snapshot(
            MoodDecision(mood=MOOD_LAZY, willingness=60), UserRelationState()
        )
        self.assertEqual(lazy.response_style, "short_casual")
        warm = build_snapshot(
            MoodDecision(),
            UserRelationState(affinity_score=90.0, familiarity_score=90.0),
        )
        self.assertEqual(warm.response_style, "warm_playful")


if __name__ == "__main__":
    unittest.main()
