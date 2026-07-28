"""检测并抑制 bot 的服务式追问收尾。

现实问题：接入工具/知识类插件后，模型很容易每轮都用
"还需要我帮你查点别的吗？""有需要随时告诉我"这类句式收尾。
单看一轮没问题，连续多轮就变成机械的工具客服腔。

本模块只做两件事：

1. ``is_followup_offer``：判断一段 bot 回复是否以服务式追问收尾；
2. ``FollowupGuard``：按作用域统计连续追问轮次，给出抑制档位。

判定只看回复**尾部**，因为正文里的正常提问（澄清缺失信息、关心对方）
不应被抑制；只有收尾处的征询式话术才是要压住的对象。
全部基于本地正则与滑动计数，不调用 LLM。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

LEVEL_NONE = "none"
LEVEL_SOFT = "soft"
LEVEL_HARD = "hard"

_MAX_TRACKED_SCOPES = 512
_TAIL_CHARS = 80
_SHORT_TAIL_CHARS = 12

# 只按强句界切分，保留 ？ 以便识别疑问收尾
_TAIL_SPLIT = re.compile(r"[\n。！!；;]+")

# 疑问标记：收尾处出现才算在征询
_ASK_MARK = re.compile(r"[?？]|吗|呢")

# "需要我 / 要不要帮" 这类征询 + 服务动作的组合
_NEED_SERVICE = re.compile(
    r"(需要|要不要|用不用|想不想|是否需要|有没有需要)\s*"
    r"(我|帮|替|给我|再|继续|接着|其他|别的|更多)"
)

# 直接提议再做点什么，或询问是否还有别的事
_SERVICE_ASK = re.compile(
    r"还有(什么|别的|其他|需要)|其他(问题|需要|想问)|别的(问题|需要|想问)|"
    r"我(可以|能)(帮|再|继续)|要我(帮|再|继续)|"
    r"anything else|want me to|shall i|should i|need me to",
    re.IGNORECASE,
)

# 待命式收尾：不带问号也算，例如"有需要随时告诉我"
_STANDBY = re.compile(
    r"随时(告诉|叫|找|问|喊|来找)我|有(需要|问题)(再|就)?(告诉|找|叫|问)我|"
    r"需要的话(再|就)?(说|告诉我|找我)|let me know",
    re.IGNORECASE,
)


def _tail(text: str) -> str:
    """取回复尾部片段，短回复整体参与判定。"""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    if len(stripped) <= _TAIL_CHARS:
        return stripped
    segments = [seg.strip() for seg in _TAIL_SPLIT.split(stripped) if seg.strip()]
    if not segments:
        return stripped[-_TAIL_CHARS:]
    tail = segments[-1]
    if len(tail) < _SHORT_TAIL_CHARS and len(segments) >= 2:
        tail = f"{segments[-2]} {tail}"
    return tail[-_TAIL_CHARS:]


def is_followup_offer(text: str) -> bool:
    """判断 bot 回复是否以服务式追问/待命话术收尾。

    命中条件（任一）：

    - 尾部是待命式承诺（"有需要随时告诉我"）；
    - 尾部同时出现疑问标记与"征询 + 服务动作"组合（"还需要我帮你查吗"）。

    单纯的关心或澄清提问（"你今天怎么样？"、"你说的是哪一个？"）不命中，
    因为它们缺少服务提议成分。
    """
    tail = _tail(text)
    if not tail:
        return False
    if _STANDBY.search(tail):
        return True
    if not _ASK_MARK.search(tail):
        return False
    return bool(_NEED_SERVICE.search(tail) or _SERVICE_ASK.search(tail))


@dataclass(frozen=True)
class FollowupConfig:
    """追问抑制配置。"""

    enabled: bool = True
    streak_limit: int = 2
    window_seconds: int = 900


@dataclass(frozen=True)
class FollowupDecision:
    """追问抑制判定结果，只是建议，不阻断任何事件。"""

    level: str = LEVEL_NONE
    streak: int = 0
    last_at: float = 0.0

    @property
    def suppressed(self) -> bool:
        return self.level in (LEVEL_SOFT, LEVEL_HARD)


class FollowupGuard:
    """按作用域统计连续服务式追问，给出软/硬抑制档位。"""

    def __init__(
        self,
        config: FollowupConfig | None = None,
        clock=time.time,
    ) -> None:
        self._config = config or FollowupConfig()
        self._clock = clock
        self._streaks: dict[str, tuple[int, float]] = {}

    def update_config(self, config: FollowupConfig) -> None:
        """热更新配置；已有计数保留，窗口变化靠惰性过期收敛。"""
        self._config = config

    def _now(self) -> float:
        try:
            value = float(self._clock())
        except (TypeError, ValueError, OverflowError):
            return time.time()
        return value

    def _window(self) -> float:
        return max(1.0, float(self._config.window_seconds))

    def _limit(self) -> int:
        return max(1, int(self._config.streak_limit))

    def _active(self, scope_key: str, now: float) -> tuple[int, float]:
        streak, last_at = self._streaks.get(scope_key, (0, 0.0))
        if streak <= 0:
            return 0, 0.0
        if now - last_at > self._window():
            self._streaks.pop(scope_key, None)
            return 0, 0.0
        return streak, last_at

    def _evict(self) -> None:
        if len(self._streaks) <= _MAX_TRACKED_SCOPES:
            return
        ordered = sorted(self._streaks.items(), key=lambda item: item[1][1])
        for key, _ in ordered[: len(self._streaks) - _MAX_TRACKED_SCOPES]:
            self._streaks.pop(key, None)

    def _decide(self, streak: int, last_at: float) -> FollowupDecision:
        if not self._config.enabled or streak <= 0:
            return FollowupDecision(streak=max(0, streak), last_at=last_at)
        level = LEVEL_HARD if streak >= self._limit() else LEVEL_SOFT
        return FollowupDecision(level=level, streak=streak, last_at=last_at)

    def record_reply(self, scope_key: str, text: str) -> FollowupDecision:
        """记录一次 bot 实际回复；非追问收尾会清零连续计数。"""
        if not scope_key:
            return FollowupDecision()
        now = self._now()
        if not is_followup_offer(text):
            self._streaks.pop(scope_key, None)
            return FollowupDecision()
        streak, _ = self._active(scope_key, now)
        streak += 1
        self._streaks[scope_key] = (streak, now)
        self._evict()
        return self._decide(streak, now)

    def peek(self, scope_key: str) -> FollowupDecision:
        """只读查询当前抑制档位，不改变计数。"""
        if not scope_key:
            return FollowupDecision()
        streak, last_at = self._active(scope_key, self._now())
        return self._decide(streak, last_at)

    def reset(self, scope_key: str = "") -> None:
        if scope_key:
            self._streaks.pop(scope_key, None)
            return
        self._streaks.clear()

    def stats(self, scope_key: str) -> dict[str, object]:
        decision = self.peek(scope_key)
        return {
            "level": decision.level,
            "streak": decision.streak,
            "limit": self._limit(),
            "enabled": bool(self._config.enabled),
        }
