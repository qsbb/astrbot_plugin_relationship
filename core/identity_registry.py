"""Manually verified natural-person identities across platform accounts."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .profiles import account_state_key, person_state_key, validate_profile_id

SCHEMA_VERSION = 1
MAX_PERSONS = 1000
MAX_ACCOUNTS_PER_PERSON = 20
_PERSON_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


@dataclass(frozen=True)
class PlatformAccount:
    platform_id: str
    user_id: str
    bot_id: str = ""
    session_id: str = ""
    label: str = ""
    memory_profile_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.platform_id.casefold()}\x1f{self.user_id}\x1f{self.bot_id}"

    @property
    def state_key(self) -> str:
        if not self.bot_id or not self.user_id:
            return ""
        return f"{self.bot_id}:user:{self.user_id}"

    def state_key_for(self, profile_id: str) -> str:
        if not self.bot_id or not self.user_id:
            return ""
        return account_state_key(profile_id, self.bot_id, self.user_id)

    def as_dict(self) -> dict[str, str]:
        return {
            "platform_id": self.platform_id,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "session_id": self.session_id,
            "label": self.label,
            "memory_profile_id": self.memory_profile_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PlatformAccount":
        raw_memory_profile = _clean(value.get("memory_profile_id"), 64)
        try:
            memory_profile_id = (
                validate_profile_id(raw_memory_profile) if raw_memory_profile else ""
            )
        except ValueError:
            memory_profile_id = ""
        return cls(
            platform_id=_clean(value.get("platform_id") or value.get("platform"), 120),
            user_id=_clean(value.get("user_id"), 120),
            bot_id=_clean(value.get("bot_id"), 120),
            session_id=_clean(value.get("session_id"), 240),
            label=_clean(value.get("label"), 80),
            memory_profile_id=memory_profile_id,
        )


@dataclass(frozen=True)
class PersonIdentity:
    person_id: str
    display_name: str
    accounts: tuple[PlatformAccount, ...]
    created_at: float
    updated_at: float

    @property
    def relationship_key(self) -> str:
        return f"person:user:{self.person_id}"

    def relationship_key_for(self, profile_id: str) -> str:
        return person_state_key(profile_id, self.person_id)

    @property
    def alias_state_keys(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                account.state_key for account in self.accounts if account.state_key
            )
        )

    def alias_state_keys_for(self, profile_id: str) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                account.state_key_for(profile_id)
                for account in self.accounts
                if account.state_key_for(profile_id)
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "display_name": self.display_name,
            "accounts": [account.as_dict() for account in self.accounts],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ResolvedIdentity:
    person: PersonIdentity
    account: PlatformAccount


class IdentityRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._persons: dict[str, PersonIdentity] = {}
        self._load()

    def list_persons(self) -> list[dict[str, Any]]:
        return [
            person.as_dict()
            for person in sorted(
                self._persons.values(),
                key=lambda item: (item.display_name.casefold(), item.person_id),
            )
        ]

    def get(self, person_id: str) -> PersonIdentity | None:
        person_id = str(person_id or "").strip()
        if not _PERSON_ID_RE.fullmatch(person_id):
            return None
        return self._persons.get(person_id)

    def snapshot(self) -> dict[str, PersonIdentity]:
        """Return an immutable-value snapshot for a surrounding transaction."""
        return dict(self._persons)

    def restore(self, persons: dict[str, PersonIdentity]) -> None:
        """Atomically restore a previously captured registry snapshot."""
        restored = dict(persons)
        self._write(restored)
        self._persons = restored

    def resolve(
        self,
        *,
        platform_candidates: Iterable[str],
        user_id: str,
        bot_id: str = "",
    ) -> ResolvedIdentity | None:
        platforms = {
            _clean(value, 120).casefold()
            for value in platform_candidates
            if _clean(value, 120)
        }
        user_id = _clean(user_id, 120)
        bot_id = _clean(bot_id, 120)
        if not platforms or not user_id:
            return None
        for person in self._persons.values():
            for account in person.accounts:
                if account.platform_id.casefold() not in platforms:
                    continue
                if account.user_id != user_id:
                    continue
                if account.bot_id and account.bot_id != bot_id:
                    continue
                return ResolvedIdentity(person=person, account=account)
        return None

    def resolve_unique_account(
        self,
        *,
        user_id: str,
        bot_id: str = "",
    ) -> ResolvedIdentity | None:
        """Resolve without a platform only when all matching accounts share a person."""
        user_id = _clean(user_id, 120)
        bot_id = _clean(bot_id, 120)
        if not user_id:
            return None
        matches: dict[str, ResolvedIdentity] = {}
        for person in self._persons.values():
            for account in person.accounts:
                if account.user_id != user_id:
                    continue
                if account.bot_id and account.bot_id != bot_id:
                    continue
                matches.setdefault(
                    person.person_id,
                    ResolvedIdentity(person=person, account=account),
                )
        if len(matches) > 1:
            raise ValueError("AMBIGUOUS_ACCOUNT")
        return next(iter(matches.values()), None)

    def resolve_bound_session(
        self,
        *,
        person_id: str,
        session_id: str,
    ) -> ResolvedIdentity | None:
        """Resolve a session only when it is globally unique and owned by person_id."""
        person_id = _clean(person_id, 64)
        session_id = _clean(session_id, 240)
        if not person_id or not session_id:
            return None
        matches = [
            ResolvedIdentity(person=person, account=account)
            for person in self._persons.values()
            for account in person.accounts
            if account.session_id == session_id
        ]
        if len(matches) > 1:
            raise ValueError("AMBIGUOUS_SESSION")
        resolved = next(iter(matches), None)
        if resolved is None or resolved.person.person_id != person_id:
            return None
        return resolved

    def upsert(self, payload: dict[str, Any]) -> PersonIdentity:
        person = self._build_person(payload, self._persons)
        updated = dict(self._persons)
        updated[person.person_id] = person
        self._write(updated)
        self._persons = updated
        return person

    def merge_account(
        self, person_id: str, raw_account: dict[str, Any]
    ) -> tuple[PersonIdentity, bool]:
        """Append or enrich one verified account without replacing existing accounts."""
        person, changed, _ = self.preview_merge_account(person_id, raw_account)
        if not changed:
            return person, False
        updated = dict(self._persons)
        updated[person.person_id] = person
        self._write(updated)
        self._persons = updated
        return person, True

    def preview_merge_account(
        self, person_id: str, raw_account: dict[str, Any]
    ) -> tuple[PersonIdentity, bool, PlatformAccount]:
        """Validate an account merge and return its exact post-merge value."""
        person_id = str(person_id or "").strip()
        if not _PERSON_ID_RE.fullmatch(person_id):
            raise ValueError("INVALID_PERSON_ID")
        current = self._persons.get(person_id)
        if current is None:
            raise ValueError("TARGET_PERSON_NOT_FOUND")
        if not isinstance(raw_account, dict):
            raise ValueError("INVALID_ACCOUNT")
        raw_memory_profile = _clean(raw_account.get("memory_profile_id"), 64)
        if raw_memory_profile:
            try:
                validate_profile_id(raw_memory_profile)
            except ValueError as exc:
                raise ValueError("INVALID_MEMORY_PROFILE_ID") from exc

        incoming = PlatformAccount.from_dict(raw_account)
        if not incoming.platform_id or not incoming.user_id:
            raise ValueError("ACCOUNT_ID_REQUIRED")
        owner_key = (incoming.platform_id.casefold(), incoming.user_id)
        accounts = list(current.accounts)
        changed = False
        merged_account = incoming
        for index, existing in enumerate(accounts):
            existing_key = (existing.platform_id.casefold(), existing.user_id)
            if existing_key != owner_key:
                continue
            if existing.bot_id and incoming.bot_id and existing.bot_id != incoming.bot_id:
                raise ValueError("ACCOUNT_BOT_CONFLICT")
            if (
                existing.session_id
                and incoming.session_id
                and existing.session_id != incoming.session_id
            ):
                raise ValueError("ACCOUNT_SESSION_CONFLICT")
            if (
                existing.memory_profile_id
                and incoming.memory_profile_id
                and existing.memory_profile_id != incoming.memory_profile_id
            ):
                raise ValueError("ACCOUNT_MEMORY_PROFILE_CONFLICT")
            merged_account = PlatformAccount(
                platform_id=existing.platform_id,
                user_id=existing.user_id,
                bot_id=incoming.bot_id or existing.bot_id,
                session_id=incoming.session_id or existing.session_id,
                label=incoming.label or existing.label,
                memory_profile_id=(
                    incoming.memory_profile_id or existing.memory_profile_id
                ),
            )
            changed = merged_account != existing
            accounts[index] = merged_account
            break
        else:
            accounts.append(incoming)
            changed = True

        if not changed:
            return current, False, merged_account
        person = self._build_person(
            {
                "person_id": current.person_id,
                "display_name": current.display_name,
                "accounts": [account.as_dict() for account in accounts],
            },
            self._persons,
        )
        return person, True, merged_account

    def merge_persons(
        self, source_person_id: str, target_person_id: str
    ) -> tuple[PersonIdentity, PersonIdentity]:
        """Move all accounts from source into target and remove source atomically."""
        source_person_id = str(source_person_id or "").strip()
        target_person_id = str(target_person_id or "").strip()
        if not _PERSON_ID_RE.fullmatch(source_person_id) or not _PERSON_ID_RE.fullmatch(
            target_person_id
        ):
            raise ValueError("INVALID_PERSON_ID")
        if source_person_id == target_person_id:
            raise ValueError("SAME_PERSON_IDENTITY")
        source = self._persons.get(source_person_id)
        target = self._persons.get(target_person_id)
        if source is None:
            raise ValueError("SOURCE_PERSON_NOT_FOUND")
        if target is None:
            raise ValueError("TARGET_PERSON_NOT_FOUND")

        remaining = dict(self._persons)
        remaining.pop(source_person_id, None)
        merged = self._build_person(
            {
                "person_id": target.person_id,
                "display_name": target.display_name,
                "accounts": [
                    account.as_dict() for account in (*target.accounts, *source.accounts)
                ],
            },
            remaining,
        )
        remaining[target_person_id] = merged
        self._write(remaining)
        self._persons = remaining
        return merged, source

    def _build_person(
        self,
        payload: dict[str, Any],
        persons: dict[str, PersonIdentity],
    ) -> PersonIdentity:
        person_id = str(payload.get("person_id") or "").strip()
        if not person_id:
            person_id = f"person_{uuid.uuid4().hex[:12]}"
        if not _PERSON_ID_RE.fullmatch(person_id):
            raise ValueError("INVALID_PERSON_ID")
        display_name = _clean(payload.get("display_name"), 80)
        if not display_name:
            raise ValueError("DISPLAY_NAME_REQUIRED")
        raw_accounts = payload.get("accounts")
        if not isinstance(raw_accounts, list) or not raw_accounts:
            raise ValueError("ACCOUNT_REQUIRED")
        if len(raw_accounts) > MAX_ACCOUNTS_PER_PERSON:
            raise ValueError("TOO_MANY_ACCOUNTS")

        accounts: list[PlatformAccount] = []
        seen: set[tuple[str, str]] = set()
        seen_sessions: set[str] = set()
        for raw in raw_accounts:
            if not isinstance(raw, dict):
                raise ValueError("INVALID_ACCOUNT")
            raw_memory_profile = _clean(raw.get("memory_profile_id"), 64)
            if raw_memory_profile:
                try:
                    validate_profile_id(raw_memory_profile)
                except ValueError as exc:
                    raise ValueError("INVALID_MEMORY_PROFILE_ID") from exc
            account = PlatformAccount.from_dict(raw)
            if not account.platform_id or not account.user_id:
                raise ValueError("ACCOUNT_ID_REQUIRED")
            owner_key = (account.platform_id.casefold(), account.user_id)
            if owner_key in seen:
                raise ValueError("DUPLICATE_ACCOUNT")
            if account.session_id and account.session_id in seen_sessions:
                raise ValueError("DUPLICATE_SESSION")
            seen.add(owner_key)
            if account.session_id:
                seen_sessions.add(account.session_id)
            accounts.append(account)

        for existing in persons.values():
            if existing.person_id == person_id:
                continue
            occupied = {
                (item.platform_id.casefold(), item.user_id)
                for item in existing.accounts
            }
            if occupied.intersection(seen):
                raise ValueError("ACCOUNT_ALREADY_BOUND")
            occupied_sessions = {
                item.session_id for item in existing.accounts if item.session_id
            }
            if occupied_sessions.intersection(seen_sessions):
                raise ValueError("SESSION_ALREADY_BOUND")
        if person_id not in persons and len(persons) >= MAX_PERSONS:
            raise ValueError("TOO_MANY_PERSONS")

        now = time.time()
        old = persons.get(person_id)
        return PersonIdentity(
            person_id=person_id,
            display_name=display_name,
            accounts=tuple(accounts),
            created_at=old.created_at if old else now,
            updated_at=now,
        )

    def delete(self, person_id: str) -> bool:
        person_id = str(person_id or "").strip()
        if not _PERSON_ID_RE.fullmatch(person_id):
            raise ValueError("INVALID_PERSON_ID")
        if person_id not in self._persons:
            return False
        updated = dict(self._persons)
        updated.pop(person_id, None)
        self._write(updated)
        self._persons = updated
        return True

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != SCHEMA_VERSION
        ):
            return
        raw_persons = payload.get("persons")
        if not isinstance(raw_persons, list):
            return
        loaded: dict[str, PersonIdentity] = {}
        occupied: set[tuple[str, str]] = set()
        occupied_sessions: set[str] = set()
        for raw in raw_persons[:MAX_PERSONS]:
            if not isinstance(raw, dict):
                continue
            person_id = _clean(raw.get("person_id"), 64)
            display_name = _clean(raw.get("display_name"), 80)
            raw_accounts = raw.get("accounts")
            if (
                not _PERSON_ID_RE.fullmatch(person_id)
                or not display_name
                or not isinstance(raw_accounts, list)
            ):
                continue
            accounts: list[PlatformAccount] = []
            for value in raw_accounts[:MAX_ACCOUNTS_PER_PERSON]:
                if not isinstance(value, dict):
                    continue
                account = PlatformAccount.from_dict(value)
                key = (account.platform_id.casefold(), account.user_id)
                if (
                    not account.platform_id
                    or not account.user_id
                    or key in occupied
                    or (
                        bool(account.session_id)
                        and account.session_id in occupied_sessions
                    )
                ):
                    continue
                occupied.add(key)
                if account.session_id:
                    occupied_sessions.add(account.session_id)
                accounts.append(account)
            if not accounts:
                continue
            try:
                created_at = float(raw.get("created_at") or 0.0)
                updated_at = float(raw.get("updated_at") or created_at)
            except (TypeError, ValueError, OverflowError):
                created_at = updated_at = 0.0
            loaded[person_id] = PersonIdentity(
                person_id=person_id,
                display_name=display_name,
                accounts=tuple(accounts),
                created_at=created_at,
                updated_at=updated_at,
            )
        self._persons = loaded

    def _write(self, persons: dict[str, PersonIdentity]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "persons": [person.as_dict() for person in persons.values()],
        }
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
