from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_relationship.series_control import SeriesControlAdapter


class P:
    """插件替身：原生配置键与 _conf_schema.json 一致（大写），并带 _get。"""

    def __init__(self, tmp_path):
        self._data_dir = tmp_path
        self._raw_config = {
            "MOOD_ENABLED": True,
            "CROSS_PLATFORM_MEMORY_ENABLED": True,
            "CROSS_PLATFORM_MEMORY_TOP_K": 3,
            "CROSS_PLATFORM_MEMORY_MAX_CHARS": 2400,
        }
        self._config_overrides = {}
        self.values = {}
        self.native_writes = []
        self._merged_config = lambda: dict(self._raw_config)

    def _get(self, key, default):
        if key in self._config_overrides:
            return self._config_overrides[key]
        return self._raw_config.get(key, default)

    def _apply_series_control_runtime(self, v):
        self.values.update(v)

    def _apply_native_series_control_values(self, values):
        self.native_writes.append(dict(values))
        return {
            "status": "ok",
            "written": sorted(values),
            "skipped": [],
            "backup_id": "20260916-000000",
        }


class _BarePlugin:
    """不带固化钩子的最小替身：用于 UNSUPPORTED 分支。"""

    def __init__(self, tmp_path):
        self._data_dir = tmp_path
        self._raw_config = {"MOOD_ENABLED": True}


def test_safe_schema_and_apply(tmp_path):
    a = SeriesControlAdapter(P(tmp_path))
    assert set(a.series_control_schema()["fields"]) == {
        "mood_enabled",
        "cross_platform_memory_enabled",
        "cross_platform_memory_top_k",
        "cross_platform_memory_max_chars",
    }
    assert (
        a.apply_series_control_patch(
            {"cross_platform_memory_top_k": 7}, expected_revision=0
        )["status"]
        == "ok"
    )
    assert a.plugin.values["cross_platform_memory_top_k"] == 7


def test_native_mode_ignores_managed_overlay_until_enabled(tmp_path):
    plugin = P(tmp_path)
    adapter = SeriesControlAdapter(plugin)
    adapter.apply_series_control_patch(
        {"cross_platform_memory_top_k": 7}, expected_revision=0
    )
    adapter.set_mode("native")
    assert plugin.values["cross_platform_memory_top_k"] == 3
    adapter.set_mode("managed")
    assert plugin.values["cross_platform_memory_top_k"] == 7


def test_reject_identity_and_bounds(tmp_path):
    a = SeriesControlAdapter(P(tmp_path))
    assert (
        a.validate_series_control_patch({"person_id": "x"}, expected_revision=0)[
            "reason"
        ]
        == "UNKNOWN_FIELD"
    )
    assert (
        a.validate_series_control_patch(
            {"cross_platform_memory_max_chars": 1}, expected_revision=0
        )["reason"]
        == "INVALID_VALUE"
    )


def test_contract_and_snapshot_expose_native_value(tmp_path):
    a = SeriesControlAdapter(P(tmp_path))
    contract = a.series_control_contract()
    assert "read_native" in contract["capabilities"]
    assert "write_native" in contract["capabilities"]

    fields = a.series_control_snapshot()["fields"]
    assert fields["cross_platform_memory_top_k"]["native_value"] == 3
    assert fields["mood_enabled"]["native_value"] is True
    assert fields["mood_enabled"]["native_configured"] is True
    assert all("secret" not in item for item in fields.values())


def test_native_write_delegates_to_plugin_hook(tmp_path):
    plugin = P(tmp_path)
    a = SeriesControlAdapter(plugin)
    result = a.series_control_native_write({"mood_enabled": False})
    assert result["status"] == "ok"
    assert result["reason"] == "APPLIED"
    assert result["written"] == ["mood_enabled"]
    assert result["backup_id"] == "20260916-000000"
    assert plugin.native_writes == [{"mood_enabled": False}]
    # 固化只写插件自身配置，不产生核覆盖层
    assert not (tmp_path / "series-control.json").exists()


def test_native_write_validates_whitelist_and_types(tmp_path):
    plugin = P(tmp_path)
    a = SeriesControlAdapter(plugin)
    assert a.series_control_native_write({"person_id": "u1"})["reason"] == "UNKNOWN_FIELD"
    assert a.series_control_native_write({"mood_enabled": "yes"})["reason"] == "INVALID_TYPE"
    assert (
        a.series_control_native_write({"cross_platform_memory_top_k": 99})["reason"]
        == "INVALID_VALUE"
    )
    assert a.series_control_native_write({"mood_enabled": True}, expected_revision=5)[
        "reason"
    ] == "REVISION_CONFLICT"
    assert plugin.native_writes == []


def test_native_write_requires_plugin_hook(tmp_path):
    a = SeriesControlAdapter(_BarePlugin(tmp_path))
    assert a.series_control_native_write({"mood_enabled": True})["reason"] == "UNSUPPORTED"
