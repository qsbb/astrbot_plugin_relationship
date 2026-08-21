from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_relationship.series_control import SeriesControlAdapter


class P:
    def __init__(self, tmp_path):
        self._data_dir = tmp_path
        self._raw_config = {
            "mood_enabled": True,
            "cross_platform_memory_enabled": True,
            "cross_platform_memory_top_k": 3,
            "cross_platform_memory_max_chars": 2400,
        }
        self.values = {}
        self._merged_config = lambda: dict(self._raw_config)

    def _apply_series_control_runtime(self, v):
        self.values.update(v)


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
