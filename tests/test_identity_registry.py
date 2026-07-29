from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.identity_registry import IdentityRegistry  # noqa: E402


def _payload(person_id: str = "summer") -> dict:
    return {
        "person_id": person_id,
        "display_name": "心夏",
        "accounts": [
            {
                "platform_id": "qq-main",
                "user_id": "10001",
                "bot_id": "90001",
                "session_id": "qq-main:FriendMessage:10001",
            },
            {
                "platform_id": "telegram-main",
                "user_id": "tg-42",
                "bot_id": "tg-bot",
                "session_id": "telegram-main:FriendMessage:tg-42",
            },
        ],
    }


def test_registry_persists_and_resolves_verified_account(tmp_path):
    path = tmp_path / "identities.json"
    registry = IdentityRegistry(path)
    person = registry.upsert(_payload())

    reloaded = IdentityRegistry(path)
    resolved = reloaded.resolve(
        platform_candidates=("aiocqhttp", "qq-main"),
        user_id="10001",
        bot_id="90001",
    )

    assert resolved is not None
    assert resolved.person.person_id == person.person_id
    assert resolved.person.relationship_key == "person:user:summer"
    assert resolved.person.alias_state_keys == (
        "90001:user:10001",
        "tg-bot:user:tg-42",
    )


def test_registry_does_not_match_wrong_bot_instance(tmp_path):
    registry = IdentityRegistry(tmp_path / "identities.json")
    registry.upsert(_payload())

    assert (
        registry.resolve(
            platform_candidates=("qq-main",),
            user_id="10001",
            bot_id="another-bot",
        )
        is None
    )


def test_account_cannot_belong_to_two_people(tmp_path):
    registry = IdentityRegistry(tmp_path / "identities.json")
    registry.upsert(_payload("summer"))
    duplicate = _payload("other")
    duplicate["display_name"] = "另一个人"

    with pytest.raises(ValueError, match="ACCOUNT_ALREADY_BOUND"):
        registry.upsert(duplicate)


def test_delete_only_removes_binding_file_entry(tmp_path):
    registry = IdentityRegistry(tmp_path / "identities.json")
    registry.upsert(_payload())

    assert registry.delete("summer")
    assert registry.list_persons() == []
    assert not registry.delete("summer")
