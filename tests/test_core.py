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
# affinity / familiarity / trust
# ----------------------------------------------------------------------


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
            self.assertIn("bot:user:u1", loaded)
            self.assertAlmostEqual(loaded["bot:user:u1"].affinity_score, 66.5)
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
            self.assertAlmostEqual(loaded["bot:user:u1"].affinity_score, 77.0)

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

    def test_daily_affinity_cap(self) -> None:
        mgr = self._manager(
            affinity=AffinityCalculator(
                AffinityConfig(praise_gain=2.0, daily_cap=5.0)
            )
        )
        # 同一天内狂刷夸奖：好感最多 +5
        for i in range(10):
            snap = _run(
                mgr.record(_event(kind=models.KIND_PRAISE, ts=1000.0 + i * 10))
            )
        self.assertEqual(snap.affinity, 55)

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
            affinity=AffinityCalculator(
                AffinityConfig(praise_gain=2.0, daily_cap=5.0)
            )
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
        mgr = self._manager(
            mood_tracker=MoodTracker(frequent_after=1, streak_after=1)
        )
        snap = None
        for i in range(20):
            snap = _run(mgr.record(_event(text="在吗在吗在吗", ts=1000.0 + i)))
        assert snap is not None
        self.assertGreaterEqual(snap.affinity, 50)

    def test_command_not_counted(self) -> None:
        mgr = self._manager(
            mood_tracker=MoodTracker(frequent_after=1, streak_after=1)
        )
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
                _run(
                    mgr1.record(
                        _event(kind=models.KIND_PRAISE, ts=1000.0 + i * 200)
                    )
                )
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
                    _event(user_id="u1", text=f"m{i}", ts=1000.0 + i, event_id=f"u1-{i}")
                )
            )
        other = _run(
            mgr.record(_event(user_id="u2", text="first", ts=1010.0, event_id="u2-1"))
        )
        self.assertLess(other.willingness, 100)
        pressure = mgr._mood.peek("bot:group:g1:user:u2", now=1010.0)
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
