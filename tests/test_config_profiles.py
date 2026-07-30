"""Configuration boundaries for affect, dynamics, and relationship profiles."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from core.config import (
    affect_config,
    dynamics_config,
    relationship_default_profile_id,
    relationship_legacy_profile_id,
    relationship_persona_profile_map,
)


def test_affect_defaults_and_boundaries() -> None:
    defaults = affect_config({})
    assert defaults.enabled is True
    assert defaults.half_life_seconds == 1800.0
    assert defaults.positive_gain == 24.0
    assert defaults.negative_gain == 32.0
    assert defaults.stance_threshold == 15.0

    bounded = affect_config(
        {
            "AFFECT_ENABLED": "off",
            "AFFECT_HALF_LIFE_SECONDS": 1,
            "AFFECT_POSITIVE_GAIN": 101,
            "AFFECT_NEGATIVE_GAIN": -1,
            "AFFECT_STANCE_THRESHOLD": math.nan,
        }
    )
    assert bounded.enabled is False
    assert bounded.half_life_seconds == 10.0
    assert bounded.positive_gain == 100.0
    assert bounded.negative_gain == 0.0
    assert bounded.stance_threshold == 15.0


def test_dynamics_defaults_and_boundaries() -> None:
    defaults = dynamics_config({})
    assert defaults.early_boost == 0.25
    assert defaults.evidence_half_life == 12.0

    bounded = dynamics_config(
        {
            "DYNAMICS_EARLY_BOOST": 2,
            "DYNAMICS_EVIDENCE_HALF_LIFE": 0,
        }
    )
    assert bounded.early_boost == 1.0
    assert bounded.evidence_half_life == 0.1

    invalid = dynamics_config(
        {
            "DYNAMICS_EARLY_BOOST": float("inf"),
            "DYNAMICS_EVIDENCE_HALF_LIFE": "invalid",
        }
    )
    assert invalid.early_boost == 0.25
    assert invalid.evidence_half_life == 12.0


def test_profile_ids_and_mapping_use_shared_normalization() -> None:
    assert relationship_default_profile_id({}) == "default"
    assert relationship_default_profile_id({"RELATIONSHIP_DEFAULT_PROFILE_ID": ""}) == (
        "default"
    )
    assert relationship_legacy_profile_id({"RELATIONSHIP_LEGACY_PROFILE_ID": ""}) == (
        "default"
    )

    mapping = relationship_persona_profile_map(
        {
            "RELATIONSHIP_PERSONA_PROFILE_MAP": (
                "persona-a=shared;persona-b=solo\npersona-c=third,"
                "broken,persona-d=contains spaces"
            )
        }
    )
    assert mapping == {
        "persona-a": "shared",
        "persona-b": "solo",
        "persona-c": "third",
    }


def test_schema_matches_runtime_defaults_and_documents_migration() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["AFFECT_HALF_LIFE_SECONDS"]["minimum"] == 10
    assert schema["AFFECT_HALF_LIFE_SECONDS"]["maximum"] == 604800
    assert schema["DYNAMICS_EVIDENCE_HALF_LIFE"]["minimum"] == pytest.approx(0.1)
    assert schema["RELATIONSHIP_DEFAULT_PROFILE_ID"]["default"] == "default"
    assert schema["RELATIONSHIP_LEGACY_PROFILE_ID"]["default"] == "default"
    assert "首次" in schema["RELATIONSHIP_LEGACY_PROFILE_ID"]["hint"]
    assert "persona_id=profile_id" in schema["RELATIONSHIP_PERSONA_PROFILE_MAP"][
        "hint"
    ]
