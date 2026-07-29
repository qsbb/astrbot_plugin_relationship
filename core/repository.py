"""关系状态与审计事件的 JSON 仓库。

schema v4 把长期状态、事件与去重键纳入关系人格 profile。v3 历史只迁入
管理员指定的 legacy profile，不会被每个新人格重复继承。账本不保存消息正文；
JSON 写入仍采用临时文件 + 原子替换。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from .models import RelationshipEventRecord, UserRelationState
from .profiles import (
    DEFAULT_PROFILE_ID,
    account_state_key,
    migrate_legacy_state_key,
    normalize_profile_id,
)

SCHEMA_VERSION = 4


class RelationshipRepository(Protocol):
    def load_all(self) -> dict[str, UserRelationState]: ...

    def load_events(self) -> list[RelationshipEventRecord]: ...

    def save(
        self,
        states: dict[str, UserRelationState],
        events: list[RelationshipEventRecord],
    ) -> None: ...


class MemoryRepository:
    def __init__(self) -> None:
        self._data: dict[str, UserRelationState] = {}
        self._events: list[RelationshipEventRecord] = []

    def load_all(self) -> dict[str, UserRelationState]:
        return {
            key: UserRelationState.from_dict(value.as_dict())
            for key, value in self._data.items()
        }

    def load_events(self) -> list[RelationshipEventRecord]:
        return list(self._events)

    def save(
        self,
        states: dict[str, UserRelationState],
        events: list[RelationshipEventRecord],
    ) -> None:
        self._data = {
            key: UserRelationState.from_dict(value.as_dict())
            for key, value in states.items()
        }
        self._events = list(events)

    def save_all(self, states: dict[str, UserRelationState]) -> None:
        """0.1.0 测试/调用兼容层。"""
        self.save(states, self._events)


def _empty_payload() -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "users": {}, "events": []}


def _migrate(
    payload: dict[str, object], legacy_profile_id: str = DEFAULT_PROFILE_ID
) -> dict[str, object]:
    try:
        version = int(payload.get("schema_version", 0))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return _empty_payload()
    if version < 0:
        return _empty_payload()
    if version == 0:
        users = payload.get("users")
        if not isinstance(users, dict):
            users = {k: v for k, v in payload.items() if isinstance(v, dict)}
        payload = {"schema_version": 1, "users": users}
        version = 1
    if version == 1:
        payload = {
            "schema_version": 2,
            "users": payload.get("users", {}),
            "events": [],
        }
        version = 2
    if version == 2:
        raw_events = payload.get("events", [])
        migrated_events: list[object] = []
        if isinstance(raw_events, list):
            for value in raw_events:
                if not isinstance(value, dict):
                    continue
                event = dict(value)
                namespace = (
                    f"{event.get('bot_id', '')}\x1f{event.get('user_id', '')}\x1f"
                    f"{event.get('group_id') or ''}\x1f"
                )
                raw_event_id = str(event.get("event_id", ""))
                raw_dedupe = str(event.get("dedupe_key", ""))
                if raw_event_id and not raw_event_id.startswith(namespace):
                    event["event_id"] = namespace + raw_event_id
                if raw_dedupe and not raw_dedupe.startswith(namespace):
                    event["dedupe_key"] = namespace + raw_dedupe
                elif not raw_dedupe and raw_event_id:
                    event["dedupe_key"] = namespace + raw_event_id
                migrated_events.append(event)
        payload = {
            "schema_version": 3,
            "users": payload.get("users", {}),
            "events": migrated_events,
        }
        version = 3
    if version == 3:
        profile_id = normalize_profile_id(legacy_profile_id)
        raw_users = payload.get("users", {})
        migrated_users: dict[str, object] = {}
        if isinstance(raw_users, dict):
            for key, value in raw_users.items():
                if not isinstance(value, dict):
                    continue
                migrated_users.setdefault(
                    migrate_legacy_state_key(str(key), profile_id), value
                )

        raw_events = payload.get("events", [])
        migrated_events: list[object] = []
        if isinstance(raw_events, list):
            for value in raw_events:
                if not isinstance(value, dict):
                    continue
                event = dict(value)
                bot_id = str(event.get("bot_id", ""))
                user_id = str(event.get("user_id", ""))
                group_id = str(event.get("group_id") or "")
                old_namespace = f"{bot_id}\x1f{user_id}\x1f{group_id}\x1f"
                new_namespace = f"{profile_id}\x1f{old_namespace}"
                raw_event_id = str(event.get("event_id", ""))
                raw_dedupe = str(event.get("dedupe_key", ""))
                while raw_event_id.startswith(old_namespace):
                    raw_event_id = raw_event_id[len(old_namespace) :]
                while raw_dedupe.startswith(old_namespace):
                    raw_dedupe = raw_dedupe[len(old_namespace) :]
                event["event_id"] = new_namespace + raw_event_id
                event["dedupe_key"] = new_namespace + (
                    raw_dedupe or raw_event_id
                )
                event["relationship_profile_id"] = profile_id
                event["scope_key"] = account_state_key(
                    profile_id, bot_id, user_id
                )
                migrated_events.append(event)
        payload = {
            "schema_version": 4,
            "users": migrated_users,
            "events": migrated_events,
        }
        version = 4
    if version > SCHEMA_VERSION:
        payload = _empty_payload()
    return payload


class JsonRepository:
    def __init__(
        self,
        file_path: str | Path,
        *,
        legacy_profile_id: str = DEFAULT_PROFILE_ID,
    ) -> None:
        self._path = Path(file_path)
        self._legacy_profile_id = normalize_profile_id(legacy_profile_id)
        self._write_blocked = False

    @property
    def path(self) -> Path:
        return self._path

    def _load_payload(self) -> dict[str, object]:
        if not self._path.exists():
            return _empty_payload()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._write_blocked = False
            return _empty_payload()
        if not isinstance(payload, dict):
            self._write_blocked = False
            return _empty_payload()
        try:
            source_version = int(payload.get("schema_version", 0))  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            self._write_blocked = True
            return _empty_payload()
        if source_version < 0:
            self._write_blocked = True
            return _empty_payload()
        migrated = _migrate(payload, self._legacy_profile_id)
        self._write_blocked = source_version > SCHEMA_VERSION
        if source_version < SCHEMA_VERSION and not self._write_blocked:
            try:
                self._backup_before_migration(source_version)
                self._atomic_write(migrated)
            except OSError:
                self._write_blocked = True
        return migrated

    def _backup_before_migration(self, source_version: int) -> None:
        if source_version < 0 or not self._path.exists():
            return
        backup = self._path.with_name(f"{self._path.name}.v{source_version}.bak")
        if not backup.exists():
            shutil.copy2(self._path, backup)

    def load_all(self) -> dict[str, UserRelationState]:
        users = self._load_payload().get("users")
        if not isinstance(users, dict):
            return {}
        return {
            str(key): UserRelationState.from_dict(value)
            for key, value in users.items()
            if isinstance(value, dict)
        }

    def load_events(self) -> list[RelationshipEventRecord]:
        raw_events = self._load_payload().get("events")
        if not isinstance(raw_events, list):
            return []
        return [
            RelationshipEventRecord.from_dict(value)
            for value in raw_events
            if isinstance(value, dict)
        ]

    def save(
        self,
        states: dict[str, UserRelationState],
        events: list[RelationshipEventRecord],
    ) -> None:
        if self._write_blocked:
            raise OSError("refusing to overwrite a future relationship schema")
        scoped_states = {
            migrate_legacy_state_key(key, self._legacy_profile_id): state
            for key, state in states.items()
        }
        payload = {
            "schema_version": SCHEMA_VERSION,
            "users": {key: state.as_dict() for key, state in scoped_states.items()},
            "events": [event.as_dict() for event in events],
        }
        self._atomic_write(payload)

    def save_all(self, states: dict[str, UserRelationState]) -> None:
        """0.1.0 兼容层：保留已有事件后写入状态。"""
        self.save(states, self.load_events())

    def _atomic_write(self, payload: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=self._path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
