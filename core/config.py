"""配置解析：raw dict → 各模块强类型配置。

所有阈值、权重、上限来自 _conf_schema.json；DEFAULTS 在代码内兜底，
schema 缺字段或用户误删配置时插件仍可运行。
"""

from __future__ import annotations

from typing import Any, Mapping

from .affinity import AffinityConfig
from .decay import DecayConfig
from .familiarity import FamiliarityConfig
from .policy import PolicyConfig
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
    "SAVE_INTERVAL_SECONDS": 30.0,
    "LOG_LEVEL": "INFO",
}


def _get(raw: Mapping[str, Any], key: str) -> Any:
    value = raw.get(key)
    return DEFAULTS[key] if value is None else value


def _get_float(raw: Mapping[str, Any], key: str) -> float:
    try:
        return float(_get(raw, key))
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


def _get_int(raw: Mapping[str, Any], key: str) -> int:
    try:
        return int(_get(raw, key))
    except (TypeError, ValueError):
        return int(DEFAULTS[key])


def _get_bool(raw: Mapping[str, Any], key: str) -> bool:
    return bool(_get(raw, key))


def mood_kwargs(raw: Mapping[str, Any]) -> dict[str, int]:
    """MoodTracker 构造/update_config 参数。"""
    return {
        "window_seconds": _get_int(raw, "MOOD_WINDOW_SECONDS"),
        "frequent_after": _get_int(raw, "MOOD_FREQUENT_AFTER"),
        "streak_after": _get_int(raw, "MOOD_STREAK_AFTER"),
        "streak_gap_seconds": _get_int(raw, "MOOD_STREAK_GAP_SECONDS"),
        "lazy_score": _get_int(raw, "MOOD_LAZY_SCORE"),
        "annoyed_score": _get_int(raw, "MOOD_ANNOYED_SCORE"),
        "silence_score": _get_int(raw, "MOOD_SILENCE_SCORE"),
        "silence_chance_percent": _get_int(raw, "MOOD_SILENCE_CHANCE_PERCENT"),
        "max_consecutive_silences": _get_int(raw, "MOOD_MAX_CONSECUTIVE_SILENCES"),
    }


def affinity_config(raw: Mapping[str, Any]) -> AffinityConfig:
    return AffinityConfig(
        message_gain=_get_float(raw, "AFFINITY_MESSAGE_GAIN"),
        praise_gain=_get_float(raw, "AFFINITY_PRAISE_GAIN"),
        help_received_gain=_get_float(raw, "AFFINITY_HELP_RECEIVED_GAIN"),
        offense_penalty=_get_float(raw, "AFFINITY_OFFENSE_PENALTY"),
        daily_cap=_get_float(raw, "AFFINITY_DAILY_CAP"),
        daily_negative_cap=_get_float(raw, "AFFINITY_DAILY_NEGATIVE_CAP"),
        message_cooldown_seconds=_get_float(raw, "AFFINITY_MESSAGE_COOLDOWN_SECONDS"),
    )


def trust_config(raw: Mapping[str, Any]) -> TrustConfig:
    return TrustConfig(
        promise_kept_gain=_get_float(raw, "TRUST_PROMISE_KEPT_GAIN"),
        promise_broken_penalty=_get_float(raw, "TRUST_PROMISE_BROKEN_PENALTY"),
        offense_penalty=_get_float(raw, "TRUST_OFFENSE_PENALTY"),
    )


def familiarity_config(raw: Mapping[str, Any]) -> FamiliarityConfig:
    return FamiliarityConfig(
        base_gain=_get_float(raw, "FAMILIARITY_BASE_GAIN"),
        diminish_curve=_get_float(raw, "FAMILIARITY_DIMINISH_CURVE"),
        cooldown_seconds=_get_float(raw, "FAMILIARITY_COOLDOWN_SECONDS"),
    )


def decay_config(raw: Mapping[str, Any]) -> DecayConfig:
    return DecayConfig(
        affinity_regression_per_day=_get_float(
            raw, "DECAY_AFFINITY_REGRESSION_PER_DAY"
        ),
        trust_regression_per_day=_get_float(raw, "DECAY_TRUST_REGRESSION_PER_DAY"),
    )


def policy_config(raw: Mapping[str, Any]) -> PolicyConfig:
    return PolicyConfig(
        enable_prompt_fragment=_get_bool(raw, "POLICY_ENABLE_PROMPT_FRAGMENT"),
        enable_style_hint=_get_bool(raw, "POLICY_ENABLE_STYLE_HINT"),
    )


def mood_enabled(raw: Mapping[str, Any]) -> bool:
    return _get_bool(raw, "MOOD_ENABLED")


def save_interval_seconds(raw: Mapping[str, Any]) -> float:
    return _get_float(raw, "SAVE_INTERVAL_SECONDS")


def log_level(raw: Mapping[str, Any]) -> str:
    return str(_get(raw, "LOG_LEVEL")).upper()
