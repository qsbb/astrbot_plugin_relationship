"""短期情绪与回复意愿（自 astrbot_plugin_conversation_flow v0.6.0 迁移）。

本模块按会话维护 0~100 的「回话意愿」。高频打扰、复读和过长的连续
对话会降低意愿；时间流逝后滑动窗口自然清空，意愿随之恢复。

命令、明显求助以及已经连续静默过多次的消息不会被硬静默。情绪只影响
回复意愿和简短程度，不允许借情绪攻击用户。

迁移约定：对外行为与旧版 MoodTracker 完全一致（信号、三档分数、
概率硬静默、连续静默上限、保护规则），仅做两处非行为性调整：
1) 构造参数支持从配置 dict 注入（见 manager）；
2) 时间与随机源可注入，便于离线测试。
"""

from __future__ import annotations

import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

MOOD_NORMAL = "normal"
MOOD_LAZY = "lazy"
MOOD_ANNOYED = "annoyed"

_URGENT_PATTERN = re.compile(
    r"救命|求助|帮帮我|帮我一下|急用|紧急|出事了|怎么办|help|urgent|emergency",
    re.IGNORECASE,
)
_NORMALIZE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass
class MoodDecision:
    """单轮情绪判定结果。"""

    mood: str = MOOD_NORMAL
    willingness: int = 100
    should_silence: bool = False
    reason: str = ""
    interaction_count: int = 0
    repeat_count: int = 0
    streak_count: int = 0

    @property
    def should_inject(self) -> bool:
        return self.mood != MOOD_NORMAL and not self.should_silence


@dataclass
class _MoodState:
    interactions: deque[float] = field(default_factory=deque)
    texts: deque[tuple[float, str]] = field(default_factory=deque)
    last_interaction_at: float = 0.0
    streak_count: int = 0
    consecutive_silences: int = 0
    last_seen_at: float = 0.0


