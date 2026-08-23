"""Relationship-profile identifiers and scoped storage keys."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import quote, unquote

DEFAULT_PROFILE_ID = "default"
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def validate_profile_id(value: object) -> str:
    profile_id = str(value or "").strip()
    if not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError("INVALID_RELATIONSHIP_PROFILE_ID")
    return profile_id


def normalize_profile_id(value: object, fallback: str = DEFAULT_PROFILE_ID) -> str:
    """Return a safe stable profile id for configuration or runtime persona ids."""
    raw = str(value or "").strip()
    if PROFILE_ID_PATTERN.fullmatch(raw):
        return raw
    if raw:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        return f"auto-{digest}"
    try:
        return validate_profile_id(fallback)
    except ValueError:
        return DEFAULT_PROFILE_ID


def parse_profile_mapping(value: object) -> dict[str, str]:
    """Parse ``persona_id=profile_id`` entries separated by comma/newline/semicolon."""
    mapping: dict[str, str] = {}
    for item in re.split(r"[,;\n]+", str(value or "")):
        if "=" not in item:
            continue
        persona_id, profile_id = (part.strip() for part in item.split("=", 1))
        if not persona_id:
            continue
        try:
            mapping[persona_id] = validate_profile_id(profile_id)
        except ValueError:
            continue
    return mapping


def resolve_profile_id(
    persona_id: object,
    *,
    default_profile_id: str = DEFAULT_PROFILE_ID,
    mapping: dict[str, str] | None = None,
) -> str:
    raw = str(persona_id or "").strip()
    if raw and mapping and raw in mapping:
        return mapping[raw]
    if raw:
        return normalize_profile_id(raw)
    return normalize_profile_id(default_profile_id)


def _component(value: object) -> str:
    return quote(str(value or ""), safe="-_.~")


def _uncomponent(value: str) -> str:
    return unquote(value)


def person_state_key(profile_id: str, person_id: str) -> str:
    return f"persona:{normalize_profile_id(profile_id)}:person:{_component(person_id)}"


def account_state_key(profile_id: str, bot_id: str, user_id: str) -> str:
    return (
        f"persona:{normalize_profile_id(profile_id)}:account:"
        f"{_component(bot_id)}:user:{_component(user_id)}"
    )


def group_state_key(profile_id: str, bot_id: str, group_id: str) -> str:
    """Return the long-lived relationship state key for one QQ group.

    Group state is deliberately separate from account/person state.  A group
    can contain many users and must never be merged by the identity registry.
    """
    return (
        f"persona:{normalize_profile_id(profile_id)}:group:"
        f"{_component(bot_id)}:id:{_component(group_id)}"
    )


def session_state_key(
    profile_id: str,
    bot_id: str,
    user_id: str,
    group_id: str | None,
) -> str:
    base = f"persona:{normalize_profile_id(profile_id)}:session:{_component(bot_id)}"
    if group_id:
        return f"{base}:group:{_component(group_id)}"
    return f"{base}:private:{_component(user_id)}"


def pressure_state_key(session_key: str, user_id: str) -> str:
    return f"{session_key}:user:{_component(user_id)}"


def parse_state_key(key: str) -> dict[str, str] | None:
    """Parse a v4 person/account key for diagnostics and the manager page."""
    person = re.fullmatch(r"persona:([^:]+):person:(.+)", key)
    if person:
        return {
            "kind": "person",
            "profile_id": person.group(1),
            "person_id": _uncomponent(person.group(2)),
        }
    account = re.fullmatch(r"persona:([^:]+):account:([^:]*):user:(.+)", key)
    if account:
        return {
            "kind": "account",
            "profile_id": account.group(1),
            "bot_id": _uncomponent(account.group(2)),
            "user_id": _uncomponent(account.group(3)),
        }
    group = re.fullmatch(r"persona:([^:]+):group:([^:]*):id:(.+)", key)
    if group:
        return {
            "kind": "group",
            "profile_id": group.group(1),
            "bot_id": _uncomponent(group.group(2)),
            "group_id": _uncomponent(group.group(3)),
        }
    return None


def migrate_legacy_state_key(key: str, legacy_profile_id: str) -> str:
    """Map one pre-v4 state key into exactly one administrator-owned profile."""
    if parse_state_key(key) is not None:
        return key
    if key.startswith("person:user:"):
        return person_state_key(legacy_profile_id, key[len("person:user:") :])
    if ":user:" in key:
        bot_id, user_id = key.split(":user:", 1)
        return account_state_key(legacy_profile_id, bot_id, user_id)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return account_state_key(legacy_profile_id, "legacy", digest)
