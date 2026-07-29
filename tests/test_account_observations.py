"""Tests for metadata-only account observations used by quick binding."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.account_observations import AccountObservationStore  # noqa: E402


class AccountObservationStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "account_observations.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_persists_minimal_metadata_and_reloads(self) -> None:
        store = AccountObservationStore(self.path)

        changed = store.record(
            bot_id="bot-1",
            user_id="user-1",
            platform_id="qq-main",
            private_umo="qq-main:FriendMessage:user-1",
            display_name="心夏",
            relationship_profile_id="default",
            now=123.0,
        )

        self.assertTrue(changed)
        restored = AccountObservationStore(self.path).get("bot-1", "user-1")
        self.assertEqual(restored["platform_id"], "qq-main")
        self.assertEqual(restored["session_id"], "qq-main:FriendMessage:user-1")
        self.assertEqual(restored["display_name"], "心夏")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        fields = set(next(iter(payload["items"].values())))
        self.assertTrue({"bot_id", "user_id", "session_id"}.issubset(fields))
        self.assertTrue(fields.isdisjoint({"message", "content", "body", "text"}))

    def test_identical_metadata_does_not_rewrite(self) -> None:
        store = AccountObservationStore(self.path)
        fields = {
            "bot_id": "bot-1",
            "user_id": "user-1",
            "platform_id": "qq-main",
            "private_umo": "qq-main:FriendMessage:user-1",
        }

        self.assertTrue(store.record(**fields, now=1.0))
        before = self.path.read_bytes()
        self.assertFalse(store.record(**fields, now=2.0))
        self.assertEqual(self.path.read_bytes(), before)

    def test_group_observation_preserves_previous_private_umo(self) -> None:
        store = AccountObservationStore(self.path)
        store.record(
            bot_id="bot-1",
            user_id="user-1",
            private_umo="qq-main:FriendMessage:user-1",
        )

        store.record(
            bot_id="bot-1",
            user_id="user-1",
            platform_id="qq-main",
            private_umo="",
        )

        item = store.get("bot-1", "user-1")
        self.assertEqual(item["session_id"], "qq-main:FriendMessage:user-1")

    def test_malformed_file_is_ignored(self) -> None:
        self.path.write_text("not-json", encoding="utf-8")

        store = AccountObservationStore(self.path)

        self.assertIsNone(store.get("bot-1", "user-1"))

    def test_invalid_observed_timestamp_does_not_break_loading(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "items": {
                        "ignored-key": {
                            "bot_id": "bot-1",
                            "user_id": "user-1",
                            "observed_at": "not-a-time",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        item = AccountObservationStore(self.path).get("bot-1", "user-1")

        self.assertEqual(item["observed_at"], 0.0)


if __name__ == "__main__":
    unittest.main()
