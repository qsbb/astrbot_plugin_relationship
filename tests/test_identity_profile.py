from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.identity_registry import IdentityRegistry  # noqa: E402


def _identity_payload(*, memory_profile_id: str | None = None) -> dict:
    account = {
        "platform_id": "qq-main",
        "user_id": "user:42",
        "bot_id": "bot/one",
        "session_id": "qq-main:FriendMessage:user:42",
    }
    if memory_profile_id is not None:
        account["memory_profile_id"] = memory_profile_id
    return {
        "person_id": "summer",
        "display_name": "Summer",
        "accounts": [account],
    }


def test_old_identity_json_without_memory_profile_remains_compatible(tmp_path):
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "persons": [
                    {
                        **_identity_payload(),
                        "created_at": 1.0,
                        "updated_at": 2.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    person = IdentityRegistry(path).get("summer")

    assert person is not None
    assert person.accounts[0].memory_profile_id == ""
    assert person.relationship_key == "person:user:summer"
    assert person.alias_state_keys == ("bot/one:user:user:42",)


def test_memory_profile_is_cleaned_and_persisted(tmp_path):
    path = tmp_path / "identities.json"
    registry = IdentityRegistry(path)

    person = registry.upsert(
        _identity_payload(memory_profile_id=f"  {'p' * 80}  ")
    )
    reloaded = IdentityRegistry(path).get(person.person_id)

    assert reloaded is not None
    assert reloaded.accounts[0].memory_profile_id == "p" * 64
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["persons"][0]["accounts"][0]["memory_profile_id"] == "p" * 64


def test_profile_scoped_relationship_keys_keep_global_identity_compatible(tmp_path):
    person = IdentityRegistry(tmp_path / "identities.json").upsert(
        _identity_payload(memory_profile_id="companion")
    )

    assert person.relationship_key_for("persona-a") == "persona:persona-a:person:summer"
    assert person.alias_state_keys_for("persona-a") == (
        "persona:persona-a:account:bot%2Fone:user:user%3A42",
    )
    assert person.relationship_key == "person:user:summer"
    assert person.alias_state_keys == ("bot/one:user:user:42",)
