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


def test_session_cannot_be_reused_by_another_account_or_person(tmp_path):
    registry = IdentityRegistry(tmp_path / "identities.json")
    registry.upsert(_payload("summer"))

    same_person = _payload("summer")
    same_person["accounts"][1]["session_id"] = same_person["accounts"][0]["session_id"]
    with pytest.raises(ValueError, match="DUPLICATE_SESSION"):
        registry.upsert(same_person)

    other = _payload("other")
    other["display_name"] = "另一个人"
    other["accounts"] = [
        {
            "platform_id": "another-platform",
            "user_id": "another-user",
            "bot_id": "another-bot",
            "session_id": "qq-main:FriendMessage:10001",
        }
    ]
    with pytest.raises(ValueError, match="SESSION_ALREADY_BOUND"):
        registry.upsert(other)


def test_resolve_bound_session_requires_global_unique_owner(tmp_path):
    registry = IdentityRegistry(tmp_path / "identities.json")
    registry.upsert(_payload("summer"))

    resolved = registry.resolve_bound_session(
        person_id="summer", session_id="qq-main:FriendMessage:10001"
    )
    assert resolved is not None
    assert resolved.person.person_id == "summer"
    assert (
        registry.resolve_bound_session(
            person_id="other", session_id="qq-main:FriendMessage:10001"
        )
        is None
    )


def test_delete_only_removes_binding_file_entry(tmp_path):
    registry = IdentityRegistry(tmp_path / "identities.json")
    registry.upsert(_payload())

    assert registry.delete("summer")
    assert registry.list_persons() == []
    assert not registry.delete("summer")


def test_merge_account_appends_without_replacing_and_is_idempotent(tmp_path):
    path = tmp_path / "identities.json"
    registry = IdentityRegistry(path)
    before = registry.upsert(_payload())
    account = {
        "platform_id": "discord-main",
        "user_id": "discord-7",
        "bot_id": "discord-bot",
    }

    merged, changed = registry.merge_account("summer", account)
    enriched, enriched_changed = registry.merge_account(
        "summer",
        {**account, "session_id": "discord-main:DirectMessage:discord-7"},
    )
    repeated, repeated_changed = registry.merge_account(
        "summer",
        {**account, "session_id": "discord-main:DirectMessage:discord-7"},
    )

    assert changed
    assert enriched_changed
    assert not repeated_changed
    assert merged.created_at == before.created_at
    assert enriched.display_name == before.display_name
    assert len(repeated.accounts) == 3
    assert repeated.accounts[-1].session_id == "discord-main:DirectMessage:discord-7"
    assert len(IdentityRegistry(path).get("summer").accounts) == 3


def test_merge_account_rejects_account_owned_by_another_person(tmp_path):
    registry = IdentityRegistry(tmp_path / "identities.json")
    registry.upsert(_payload("summer"))
    registry.upsert(
        {
            "person_id": "other",
            "display_name": "另一个人",
            "accounts": [{"platform_id": "discord-main", "user_id": "discord-7"}],
        }
    )

    with pytest.raises(ValueError, match="ACCOUNT_ALREADY_BOUND"):
        registry.merge_account(
            "summer", {"platform_id": "discord-main", "user_id": "discord-7"}
        )

    assert len(registry.get("summer").accounts) == 2
    assert len(registry.get("other").accounts) == 1


def test_merge_account_rejects_conflicting_bot_or_session_metadata(tmp_path):
    registry = IdentityRegistry(tmp_path / "identities.json")
    registry.upsert(_payload("summer"))

    with pytest.raises(ValueError, match="ACCOUNT_BOT_CONFLICT"):
        registry.merge_account(
            "summer",
            {"platform_id": "qq-main", "user_id": "10001", "bot_id": "other-bot"},
        )
    with pytest.raises(ValueError, match="ACCOUNT_SESSION_CONFLICT"):
        registry.merge_account(
            "summer",
            {
                "platform_id": "qq-main",
                "user_id": "10001",
                "session_id": "qq-main:FriendMessage:other-user",
            },
        )
    registry.upsert(
        {
            **_payload("summer"),
            "accounts": [
                {**_payload("summer")["accounts"][0], "memory_profile_id": "persona-a"},
                _payload("summer")["accounts"][1],
            ],
        }
    )
    with pytest.raises(ValueError, match="ACCOUNT_MEMORY_PROFILE_CONFLICT"):
        registry.merge_account(
            "summer",
            {
                "platform_id": "qq-main",
                "user_id": "10001",
                "memory_profile_id": "persona-b",
            },
        )

    assert len(registry.get("summer").accounts) == 2


def test_merge_persons_moves_all_accounts_and_removes_source(tmp_path):
    registry = IdentityRegistry(tmp_path / "identities.json")
    target = registry.upsert(_payload("summer"))
    registry.upsert(
        {
            "person_id": "work-account",
            "display_name": "工作账号",
            "accounts": [
                {
                    "platform_id": "discord-main",
                    "user_id": "discord-7",
                    "bot_id": "discord-bot",
                }
            ],
        }
    )

    merged, source = registry.merge_persons("work-account", "summer")

    assert source.person_id == "work-account"
    assert registry.get("work-account") is None
    assert merged.display_name == target.display_name
    assert merged.created_at == target.created_at
    assert len(merged.accounts) == 3
