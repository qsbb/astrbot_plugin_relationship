"""Privacy-minimized read-only identity candidate projection.

This module deliberately accepts and returns plain data only. Raw platform
accounts stay inside ``IdentityRegistry`` and never cross this boundary.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .identity_registry import PersonIdentity

MAX_IDENTITY_CANDIDATES = 1000
MAX_IDENTITY_ACCOUNTS = 20
MAX_DISPLAY_NAME_LENGTH = 80
IDENTITY_CANDIDATE_FIELDS = frozenset({"person_id", "display_name", "account_count"})

_PERSON_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class IdentityCandidateValidationError(ValueError):
    """Raised when a candidate batch cannot be exposed without ambiguity."""


def identity_candidate_rows(
    persons: Iterable[PersonIdentity],
) -> list[dict[str, object]]:
    """Project trusted registry values into the three-field public shape."""
    rows: list[dict[str, object]] = []
    for person in persons:
        if not isinstance(person, PersonIdentity):
            raise IdentityCandidateValidationError("INVALID_PERSON_RECORD")
        rows.append(
            {
                "person_id": person.person_id,
                "display_name": person.display_name,
                "account_count": len(person.accounts),
            }
        )
    return rows


def validate_identity_candidates(raw_candidates: Any) -> list[dict[str, object]]:
    """Validate, normalize, sort, and cap one complete candidate batch.

    Validation is all-or-nothing. A caller must discard the whole batch when
    this function raises so a malformed or over-broad row can never be exposed.
    """
    if not isinstance(raw_candidates, list):
        raise IdentityCandidateValidationError("INVALID_CANDIDATE_BATCH")

    candidates: list[dict[str, object]] = []
    seen_person_ids: set[str] = set()
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            raise IdentityCandidateValidationError("INVALID_CANDIDATE")
        if set(raw) != IDENTITY_CANDIDATE_FIELDS:
            raise IdentityCandidateValidationError("INVALID_CANDIDATE_FIELDS")

        person_id = raw.get("person_id")
        if not isinstance(person_id, str) or not _PERSON_ID_RE.fullmatch(person_id):
            raise IdentityCandidateValidationError("INVALID_PERSON_ID")
        if person_id in seen_person_ids:
            raise IdentityCandidateValidationError("DUPLICATE_PERSON_ID")

        display_name = raw.get("display_name")
        if not isinstance(display_name, str):
            raise IdentityCandidateValidationError("INVALID_DISPLAY_NAME")
        display_name = display_name.strip()
        if not display_name or len(display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise IdentityCandidateValidationError("INVALID_DISPLAY_NAME")

        account_count = raw.get("account_count")
        if (
            type(account_count) is not int
            or account_count < 0
            or account_count > MAX_IDENTITY_ACCOUNTS
        ):
            raise IdentityCandidateValidationError("INVALID_ACCOUNT_COUNT")

        seen_person_ids.add(person_id)
        candidates.append(
            {
                "person_id": person_id,
                "display_name": display_name,
                "account_count": account_count,
            }
        )

    candidates.sort(key=lambda item: str(item["person_id"]))
    return candidates[:MAX_IDENTITY_CANDIDATES]
