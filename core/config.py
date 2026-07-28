"""配置解析：raw dict → 各模块强类型配置。

所有阈值、权重、上限来自 _conf_schema.json；DEFAULTS 在代码内兜底，
schema 缺字段或用户误删配置时插件仍可运行。
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .affinity import AffinityConfig
from .decay import DecayConfig
from .familiarity import FamiliarityConfig
from .followup import FollowupConfig
from .policy import PolicyConfig
from .prompts import PromptConfig
from .trust import TrustConfig

DEFAULTS: dict[str, Any] = {
    "MOOD_ENABLED": True,
    "MOOD_WINDOW_SECONDS": 300,
    "MOOD_FREQUENT_AFTER": 6,
    "MOOD_STREAK_AFTER": 8,
    "MOOD_STREAK_GAP_SECONDS": 90,
    "MOOD_LAZY_SCORE": 72,
    "MOOD_ANNOYED_SCORE": 45,
    "MOOD_SILENCE_SCORE": 25,
    "MOOD_SILENCE_CHANCE_PERCENT": 45,
    "MOOD_MAX_CONSECUTIVE_SILENCES": 2,
    "AFFINITY_MESSAGE_GAIN": 0.2,
    "AFFINITY_PRAISE_GAIN": 1.5,
    "AFFINITY_HELP_RECEIVED_GAIN": 2.0,
    "AFFINITY_OFFENSE_PENALTY": -2.0,
    "AFFINITY_DAILY_CAP": 5.0,
    "AFFINITY_DAILY_NEGATIVE_CAP": 5.0,
    "AFFINITY_MESSAGE_COOLDOWN_SECONDS": 60.0,
    "AFFINITY_WHITELIST_USER_IDS": "",
    "AFFINITY_HIGH_THRESHOLD": 75.0,
    "AFFINITY_NON_WHITELIST_CEILING": 68.0,
    "AFFINITY_WHITELIST_TRUST_GATE": 65.0,
    "AFFINITY_WHITELIST_FAMILIARITY_GATE": 25.0,
    "TRUST_PROMISE_KEPT_GAIN": 3.0,
    "TRUST_PROMISE_BROKEN_PENALTY": -5.0,
    "TRUST_OFFENSE_PENALTY": -1.0,
    "FAMILIARITY_BASE_GAIN": 0.8,
    "FAMILIARITY_DIMINISH_CURVE": 2.0,
    "FAMILIARITY_COOLDOWN_SECONDS": 120.0,
    "DECAY_AFFINITY_REGRESSION_PER_DAY": 0.02,
    "DECAY_TRUST_REGRESSION_PER_DAY": 0.005,
    "POLICY_ENABLE_PROMPT_FRAGMENT": True,
    "POLICY_ENABLE_STYLE_HINT": True,
    "PROMPT_INJECT_ENABLED": True,
    "FOLLOWUP_GUARD_ENABLED": True,
    "FOLLOWUP_STREAK_LIMIT": 2,
    "FOLLOWUP_WINDOW_SECONDS": 900,
    "SAVE_INTERVAL_SECONDS": 30.0,
    "LOG_LEVEL": "INFO",
}


def _get(raw: Mapping[str, Any], key: str) -> Any:
    value = raw.get(key)
    return DEFAULTS[key] if value is None else value


def _get_float(
    raw: Mapping[str, Any],
    key: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        value = float(_get(raw, key))
    except (TypeError, ValueError):
        value = float(DEFAULTS[key])
    if not math.isfinite(value):
        value = float(DEFAULTS[key])
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _get_int(
    raw: Mapping[str, Any],
    key: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        value = int(_get(raw, key))
    except (TypeError, ValueError, OverflowError):
        value = int(DEFAULTS[key])
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _get_bool(raw: Mapping[str, Any], key: str) -> bool:
    value = _get(raw, key)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on", "是", "开启"}:
            return True
        if normalized in {"false", "0", "no", "off", "否", "关闭", ""}:
            return False
        return bool(DEFAULTS[key])
    return bool(value)


def mood_kwargs(raw: Mapping[str, Any]) -> dict[str, int]:
    """MoodTracker 构造/update_config 参数。"""
    return {
        "window_seconds": _get_int(
            raw, "MOOD_WINDOW_SECONDS", minimum=10, maximum=86400
        ),
        "frequent_after": _get_int(
            raw, "MOOD_FREQUENT_AFTER", minimum=1, maximum=100000
        ),
        "streak_after": _get_int(raw, "MOOD_STREAK_AFTER", minimum=1, maximum=100000),
        "streak_gap_seconds": _get_int(
            raw, "MOOD_STREAK_GAP_SECONDS", minimum=1, maximum=86400
        ),
        "lazy_score": _get_int(raw, "MOOD_LAZY_SCORE", minimum=0, maximum=100),
        "annoyed_score": _get_int(raw, "MOOD_ANNOYED_SCORE", minimum=0, maximum=100),
        "silence_score": _get_int(raw, "MOOD_SILENCE_SCORE", minimum=0, maximum=100),
        "silence_chance_percent": _get_int(
            raw, "MOOD_SILENCE_CHANCE_PERCENT", minimum=0, maximum=100
        ),
        "max_consecutive_silences": _get_int(
            raw, "MOOD_MAX_CONSECUTIVE_SILENCES", minimum=0, maximum=100000
        ),
    }


def _get_ids(raw: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = _get(raw, key)
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def affinity_config(raw: Mapping[str, Any]) -> AffinityConfig:
    return AffinityConfig(
        message_gain=_get_float(
            raw, "AFFINITY_MESSAGE_GAIN", minimum=0.0, maximum=100.0
        ),
        praise_gain=_get_float(raw, "AFFINITY_PRAISE_GAIN", minimum=0.0, maximum=100.0),
        help_received_gain=_get_float(
            raw, "AFFINITY_HELP_RECEIVED_GAIN", minimum=0.0, maximum=100.0
        ),
        offense_penalty=_get_float(
            raw, "AFFINITY_OFFENSE_PENALTY", minimum=-100.0, maximum=0.0
        ),
        daily_cap=_get_float(raw, "AFFINITY_DAILY_CAP", minimum=0.0, maximum=100.0),
        daily_negative_cap=_get_float(
            raw, "AFFINITY_DAILY_NEGATIVE_CAP", minimum=0.0, maximum=100.0
        ),
        message_cooldown_seconds=_get_float(
            raw, "AFFINITY_MESSAGE_COOLDOWN_SECONDS", minimum=0.0, maximum=86400.0
        ),
        whitelist_user_ids=_get_ids(raw, "AFFINITY_WHITELIST_USER_IDS"),
        high_affinity_threshold=_get_float(
            raw, "AFFINITY_HIGH_THRESHOLD", minimum=0.0, maximum=100.0
        ),
        non_whitelist_ceiling=_get_float(
            raw, "AFFINITY_NON_WHITELIST_CEILING", minimum=0.0, maximum=100.0
        ),
        whitelist_trust_gate=_get_float(
            raw, "AFFINITY_WHITELIST_TRUST_GATE", minimum=0.0, maximum=100.0
        ),
        whitelist_familiarity_gate=_get_float(
            raw, "AFFINITY_WHITELIST_FAMILIARITY_GATE", minimum=0.0, maximum=100.0
        ),
    )


def trust_config(raw: Mapping[str, Any]) -> TrustConfig:
    return TrustConfig(
        promise_kept_gain=_get_float(
            raw, "TRUST_PROMISE_KEPT_GAIN", minimum=0.0, maximum=100.0
        ),
        promise_broken_penalty=_get_float(
            raw, "TRUST_PROMISE_BROKEN_PENALTY", minimum=-100.0, maximum=0.0
        ),
        offense_penalty=_get_float(
            raw, "TRUST_OFFENSE_PENALTY", minimum=-100.0, maximum=0.0
        ),
    )


def familiarity_config(raw: Mapping[str, Any]) -> FamiliarityConfig:
    return FamiliarityConfig(
        base_gain=_get_float(raw, "FAMILIARITY_BASE_GAIN", minimum=0.0, maximum=100.0),
        diminish_curve=_get_float(
            raw, "FAMILIARITY_DIMINISH_CURVE", minimum=0.01, maximum=10.0
        ),
        cooldown_seconds=_get_float(
            raw, "FAMILIARITY_COOLDOWN_SECONDS", minimum=0.0, maximum=86400.0
        ),
    )


def decay_config(raw: Mapping[str, Any]) -> DecayConfig:
    return DecayConfig(
        affinity_regression_per_day=_get_float(
            raw,
            "DECAY_AFFINITY_REGRESSION_PER_DAY",
            minimum=0.0,
            maximum=1.0,
        ),
        trust_regression_per_day=_get_float(
            raw, "DECAY_TRUST_REGRESSION_PER_DAY", minimum=0.0, maximum=1.0
        ),
    )


def policy_config(raw: Mapping[str, Any]) -> PolicyConfig:
    return PolicyConfig(
        enable_prompt_fragment=_get_bool(raw, "POLICY_ENABLE_PROMPT_FRAGMENT"),
        enable_style_hint=_get_bool(raw, "POLICY_ENABLE_STYLE_HINT"),
        enable_followup_guard=_get_bool(raw, "FOLLOWUP_GUARD_ENABLED"),
    )


def followup_config(raw: Mapping[str, Any]) -> FollowupConfig:
    return FollowupConfig(
        enabled=_get_bool(raw, "FOLLOWUP_GUARD_ENABLED"),
        streak_limit=_get_int(raw, "FOLLOWUP_STREAK_LIMIT", minimum=1, maximum=100),
        window_seconds=_get_int(
            raw, "FOLLOWUP_WINDOW_SECONDS", minimum=60, maximum=86400
        ),
    )


def prompt_config(raw: Mapping[str, Any]) -> PromptConfig:
    return PromptConfig(
        inject_enabled=_get_bool(raw, "PROMPT_INJECT_ENABLED"),
        followup_guard_enabled=_get_bool(raw, "FOLLOWUP_GUARD_ENABLED"),
    )


def mood_enabled(raw: Mapping[str, Any]) -> bool:
    return _get_bool(raw, "MOOD_ENABLED")


def save_interval_seconds(raw: Mapping[str, Any]) -> float:
    return _get_float(raw, "SAVE_INTERVAL_SECONDS")


def log_level(raw: Mapping[str, Any]) -> str:
    return str(_get(raw, "LOG_LEVEL")).upper()