class MoodTracker:
    """按会话追踪疲劳信号并计算本轮回复意愿。"""

    def __init__(
        self,
        window_seconds: int = 300,
        frequent_after: int = 6,
        streak_after: int = 8,
        streak_gap_seconds: int = 90,
        lazy_score: int = 72,
        annoyed_score: int = 45,
        silence_score: int = 25,
        silence_chance_percent: int = 45,
        max_consecutive_silences: int = 2,
        rng: Any = None,
    ) -> None:
        self._states: dict[str, _MoodState] = defaultdict(_MoodState)
        self._rng = rng or random.Random()
        self.update_config(
            window_seconds,
            frequent_after,
            streak_after,
            streak_gap_seconds,
            lazy_score,
            annoyed_score,
            silence_score,
            silence_chance_percent,
            max_consecutive_silences,
        )

    def update_config(
        self,
        window_seconds: int,
        frequent_after: int,
        streak_after: int,
        streak_gap_seconds: int,
        lazy_score: int,
        annoyed_score: int,
        silence_score: int,
        silence_chance_percent: int,
        max_consecutive_silences: int,
    ) -> None:
        """热更新阈值；已有状态保留并自然收敛。"""
        self._window = max(10, int(window_seconds))
        self._frequent_after = max(1, int(frequent_after))
        self._streak_after = max(1, int(streak_after))
        self._streak_gap = max(1, int(streak_gap_seconds))
        self._lazy_score = max(0, min(100, int(lazy_score)))
        self._annoyed_score = max(0, min(self._lazy_score, int(annoyed_score)))
        self._silence_score = max(0, min(self._annoyed_score, int(silence_score)))
        self._silence_chance = max(0, min(100, int(silence_chance_percent)))
        self._max_silences = max(0, int(max_consecutive_silences))

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = _NORMALIZE_PATTERN.sub("", (text or "").lower())
        return normalized[:120]

    def _prune(self, state: _MoodState, now: float) -> None:
        deadline = now - self._window
        while state.interactions and state.interactions[0] < deadline:
            state.interactions.popleft()
        while state.texts and state.texts[0][0] < deadline:
            state.texts.popleft()

    def _calculate(
        self, state: _MoodState, normalized_text: str
    ) -> tuple[int, int, list[str]]:
        interaction_count = len(state.interactions)
        repeat_count = 0
        if normalized_text:
            repeat_count = sum(
                1 for _ts, value in state.texts if value == normalized_text
            )

        score = 100
        reasons: list[str] = []

        excess_frequency = max(0, interaction_count - self._frequent_after)
        if excess_frequency:
            penalty = min(40, excess_frequency * 7)
            score -= penalty
            reasons.append(f"窗口内互动 {interaction_count} 次")

        # 当前文本也已经写入 texts，所以首次出现为 1，第二次起才扣分。
        excess_repeats = max(0, repeat_count - 1)
        if excess_repeats:
            penalty = min(60, excess_repeats * 15)
            score -= penalty
            reasons.append(f"相似内容重复 {repeat_count} 次")

        excess_streak = max(0, state.streak_count - self._streak_after)
        if excess_streak:
            penalty = min(35, excess_streak * 5)
            score -= penalty
            reasons.append(f"连续对话 {state.streak_count} 轮")

        return max(0, min(100, score)), repeat_count, reasons

    def evaluate(
        self, scope_key: str, user_text: str, now: float | None = None
    ) -> MoodDecision:
        """记录本轮互动并返回情绪判定。"""
        if not scope_key:
            return MoodDecision()
        current = time.time() if now is None else float(now)
        state = self._states[scope_key]
        self._prune(state, current)

        if (
            state.last_interaction_at
            and current - state.last_interaction_at <= self._streak_gap
        ):
            state.streak_count += 1
        else:
            state.streak_count = 1
        state.last_interaction_at = current
        state.last_seen_at = current
        state.interactions.append(current)

        normalized = self._normalize_text(user_text)
        if normalized:
            state.texts.append((current, normalized))

        score, repeat_count, reasons = self._calculate(state, normalized)
        if score <= self._annoyed_score:
            mood = MOOD_ANNOYED
        elif score <= self._lazy_score:
            mood = MOOD_LAZY
        else:
            mood = MOOD_NORMAL

        protected = (user_text or "").lstrip().startswith("/") or bool(
            _URGENT_PATTERN.search(user_text or "")
        )
        silence = False
        if (
            mood == MOOD_ANNOYED
            and score <= self._silence_score
            and not protected
            and self._silence_chance > 0
            and (
                self._max_silences <= 0
                or state.consecutive_silences < self._max_silences
            )
        ):
            silence = self._rng.random() * 100 < self._silence_chance

        if silence:
            state.consecutive_silences += 1
        reason = "、".join(reasons) if reasons else "互动节奏正常"
        return MoodDecision(
            mood=mood,
            willingness=score,
            should_silence=silence,
            reason=reason,
            interaction_count=len(state.interactions),
            repeat_count=repeat_count,
            streak_count=state.streak_count,
        )

    def peek(self, scope_key: str, now: float | None = None) -> MoodDecision:
        """只读评估当前情绪档位，不记录互动、不触发静默。

        新增方法（迁移时补充）：供只读查询与命令路径使用，
        对 evaluate 的行为无任何影响。
        """
        state = self._states.get(scope_key)
        if state is None:
            return MoodDecision()
        current = time.time() if now is None else float(now)
        self._prune(state, current)
        score, _repeat, reasons = self._calculate(state, "")
        if score <= self._annoyed_score:
            mood = MOOD_ANNOYED
        elif score <= self._lazy_score:
            mood = MOOD_LAZY
        else:
            mood = MOOD_NORMAL
        return MoodDecision(
            mood=mood,
            willingness=score,
            should_silence=False,
            reason="、".join(reasons) if reasons else "互动节奏正常",
            interaction_count=len(state.interactions),
            repeat_count=0,
            streak_count=state.streak_count,
        )

    def record_reply(self, scope_key: str) -> None:
        """记录一次实际回复，解除连续静默保护计数。"""
        state = self._states.get(scope_key)
        if state is not None:
            state.consecutive_silences = 0

    def stats(self, scope_key: str, now: float | None = None) -> dict[str, int]:
        """返回当前会话的窗口状态，不新增互动。"""
        state = self._states.get(scope_key)
        if state is None:
            return {"interactions": 0, "streak": 0, "consecutive_silences": 0}
        current = time.time() if now is None else float(now)
        self._prune(state, current)
        return {
            "interactions": len(state.interactions),
            "streak": state.streak_count,
            "consecutive_silences": state.consecutive_silences,
        }

    def cleanup_stale(
        self, ttl_seconds: float | None = None, now: float | None = None
    ) -> int:
        """清理长期无活动的会话，返回数量。"""
        current = time.time() if now is None else float(now)
        ttl = max(self._window, float(ttl_seconds or self._window * 2))
        stale = [
            key
            for key, state in self._states.items()
            if state.last_seen_at and current - state.last_seen_at > ttl
        ]
        for key in stale:
            self._states.pop(key, None)
        return len(stale)

    def reset(self, scope_key: str = "") -> None:
        """重置指定会话或全部情绪状态。"""
        if scope_key:
            self._states.pop(scope_key, None)
        else:
            self._states.clear()
