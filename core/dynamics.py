"""Evidence strength and smooth relationship plasticity helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .models import TRUSTED_SEMANTIC_SOURCES, InteractionEvent

EVIDENCE_MASS_KEY = "trusted_semantic_evidence_mass"


@dataclass(frozen=True)
class DynamicsConfig:
    """Controls the small early-relationship plasticity boost."""

    early_boost: float = 0.25
    evidence_half_life: float = 12.0


def _finite_float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return number if math.isfinite(number) else fallback


def _unit_interval(value: Any) -> float:
    return max(0.0, min(1.0, _finite_float(value)))


def _nonnegative(value: Any, fallback: float = 0.0) -> float:
    return max(0.0, _finite_float(value, fallback))


def is_trusted_semantic_event(event: InteractionEvent) -> bool:
    """Return whether an event may affect evidence-backed relationship dynamics."""

    return bool(event.is_semantic) and event.source in TRUSTED_SEMANTIC_SOURCES


def event_strength(event: InteractionEvent) -> float:
    """Normalize event confidence and severity into a safe ``0..1`` strength."""

    return _unit_interval(event.confidence) * _unit_interval(event.severity)


def event_weight(
    event: InteractionEvent,
    evidence_mass: float = 0.0,
    config: DynamicsConfig | None = None,
) -> float:
    """Return the effective weight for one event.

    Ordinary interactions retain their historical weight of ``1``. Untrusted
    semantic assertions receive no weight. Trusted semantic events combine their
    normalized evidence strength with a smooth early-relationship boost that
    approaches ``1`` as evidence accumulates.
    """

    if not event.is_semantic:
        return 1.0
    if not is_trusted_semantic_event(event):
        return 0.0

    cfg = config or DynamicsConfig()
    strength = event_strength(event)
    if strength <= 0.0:
        return 0.0

    mass = _nonnegative(evidence_mass)
    early_boost = _nonnegative(cfg.early_boost, DynamicsConfig.early_boost)
    half_life = _finite_float(
        cfg.evidence_half_life, DynamicsConfig.evidence_half_life
    )
    if half_life <= 0.0:
        half_life = DynamicsConfig.evidence_half_life
    plasticity = 1.0 + early_boost * (2.0 ** (-mass / half_life))
    return strength * plasticity


def accumulate_evidence_mass(
    evidence_mass: float, event: InteractionEvent
) -> float:
    """Purely return evidence mass after accepting ``event``.

    Callers should invoke this only after normal event validation and deduplication.
    The function still rejects untrusted or zero-strength semantic events so it is
    safe to reuse at integration boundaries.
    """

    current = _nonnegative(evidence_mass)
    if not is_trusted_semantic_event(event):
        return current
    return current + event_strength(event)
