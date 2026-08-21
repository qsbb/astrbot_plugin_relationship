"""Public series.control@1.0 adapter for non-identity relationship settings."""

from __future__ import annotations
import json
import os
import tempfile

FIELDS = {
    "mood_enabled": {"type": "bool", "default": True},
    "cross_platform_memory_enabled": {"type": "bool", "default": True},
    "cross_platform_memory_top_k": {
        "type": "int",
        "default": 3,
        "minimum": 1,
        "maximum": 20,
    },
    "cross_platform_memory_max_chars": {
        "type": "int",
        "default": 1200,
        "minimum": 100,
        "maximum": 10000,
    },
}


def _path(plugin):
    return plugin._data_dir / "series-control.json"


def _load(plugin):
    try:
        data = json.loads(_path(plugin).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _native(plugin, name):
    return plugin._raw_config.get(name, FIELDS[name]["default"])


def contract(plugin):
    return {
        "name": "series.control@1.0",
        "version": "1.0",
        "series_id": "ningxin_suxi",
        "plugin_id": "astrbot_plugin_relationship",
        "plugin_name": "凝心溯溪-情",
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


def schema(plugin):
    return {
        "contract_name": "series.control@1.0",
        "contract_version": "1.0",
        "plugin_id": "astrbot_plugin_relationship",
        "revision": int(_load(plugin).get("revision", 0) or 0),
        "fields": {
            k: {
                **v,
                "control": "overrideable",
                "secret": False,
                "restart_required": False,
            }
            for k, v in FIELDS.items()
        },
    }


def snapshot(plugin):
    state = _load(plugin)
    overrides = (
        state.get("overrides", {})
        if isinstance(state.get("overrides", {}), dict)
        else {}
    )
    managed = getattr(plugin, "_series_control_mode", "native") == "managed"
    return {
        "status": "ok",
        "revision": int(state.get("revision", 0) or 0),
        "fields": {
            k: {
                "native_configured": k in plugin._raw_config,
                "managed_configured": k in overrides,
                "effective_source": "managed"
                if managed and k in overrides
                else "plugin",
            }
            for k in FIELDS
        },
    }


def validate(plugin, patch, *, expected_revision):
    state = _load(plugin)
    current = int(state.get("revision", 0) or 0)
    if current != int(expected_revision):
        return {"status": "error", "valid": False, "reason": "REVISION_CONFLICT"}
    if not isinstance(patch, dict) or not patch:
        return {"status": "error", "valid": False, "reason": "PATCH_INVALID"}
    for key in patch:
        if key not in FIELDS:
            return {"status": "error", "valid": False, "reason": "UNKNOWN_FIELD"}
    for k, v in patch.items():
        s = FIELDS[k]
        if s["type"] == "bool" and not isinstance(v, bool):
            return {"status": "error", "valid": False, "reason": "INVALID_TYPE"}
        if s["type"] == "int" and (
            not isinstance(v, int)
            or isinstance(v, bool)
            or not s["minimum"] <= v <= s["maximum"]
        ):
            return {"status": "error", "valid": False, "reason": "INVALID_VALUE"}
    return {"status": "ok", "valid": True, "revision": current}


def _write(plugin, state):
    path = _path(plugin)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".series-control.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            json.dump(state, h, ensure_ascii=False, sort_keys=True)
            h.flush()
            os.fsync(h.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply(plugin, patch, *, expected_revision):
    result = validate(plugin, patch, expected_revision=expected_revision)
    if not result.get("valid"):
        return result
    state = _load(plugin)
    overrides = dict(state.get("overrides", {}) or {})
    overrides.update(patch)
    next_state = {
        "schema_version": 1,
        "revision": int(expected_revision) + 1,
        "overrides": overrides,
    }
    _write(plugin, next_state)
    if getattr(plugin, "_series_control_mode", "native") == "managed":
        plugin._apply_series_control_runtime(
            {**{k: _native(plugin, k) for k in FIELDS}, **overrides}
        )
    return {"status": "ok", "success": True, "revision": next_state["revision"]}


def reset(plugin, fields=None, *, expected_revision=None):
    state = _load(plugin)
    current = int(state.get("revision", 0) or 0)
    if expected_revision is not None and current != int(expected_revision):
        return {"success": False, "reason": "REVISION_CONFLICT"}
    overrides = dict(state.get("overrides", {}) or {})
    for field in fields or list(overrides):
        overrides.pop(field, None)
    _write(
        plugin, {"schema_version": 1, "revision": current + 1, "overrides": overrides}
    )
    return {"success": True, "revision": current + 1}


def set_mode(plugin, mode):
    plugin._series_control_mode = mode if mode in {"native", "managed"} else "native"
    if plugin._series_control_mode == "managed":
        plugin._apply_series_control_runtime(
            {
                **{k: _native(plugin, k) for k in FIELDS},
                **(_load(plugin).get("overrides", {}) or {}),
            }
        )
    else:
        plugin._apply_series_control_runtime({k: _native(plugin, k) for k in FIELDS})
    return {"success": True, "mode": plugin._series_control_mode}


class SeriesControlAdapter:
    def __init__(self, plugin):
        self.plugin = plugin
        self._mode = "managed" if _load(plugin).get("overrides") else "native"

    def sync_runtime(self):
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
