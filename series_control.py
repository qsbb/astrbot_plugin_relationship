"""Public ``series.control@1.0`` adapter for non-identity settings."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

CONTRACT_NAME = "series.control@1.0"
PLUGIN_ID = "astrbot_plugin_relationship"
SERIES_ID = "ningxin_suxi"
FIELDS = {
    "mood_enabled": {"type": "bool", "default": True},
    "cross_platform_memory_enabled": {"type": "bool", "default": True},
    "cross_platform_memory_top_k": {
        "type": "int",
        "default": 3,
        "minimum": 1,
        "maximum": 10,
    },
    "cross_platform_memory_max_chars": {
        "type": "int",
        "default": 2400,
        "minimum": 200,
        "maximum": 8000,
    },
}

# series.control 的逻辑字段名 → 本插件原生配置键（_conf_schema.json 的键）。
# 一键固化/一键读取必须落在插件真正读取的那份配置上，不能各写各的。
NATIVE_KEYS = {
    "mood_enabled": "MOOD_ENABLED",
    "cross_platform_memory_enabled": "CROSS_PLATFORM_MEMORY_ENABLED",
    "cross_platform_memory_top_k": "CROSS_PLATFORM_MEMORY_TOP_K",
    "cross_platform_memory_max_chars": "CROSS_PLATFORM_MEMORY_MAX_CHARS",
}


def _path(plugin: Any) -> Path:
    return Path(plugin._data_dir) / "series-control.json"


def _load(plugin: Any) -> dict[str, Any]:
    try:
        value = json.loads(_path(plugin).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _clean(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    clean: dict[str, Any] = {}
    for name, value in values.items():
        spec = FIELDS.get(name)
        if spec is None:
            continue
        if spec["type"] == "bool" and isinstance(value, bool):
            clean[name] = value
        elif (
            spec["type"] == "int"
            and isinstance(value, int)
            and not isinstance(value, bool)
            and spec["minimum"] <= value <= spec["maximum"]
        ):
            clean[name] = value
    return clean


def _revision(state: dict[str, Any]) -> int:
    value = state.get("revision", 0)
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _native(plugin: Any, name: str) -> Any:
    """插件自身配置（页面 overlay → 插件配置页 → 默认值）的当前值。

    这是「核掉线后插件实际会用」的值，一键读取/一键固化都以它为准。
    """
    key = NATIVE_KEYS.get(name, name)
    default = FIELDS[name]["default"]
    getter = getattr(plugin, "_get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            return default
    raw = getattr(plugin, "_raw_config", None)
    if isinstance(raw, Mapping):
        return raw.get(key, default)
    return default


def _write(plugin: Any, state: dict[str, Any]) -> None:
    path = _path(plugin)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix="series-control-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def contract(plugin: Any) -> dict[str, Any]:
    return {
        "name": CONTRACT_NAME,
        "version": "1.0",
        "series_id": SERIES_ID,
        "plugin_id": PLUGIN_ID,
        "plugin_name": "情",
        "capabilities": [
            "read_schema",
            "read_snapshot",
            "read_native",
            "validate_patch",
            "apply_patch",
            "reset_override",
            "write_native",
        ],
        "read_only": False,
        "secrets_in_response": False,
        "max_patch_fields": len(FIELDS),
    }


def schema(plugin: Any) -> dict[str, Any]:
    state = _load(plugin)
    fields = {
        name: {
            **spec,
            "control": "overrideable",
            "secret": False,
            "restart_required": False,
        }
        for name, spec in FIELDS.items()
    }
    return {
        "contract_name": CONTRACT_NAME,
        "contract_version": "1.0",
        "plugin_id": PLUGIN_ID,
        "revision": _revision(state),
        "fields": fields,
    }


def _effective(
    plugin: Any, name: str, state: dict[str, Any], force_overlay: bool = False
) -> Any:
    overrides = _clean(state.get("overrides"))
    managed = (
        force_overlay or getattr(plugin, "_series_control_mode", "native") == "managed"
    )
    return (
        overrides.get(name, _native(plugin, name)) if managed else _native(plugin, name)
    )


def _native_configured(plugin: Any, name: str) -> bool:
    key = NATIVE_KEYS.get(name, name)
    raw = getattr(plugin, "_raw_config", None)
    if isinstance(raw, Mapping) and key in raw:
        return True
    overrides = getattr(plugin, "_config_overrides", None)
    if isinstance(overrides, Mapping) and key in overrides:
        return True
    return False


def snapshot(plugin: Any) -> dict[str, Any]:
    state = _load(plugin)
    overrides = _clean(state.get("overrides"))
    managed = getattr(plugin, "_series_control_mode", "native") == "managed"
    fields: dict[str, Any] = {}
    for name, spec in FIELDS.items():
        item = {
            "native_configured": _native_configured(plugin, name),
            "managed_configured": name in overrides,
            "effective_source": "managed" if managed and name in overrides else "plugin",
            "effective_value": _effective(plugin, name, state),
        }
        # 原生值：供核「一键读取当前配置」使用（secret 字段不回传）
        if spec.get("secret") or spec.get("write_only"):
            item["secret"] = True
        else:
            item["native_value"] = _native(plugin, name)
        fields[name] = item
    return {
        "status": "ok",
        "revision": _revision(state),
        "fields": fields,
    }


def native_write(
    plugin: Any, patch: Any, *, expected_revision: int | None = None
) -> dict[str, Any]:
    """一键固化：把当前值写进插件自身配置（核掉线后仍按此运行）。

    只接受 FIELDS 内的字段；先按白名单 + 类型/范围校验，再交给插件层
    「备份 → 合并 → 原子落盘」。
    """
    state = _load(plugin)
    current = _revision(state)
    revision = current if expected_revision is None else expected_revision
    result = validate(plugin, patch, expected_revision=revision)
    if not result.get("valid"):
        return result
    clean = dict(result.get("patch") or {})
    hook = getattr(plugin, "_apply_native_series_control_values", None)
    if not callable(hook):
        return {"status": "error", "reason": "UNSUPPORTED", "revision": current}
    outcome = hook(clean)
    if not isinstance(outcome, dict) or outcome.get("status") != "ok":
        reason = str((outcome or {}).get("reason") or "PERSIST_FAILED")
        return {"status": "error", "reason": reason, "revision": current}
    return {
        "status": "ok",
        "reason": "APPLIED",
        "revision": current,
        "written": list(outcome.get("written") or clean.keys()),
        "skipped": list(outcome.get("skipped") or []),
        "backup_id": str(outcome.get("backup_id") or ""),
    }


def validate(plugin: Any, patch: Any, *, expected_revision: int) -> dict[str, Any]:
    state = _load(plugin)
    current = _revision(state)
    if current != expected_revision:
        return {
            "status": "error",
            "valid": False,
            "reason": "REVISION_CONFLICT",
            "revision": current,
        }
    if not isinstance(patch, dict) or not patch or len(patch) > len(FIELDS):
        return {
            "status": "error",
            "valid": False,
            "reason": "INVALID_PATCH",
            "revision": current,
        }
    for name, value in patch.items():
        if name not in FIELDS:
            return {
                "status": "error",
                "valid": False,
                "reason": "UNKNOWN_FIELD",
                "field": str(name),
            }
        if name not in _clean({name: value}):
            reason = (
                "INVALID_TYPE" if FIELDS[name]["type"] == "bool" else "INVALID_VALUE"
            )
            return {"status": "error", "valid": False, "reason": reason, "field": name}
    return {
        "status": "ok",
        "valid": True,
        "reason": "VALID",
        "revision": current,
        "patch": dict(patch),
    }


def _apply_runtime(
    plugin: Any, state: dict[str, Any], force_overlay: bool = False
) -> None:
    hook = getattr(plugin, "_apply_series_control_runtime", None)
    if callable(hook):
        hook({name: _effective(plugin, name, state, force_overlay) for name in FIELDS})


def apply(
    plugin: Any, patch: dict[str, Any], *, expected_revision: int
) -> dict[str, Any]:
    result = validate(plugin, patch, expected_revision=expected_revision)
    if not result.get("valid"):
        return result
    state = _load(plugin)
    before = dict(state)
    overrides = _clean(state.get("overrides"))
    overrides.update(result["patch"])
    next_state = {
        "schema_version": 1,
        "revision": expected_revision + 1,
        "overrides": overrides,
    }
    try:
        _write(plugin, next_state)
        plugin._series_control_mode = "managed"
        _apply_runtime(plugin, next_state, force_overlay=True)
    except Exception:
        try:
            _write(plugin, before)
        except Exception:
            pass
        return {
            "status": "error",
            "valid": False,
            "reason": "APPLY_FAILED_ROLLED_BACK",
            "revision": expected_revision,
        }
    return {
        "status": "ok",
        "success": True,
        "reason": "APPLIED",
        "revision": next_state["revision"],
        "fields": snapshot(plugin)["fields"],
    }


def reset(
    plugin: Any,
    fields: list[str] | None = None,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    state = _load(plugin)
    current = _revision(state)
    if expected_revision is not None and current != expected_revision:
        return {
            "status": "error",
            "success": False,
            "reason": "REVISION_CONFLICT",
            "revision": current,
        }
    overrides = _clean(state.get("overrides"))
    names = list(overrides) if fields is None else fields
    if any(name not in FIELDS for name in names):
        return {
            "status": "error",
            "success": False,
            "reason": "UNKNOWN_FIELD",
            "revision": current,
        }
    before = dict(state)
    for name in names:
        overrides.pop(name, None)
    next_state = {"schema_version": 1, "revision": current + 1, "overrides": overrides}
    try:
        _write(plugin, next_state)
        plugin._series_control_mode = "managed" if overrides else "native"
        _apply_runtime(plugin, next_state)
    except Exception:
        try:
            _write(plugin, before)
        except Exception:
            pass
        return {
            "status": "error",
            "success": False,
            "reason": "APPLY_FAILED_ROLLED_BACK",
            "revision": current,
        }
    return {
        "status": "ok",
        "success": True,
        "reason": "RESET",
        "revision": next_state["revision"],
        "fields": snapshot(plugin)["fields"],
    }


def set_mode(plugin: Any, mode: str) -> dict[str, Any]:
    plugin._series_control_mode = mode if mode in {"native", "managed"} else "native"
    _apply_runtime(plugin, _load(plugin))
    return {"success": True, "mode": plugin._series_control_mode}


class SeriesControlAdapter:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        state = _load(plugin)
        self._mode = "managed" if _clean(state.get("overrides")) else "native"

    def sync_runtime(self) -> None:
        set_mode(self.plugin, self._mode)

    def series_control_contract(self):
        return contract(self.plugin)

    def series_control_schema(self):
        return schema(self.plugin)

    def series_control_snapshot(self):
        return snapshot(self.plugin)

    def series_control_native_write(self, patch, *, expected_revision=None):
        return native_write(
            self.plugin, patch, expected_revision=expected_revision
        )

    def validate_series_control_patch(self, patch, *, expected_revision):
        return validate(self.plugin, patch, expected_revision=expected_revision)

    def apply_series_control_patch(self, patch, *, expected_revision):
        return apply(self.plugin, patch, expected_revision=expected_revision)

    def reset_series_control_override(self, fields=None, *, expected_revision=None):
        return reset(self.plugin, fields, expected_revision=expected_revision)

    def set_mode(self, mode: str):
        self._mode = mode if mode in {"native", "managed"} else "native"
        return set_mode(self.plugin, self._mode)
