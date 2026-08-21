"""Public ``series.control@1.0`` adapter for non-identity settings."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

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
    return plugin._raw_config.get(name, FIELDS[name]["default"])


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
            "validate_patch",
            "apply_patch",
            "reset_override",
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


def snapshot(plugin: Any) -> dict[str, Any]:
    state = _load(plugin)
    overrides = _clean(state.get("overrides"))
    managed = getattr(plugin, "_series_control_mode", "native") == "managed"
    return {
        "status": "ok",
        "revision": _revision(state),
        "fields": {
            name: {
                "native_configured": name in plugin._raw_config,
                "managed_configured": name in overrides,
                "effective_source": "managed"
                if managed and name in overrides
                else "plugin",
                "effective_value": _effective(plugin, name, state),
            }
            for name in FIELDS
        },
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

    def validate_series_control_patch(self, patch, *, expected_revision):
        return validate(self.plugin, patch, expected_revision=expected_revision)

    def apply_series_control_patch(self, patch, *, expected_revision):
        return apply(self.plugin, patch, expected_revision=expected_revision)

    def reset_series_control_override(self, fields=None, *, expected_revision=None):
        return reset(self.plugin, fields, expected_revision=expected_revision)

    def set_mode(self, mode: str):
        self._mode = mode if mode in {"native", "managed"} else "native"
        return set_mode(self.plugin, self._mode)
