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

    @property
    def key(self) -> str:
        return f"{self.platform_id.casefold()}\x1f{self.user_id}\x1f{self.bot_id}"

    @property
    def state_key(self) -> str:
        if not self.bot_id or not self.user_id:
            return ""
        return f"{self.bot_id}:user:{self.user_id}"

    def as_dict(self) -> dict[str, str]:
        return {
            "platform_id": self.platform_id,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "session_id": self.session_id,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PlatformAccount":
        return cls(
            platform_id=_clean(value.get("platform_id") or value.get("platform"), 120),
            user_id=_clean(value.get("user_id"), 120),
            bot_id=_clean(value.get("bot_id"), 120),
            session_id=_clean(value.get("session_id"), 240),
            label=_clean(value.get("label"), 80),
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

    @property
    def alias_state_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(account.state_key for account in self.accounts if account.state_key))

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
        return self._persons.get(_clean(person_id, 64))

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

    def upsert(self, payload: dict[str, Any]) -> PersonIdentity:
        person_id = _clean(payload.get("person_id"), 64)
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
        for raw in raw_accounts:
            if not isinstance(raw, dict):
                raise ValueError("INVALID_ACCOUNT")
            account = PlatformAccount.from_dict(raw)
            if not account.platform_id or not account.user_id:
                raise ValueError("ACCOUNT_ID_REQUIRED")
            owner_key = (account.platform_id.casefold(), account.user_id)
            if owner_key in seen:
                raise ValueError("DUPLICATE_ACCOUNT")
            seen.add(owner_key)
            accounts.append(account)

        for existing in self._persons.values():
            if existing.person_id == person_id:
                continue
            occupied = {
                (item.platform_id.casefold(), item.user_id)
                for item in existing.accounts
            }
            if occupied.intersection(seen):
                raise ValueError("ACCOUNT_ALREADY_BOUND")
        if person_id not in self._persons and len(self._persons) >= MAX_PERSONS:
            raise ValueError("TOO_MANY_PERSONS")

        now = time.time()
        old = self._persons.get(person_id)
        person = PersonIdentity(
            person_id=person_id,
            display_name=display_name,
            accounts=tuple(accounts),
            created_at=old.created_at if old else now,
            updated_at=now,
        )
        updated = dict(self._persons)
        updated[person_id] = person
        self._write(updated)
        self._persons = updated
        return person

    def delete(self, person_id: str) -> bool:
        person_id = _clean(person_id, 64)
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
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            return
        raw_persons = payload.get("persons")
        if not isinstance(raw_persons, list):
            return
        loaded: dict[str, PersonIdentity] = {}
        occupied: set[tuple[str, str]] = set()
        for raw in raw_persons[:MAX_PERSONS]:
            if not isinstance(raw, dict):
                continue
            person_id = _clean(raw.get("person_id"), 64)
            display_name = _clean(raw.get("display_name"), 80)
            raw_accounts = raw.get("accounts")
            if not _PERSON_ID_RE.fullmatch(person_id) or not display_name or not isinstance(raw_accounts, list):
                continue
            accounts: list[PlatformAccount] = []
            for value in raw_accounts[:MAX_ACCOUNTS_PER_PERSON]:
                if not isinstance(value, dict):
                    continue
                account = PlatformAccount.from_dict(value)
                key = (account.platform_id.casefold(), account.user_id)
                if not account.platform_id or not account.user_id or key in occupied:
                    continue
                occupied.add(key)
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
