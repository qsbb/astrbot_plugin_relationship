"""关系状态与审计事件的 JSON 仓库。

schema v2 在 v1 聚合快照基础上新增只追加审计事件。账本不保存消息正文；
JSON 写入仍采用临时文件 + 原子替换。仓库只负责存取，不负责业务重放。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from .models import RelationshipEventRecord, UserRelationState

SCHEMA_VERSION = 2


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
        return {key: UserRelationState.from_dict(value.as_dict()) for key, value in self._data.items()}

    def load_events(self) -> list[RelationshipEventRecord]:
        return list(self._events)

    def save(
        self,
        states: dict[str, UserRelationState],
        events: list[RelationshipEventRecord],
    ) -> None:
        self._data = {
            key: UserRelationState.from_dict(value.as_dict()) for key, value in states.items()
        }
        self._events = list(events)

    def save_all(self, states: dict[str, UserRelationState]) -> None:
        """0.1.0 测试/调用兼容层。"""
        self.save(states, self._events)


def _migrate(payload: dict[str, object]) -> dict[str, object]:
    version = int(payload.get("schema_version", 0))  # type: ignore[arg-type]
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
    if version > SCHEMA_VERSION:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "users": payload.get("users", {}),
            "events": payload.get("events", []),
        }
    return payload


class JsonRepository:
    def __init__(self, file_path: str | Path) -> None:
        self._path = Path(file_path)

    @property
    def path(self) -> Path:
        return self._path

    def _load_payload(self) -> dict[str, object]:
        if not self._path.exists():
            return {"schema_version": SCHEMA_VERSION, "users": {}, "events": []}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": SCHEMA_VERSION, "users": {}, "events": []}
        if not isinstance(payload, dict):
            return {"schema_version": SCHEMA_VERSION, "users": {}, "events": []}
        return _migrate(payload)

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
        payload = {
            "schema_version": SCHEMA_VERSION,
            "users": {key: state.as_dict() for key, state in states.items()},
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
