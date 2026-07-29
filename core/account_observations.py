"""Persist minimal account metadata observed during real conversations."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_OBSERVATIONS = 1000


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _timestamp(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) and parsed >= 0 else 0.0


class AccountObservationStore:
    """Small metadata-only cache used to prefill identity bindings."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._items: dict[str, dict[str, Any]] = {}
        self._load()

    @staticmethod
    def _key(bot_id: str, user_id: str) -> str:
        return f"{bot_id}\x1f{user_id}"

    def get(self, bot_id: str, user_id: str) -> dict[str, Any] | None:
        key = self._key(_clean(bot_id, 120), _clean(user_id, 120))
        item = self._items.get(key)
        return dict(item) if item is not None else None

    def record(
        self,
        *,
        bot_id: str,
        user_id: str,
        platform_id: str = "",
        private_umo: str = "",
        display_name: str = "",
        relationship_profile_id: str = "",
        now: float | None = None,
    ) -> bool:
        bot_id = _clean(bot_id, 120)
        user_id = _clean(user_id, 120)
        if not bot_id or not user_id:
            return False
        key = self._key(bot_id, user_id)
        previous = self._items.get(key, {})
        current = {
            "platform_id": _clean(platform_id, 120)
            or _clean(previous.get("platform_id"), 120),
            "user_id": user_id,
            "bot_id": bot_id,
            "session_id": _clean(private_umo, 240)
            or _clean(previous.get("session_id"), 240),
            "display_name": _clean(display_name, 80)
            or _clean(previous.get("display_name"), 80),
            "relationship_profile_id": _clean(relationship_profile_id, 64)
            or _clean(previous.get("relationship_profile_id"), 64),
        }
        comparable_previous = {field: previous.get(field, "") for field in current}
        if comparable_previous == current:
            return False
        current["observed_at"] = _timestamp(now if now is not None else time.time())
        self._items[key] = current
        self._prune()
        self._write()
        return True

    def _prune(self) -> None:
        if len(self._items) <= MAX_OBSERVATIONS:
            return
        ordered = sorted(
            self._items.items(),
            key=lambda item: _timestamp(item[1].get("observed_at")),
            reverse=True,
        )
        self._items = dict(ordered[:MAX_OBSERVATIONS])

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
            or not isinstance(payload.get("items"), dict)
        ):
            return
        for value in payload["items"].values():
            if not isinstance(value, dict):
                continue
            bot_id = _clean(value.get("bot_id"), 120)
            user_id = _clean(value.get("user_id"), 120)
            if not bot_id or not user_id:
                continue
            self._items[self._key(bot_id, user_id)] = {
                "platform_id": _clean(value.get("platform_id"), 120),
                "user_id": user_id,
                "bot_id": bot_id,
                "session_id": _clean(value.get("session_id"), 240),
                "display_name": _clean(value.get("display_name"), 80),
                "relationship_profile_id": _clean(
                    value.get("relationship_profile_id"), 64
                ),
                "observed_at": _timestamp(value.get("observed_at")),
            }
        self._prune()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "items": self._items,
        }
        fd, temporary = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
