"""Managed ``series.webui@2.0`` surface for the relationship module.

All writes delegate to the plugin's existing identity transaction services.
The adapter owns presentation and opaque references only; it never keeps a
second identity registry or relationship state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from .core.identity_registry import PersonIdentity, PlatformAccount
from .core.models import (
    RELATIONSHIP_TYPE_ALIASES,
    RELATIONSHIP_TYPE_LABELS,
    RelationshipScope,
)
from .core.profiles import validate_profile_id

SERIES_ID = "ningxin_suxi"
PLUGIN_ID = "astrbot_plugin_relationship"
ROLE_RANK = {"viewer": 0, "admin": 1, "owner": 2}


class RelationshipWebUIAdapter:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    # -- contract ---------------------------------------------------------

    def contract(self) -> dict[str, object]:
        return {
            "name": "series.webui@2.0",
            "version": "2.0",
            "series_id": SERIES_ID,
            "plugin_id": PLUGIN_ID,
            "state_owner": "plugin",
            "preferred_surface": "kernel",
            "managed": {"supported": True, "level": "actions"},
            "standalone": {"available": True, "pages": ["manager"]},
            "capabilities": [
                "generic_table",
                "generic_actions",
                "revision",
                "idempotency",
            ],
            "panels": [
                {
                    "id": "overview",
                    "title": "关系总览",
                    "description": "查看关系状态并设置关系性质",
                    "actions": [self._set_type_declaration()],
                },
                {
                    "id": "identities",
                    "title": "自然人管理",
                    "description": "创建、更新、合并和解除自然人归属",
                    "actions": [
                        self._create_person_declaration(),
                        self._update_person_declaration(),
                        self._merge_people_declaration(),
                        self._delete_person_declaration(),
                    ],
                },
                {
                    "id": "accounts",
                    "title": "账号归属",
                    "description": "绑定、解绑和迁移平台账号；只显示 opaque 引用",
                    "actions": [
                        self._bind_account_declaration(),
                        self._unbind_account_declaration(),
                        self._migrate_account_declaration(),
                    ],
                },
            ],
        }

    @staticmethod
    def _set_type_declaration() -> dict[str, Any]:
        return {
            "id": "set_type",
            "label": "设置关系性质",
            "confirm": "确定修改该用户的关系性质？",
            "effect": "idempotent",
            "revision_required": True,
            "idempotency_required": False,
            "min_role": "admin",
            "timeout_seconds": 8,
            "payload_fields": [
                {
                    "name": "user_id",
                    "type": "text",
                    "label": "用户 ID（人物 ID 或账号 user_id）",
                    "required": True,
                    "hint": "从关系总览复制。",
                },
                {
                    "name": "scope_kind",
                    "type": "select",
                    "label": "范围",
                    "required": True,
                    "options": [("person", "人物"), ("account", "账号")],
                },
                {
                    "name": "relationship_type",
                    "type": "select",
                    "label": "关系性质",
                    "required": True,
                    "options": [
                        (value, label)
                        for value, label in RELATIONSHIP_TYPE_LABELS.items()
                    ],
                },
                {
                    "name": "bot_id",
                    "type": "text",
                    "label": "Bot ID（账号范围时必填）",
                    "required": False,
                },
                {
                    "name": "relationship_profile_id",
                    "type": "text",
                    "label": "关系 Profile ID（可选）",
                    "required": False,
                },
            ],
        }

    @staticmethod
    def _account_payload_fields(*, include_person: bool) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        if include_person:
            fields.append(
                {
                    "name": "person_ref",
                    "type": "text",
                    "label": "自然人引用 person_ref",
                    "required": True,
                    "hint": "从自然人管理面板复制。",
                }
            )
        fields.extend(
            [
                {
                    "name": "platform_id",
                    "type": "text",
                    "label": "平台 ID",
                    "required": True,
                },
                {
                    "name": "user_id",
                    "type": "text",
                    "label": "平台用户 ID",
                    "required": True,
                },
                {
                    "name": "bot_id",
                    "type": "text",
                    "label": "Bot ID（可选）",
                    "required": False,
                },
                {
                    "name": "session_id",
                    "type": "text",
                    "label": "私聊 UMO / session（可选）",
                    "required": False,
                },
                {
                    "name": "label",
                    "type": "text",
                    "label": "账号备注",
                    "required": False,
                },
                {
                    "name": "memory_profile_id",
                    "type": "text",
                    "label": "记忆关系人格（可选）",
                    "required": False,
                },
            ]
        )
        return fields

    def _create_person_declaration(self) -> dict[str, Any]:
        return {
            "id": "create_person",
            "label": "创建自然人",
            "confirm": "确定创建这个自然人并绑定首个账号？",
            "effect": "non_idempotent",
            "revision_required": True,
            "idempotency_required": True,
            "min_role": "admin",
            "timeout_seconds": 12,
            "payload_fields": [
                {
                    "name": "display_name",
                    "type": "text",
                    "label": "显示名称",
                    "required": True,
                },
                *self._account_payload_fields(include_person=False),
                {
                    "name": "relationship_profile_id",
                    "type": "text",
                    "label": "首绑关系 Profile ID（可选）",
                    "required": False,
                },
                {
                    "name": "initial_prior",
                    "type": "select",
                    "label": "初始关系档（可选）",
                    "required": False,
                    "options": [
                        ("", "不设置"),
                        ("neutral", "中性"),
                        ("acquainted", "相识"),
                        ("fond", "亲近"),
                    ],
                },
            ],
        }

    @staticmethod
    def _update_person_declaration() -> dict[str, Any]:
        return {
            "id": "update_person",
            "label": "重命名 / 更新自然人",
            "confirm": "确定更新该自然人的显示名称？",
            "effect": "idempotent",
            "revision_required": True,
            "idempotency_required": False,
            "min_role": "admin",
            "timeout_seconds": 10,
            "payload_fields": [
                {
                    "name": "person_ref",
                    "type": "text",
                    "label": "自然人引用 person_ref",
                    "required": True,
                },
                {
                    "name": "display_name",
                    "type": "text",
                    "label": "新的显示名称",
                    "required": True,
                },
            ],
        }

    def _merge_people_declaration(self) -> dict[str, Any]:
        return {
            "id": "merge_persons",
            "label": "合并自然人",
            "confirm": "来源自然人会被移除，账号与关系状态并入目标；确定继续？",
            "danger": True,
            "effect": "non_idempotent",
            "revision_required": True,
            "idempotency_required": True,
            "min_role": "owner",
            "timeout_seconds": 20,
            "payload_fields": [
                {
                    "name": "source_ref",
                    "type": "text",
                    "label": "来源 person_ref",
                    "required": True,
                },
                {
                    "name": "target_ref",
                    "type": "text",
                    "label": "目标 person_ref",
                    "required": True,
                },
            ],
        }

    def _delete_person_declaration(self) -> dict[str, Any]:
        return {
            "id": "delete_person",
            "label": "解除自然人归属",
            "confirm": "将解除自然人归属并由一个账号承接现有关系；确定继续？",
            "danger": True,
            "effect": "non_idempotent",
            "revision_required": True,
            "idempotency_required": True,
            "min_role": "owner",
            "timeout_seconds": 20,
            "payload_fields": [
                {
                    "name": "person_ref",
                    "type": "text",
                    "label": "自然人引用 person_ref",
                    "required": True,
                },
                {
                    "name": "restore_account_ref",
                    "type": "text",
                    "label": "承接账号 account_ref（多账号时必填）",
                    "required": False,
                },
            ],
        }

    @staticmethod
    def _bind_account_declaration() -> dict[str, Any]:
        return {
            "id": "bind_account",
            "label": "绑定账号",
            "confirm": "确定把该账号绑定到目标自然人？",
            "effect": "idempotent",
            "revision_required": True,
            "idempotency_required": False,
            "min_role": "admin",
            "timeout_seconds": 15,
            "payload_fields": RelationshipWebUIAdapter._account_payload_fields(
                include_person=True
            ),
        }

    @staticmethod
    def _unbind_account_declaration() -> dict[str, Any]:
        return {
            "id": "unbind_account",
            "label": "解绑账号",
            "confirm": "确定只解除该账号的自然人归属，不删除自然人？",
            "danger": True,
            "effect": "non_idempotent",
            "revision_required": True,
            "idempotency_required": True,
            "min_role": "owner",
            "timeout_seconds": 10,
            "payload_fields": [
                {
                    "name": "account_ref",
                    "type": "text",
                    "label": "账号引用 account_ref",
                    "required": True,
                }
            ],
        }

    @staticmethod
    def _migrate_account_declaration() -> dict[str, Any]:
        return {
            "id": "migrate_account",
            "label": "迁移账号归属",
            "confirm": "确定把该账号迁移到目标自然人？",
            "danger": True,
            "effect": "non_idempotent",
            "revision_required": True,
            "idempotency_required": True,
            "min_role": "owner",
            "timeout_seconds": 20,
            "payload_fields": [
                {
                    "name": "account_ref",
                    "type": "text",
                    "label": "账号引用 account_ref",
                    "required": True,
                },
                {
                    "name": "target_ref",
                    "type": "text",
                    "label": "目标自然人 person_ref",
                    "required": True,
                },
            ],
        }

    def _all_actions(self) -> dict[tuple[str, str], Mapping[str, Any]]:
        panels = self.contract()["panels"]
        actions: dict[tuple[str, str], Mapping[str, Any]] = {}
        for panel in panels:
            for action in panel.get("actions", []):
                actions[(str(panel["id"]), str(action["id"]))] = action
        return actions

    # -- opaque references ------------------------------------------------

    def _digest(self, namespace: str, value: str) -> str:
        secret = getattr(self.plugin, "_continuity_identity_secret", b"")
        return hmac.new(
            bytes(secret),
            f"{namespace}\x1f{value}".encode(),
            hashlib.sha256,
        ).hexdigest()[:20]

    def person_ref(self, person_id: str) -> str:
        return f"person_{self._digest('person', str(person_id or ''))}"

    def account_ref(self, account: PlatformAccount) -> str:
        material = (
            f"{account.platform_id.casefold()}\x1f"
            f"{account.user_id}\x1f{account.bot_id}"
        )
        return f"acct_{self._digest('account', material)}"

    def _resolve_person_ref(self, value: Any) -> PersonIdentity:
        ref = str(value or "").strip()
        if not ref:
            raise ValueError("PERSON_REF_REQUIRED")
        for raw in self.plugin.identity_registry.list_persons():
            person_id = str(raw.get("person_id") or "")
            if hmac.compare_digest(ref, self.person_ref(person_id)):
                person = self.plugin.identity_registry.get(person_id)
                if person is not None:
                    return person
        raise ValueError("UNKNOWN_PERSON_REF")

    def _resolve_account_ref(
        self, value: Any
    ) -> tuple[PersonIdentity, PlatformAccount]:
        ref = str(value or "").strip()
        if not ref:
            raise ValueError("ACCOUNT_REF_REQUIRED")
        for raw in self.plugin.identity_registry.list_persons():
            person = self.plugin.identity_registry.get(
                str(raw.get("person_id") or "")
            )
            if person is None:
                continue
            for account in person.accounts:
                if hmac.compare_digest(ref, self.account_ref(account)):
                    return person, account
        raise ValueError("UNKNOWN_ACCOUNT_REF")

    # -- panel data -------------------------------------------------------

    def panel_data(self, panel: str) -> dict[str, Any]:
        if panel == "overview":
            return self._overview_data()
        if panel == "identities":
            return self._identities_data()
        if panel == "accounts":
            return self._accounts_data()
        return {"success": False, "error": "UNKNOWN_PANEL"}

    def revision(self) -> str:
        states = {
            str(key): value.as_dict()
            for key, value in getattr(self.plugin.manager, "_states", {}).items()
        }
        material = {
            "persons": self.plugin.identity_registry.list_persons(),
            "states": states,
            "secret": bytes(
                getattr(self.plugin, "_continuity_identity_secret", b"")
            ).hex(),
        }
        encoded = json.dumps(
            material, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:20]

    def _overview_data(self) -> dict[str, Any]:
        payload = self.plugin._page_overview_unlocked()
        if isinstance(payload, dict) and payload.get("success") is False:
            return dict(payload)
        users = payload.get("users", [])
        columns = (
            {"key": "user_id", "label": "用户 / 人物"},
            {"key": "scope_kind", "label": "范围"},
            {"key": "display_name", "label": "昵称"},
            {"key": "affinity", "label": "好感"},
            {"key": "trust", "label": "信任"},
            {"key": "relationship_type", "label": "关系"},
            {"key": "band", "label": "区间"},
            {"key": "whitelisted", "label": "白名单"},
        )
        rows = [
            {
                "user_id": item.get("user_id", ""),
                "scope_kind": "人物" if item.get("scope_kind") == "person" else "账号",
                "display_name": item.get("display_name", ""),
                "affinity": item.get("affinity", 0),
                "trust": item.get("trust", 0),
                "relationship_type": RELATIONSHIP_TYPE_LABELS.get(
                    str(item.get("relationship_type", "friend")),
                    str(item.get("relationship_type", "friend")),
                ),
                "band": item.get("band", ""),
                "whitelisted": "是" if item.get("whitelisted") else "否",
            }
            for item in users[:200]
        ]
        return {
            "success": True,
            "title": "关系总览",
            "description": f"共 {len(users)} 条关系记录，展示前 {len(rows)} 条",
            "revision": self.revision(),
            "columns": columns,
            "rows": rows,
            "actions": [self._set_type_declaration()],
        }

    def _identities_data(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for raw in self.plugin.identity_registry.list_persons():
            person = self.plugin.identity_registry.get(
                str(raw.get("person_id") or "")
            )
            if person is None:
                continue
            profiles = sorted(
                self.plugin._known_relationship_profiles()
            )
            whitelisted = [
                profile_id
                for profile_id in profiles
                if self.plugin._is_relationship_whitelisted(
                    profile_id,
                    person.person_id,
                    person.account_user_ids,
                )
            ]
            rows.append(
                {
                    "person_ref": self.person_ref(person.person_id),
                    "display_name": person.display_name,
                    "account_count": len(person.accounts),
                    "account_refs": " ".join(
                        self.account_ref(account) for account in person.accounts
                    ),
                    "platforms": ", ".join(
                        sorted(
                            {
                                account.platform_id
                                for account in person.accounts
                                if account.platform_id
                            }
                        )
                    ),
                    "whitelist_profiles": ", ".join(whitelisted),
                }
            )
        return {
            "success": True,
            "title": "自然人管理",
            "description": f"共 {len(rows)} 个自然人；引用只在本进程内有效。",
            "revision": self.revision(),
            "columns": [
                {"key": "person_ref", "label": "person_ref"},
                {"key": "display_name", "label": "显示名称"},
                {"key": "account_count", "label": "账号数"},
                {"key": "account_refs", "label": "account_ref"},
                {"key": "platforms", "label": "平台"},
                {"key": "whitelist_profiles", "label": "白名单人格"},
            ],
            "rows": rows,
            "actions": [
                self._create_person_declaration(),
                self._update_person_declaration(),
                self._merge_people_declaration(),
                self._delete_person_declaration(),
            ],
        }

    def _accounts_data(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for raw in self.plugin.identity_registry.list_persons():
            person = self.plugin.identity_registry.get(
                str(raw.get("person_id") or "")
            )
            if person is None:
                continue
            for account in person.accounts:
                rows.append(
                    {
                        "account_ref": self.account_ref(account),
                        "person_ref": self.person_ref(person.person_id),
                        "display_name": person.display_name,
                        "platform": account.platform_id,
                        "label": account.label or "—",
                        "bot": "已设置" if account.bot_id else "未设置",
                        "session": "已设置" if account.session_id else "未设置",
                        "memory_profile": account.memory_profile_id or "默认",
                    }
                )
        return {
            "success": True,
            "title": "账号归属",
            "description": f"共 {len(rows)} 个账号；不返回 UID、Bot ID、会话或凭据。",
            "revision": self.revision(),
            "columns": [
                {"key": "account_ref", "label": "account_ref"},
                {"key": "person_ref", "label": "person_ref"},
                {"key": "display_name", "label": "自然人"},
                {"key": "platform", "label": "平台"},
                {"key": "label", "label": "备注"},
                {"key": "bot", "label": "Bot"},
                {"key": "session", "label": "会话"},
                {"key": "memory_profile", "label": "记忆人格"},
            ],
            "rows": rows,
            "actions": [
                self._bind_account_declaration(),
                self._unbind_account_declaration(),
                self._migrate_account_declaration(),
            ],
            "footer": "account_ref 为进程内 HMAC 引用，不暴露平台 UID；刷新后请重新复制。",
        }

    # -- actions ----------------------------------------------------------

    async def action(
        self,
        panel: str,
        action: str,
        payload: Mapping[str, Any] | None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        known = self._all_actions()
        declaration = known.get((str(panel or ""), str(action or "")))
        if declaration is None:
            if str(panel or "") not in {"overview", "identities", "accounts"}:
                raise ValueError("UNKNOWN_PANEL")
            raise ValueError("UNKNOWN_ACTION")
        if not isinstance(payload, Mapping):
            raise TypeError("INVALID_JSON_PAYLOAD")
        self._validate_context(declaration, context)
        data = dict(payload)
        if (panel, action) == ("overview", "set_type"):
            return await self._set_type(data)
        if (panel, action) == ("identities", "create_person"):
            return await self._create_person(data)
        if (panel, action) == ("identities", "update_person"):
            return await self._update_person(data)
        if (panel, action) == ("identities", "merge_persons"):
            return await self._merge_persons(data)
        if (panel, action) == ("identities", "delete_person"):
            return await self._delete_person(data)
        if (panel, action) == ("accounts", "bind_account"):
            return await self._bind_account(data)
        if (panel, action) == ("accounts", "unbind_account"):
            return await self._unbind_account(data)
        if (panel, action) == ("accounts", "migrate_account"):
            return await self._migrate_account(data)
        raise ValueError("UNKNOWN_ACTION")

    def _validate_context(
        self,
        declaration: Mapping[str, Any],
        context: Mapping[str, Any] | None,
    ) -> None:
        if not isinstance(context, Mapping):
            return
        actor = context.get("actor")
        actor = actor if isinstance(actor, Mapping) else {}
        role = str(context.get("role") or actor.get("role") or "").strip()
        required_role = str(declaration.get("min_role") or "admin")
        if role and ROLE_RANK.get(role, -1) < ROLE_RANK.get(required_role, 1):
            raise PermissionError("ROLE_FORBIDDEN")
        if bool(declaration.get("revision_required")):
            expected = context.get("expected_revision")
            if expected in (None, ""):
                raise ValueError("REVISION_REQUIRED")
            if str(expected) != self.revision():
                raise ValueError("REVISION_CONFLICT")
        if bool(declaration.get("idempotency_required")) and not str(
            context.get("request_id") or ""
        ).strip():
            raise ValueError("IDEMPOTENCY_REQUIRED")

    @staticmethod
    def _unwrap(result: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(result)
        payload.pop("_status", None)
        if not payload.get("success"):
            raise ValueError(str(payload.get("error") or "ACTION_FAILED"))
        return payload

    async def _set_type(self, data: dict[str, Any]) -> dict[str, Any]:
        scope_kind = str(data.get("scope_kind") or "").strip()
        raw_type = str(data.get("relationship_type") or "").strip().lower()
        relationship_type = RELATIONSHIP_TYPE_ALIASES.get(raw_type, "")
        if not relationship_type:
            raise ValueError("INVALID_RELATIONSHIP_TYPE")
        profile_id = validate_profile_id(
            str(data.get("relationship_profile_id") or "default").strip()
        )
        if scope_kind == "person":
            person_id = str(data.get("user_id") or "").strip()
            if not person_id:
                raise ValueError("PERSON_ID_REQUIRED")
            scope = RelationshipScope(
                bot_id="",
                user_id="",
                person_id=person_id,
                relationship_profile_id=profile_id,
            )
        elif scope_kind == "account":
            bot_id = str(data.get("bot_id") or "").strip()
            user_id = str(data.get("user_id") or "").strip()
            if not bot_id or not user_id:
                raise ValueError("ACCOUNT_SCOPE_REQUIRED")
            scope = RelationshipScope(
                bot_id=bot_id,
                user_id=user_id,
                relationship_profile_id=profile_id,
            )
        else:
            raise ValueError("INVALID_SCOPE_KIND")
        async with self.plugin._identity_write_lock:
            blocked = self.plugin._identity_mutation_blocked_payload()
            if blocked is not None:
                raise ValueError(str(blocked.get("error") or "MUTATION_BLOCKED"))
            await self.plugin.manager.set_relationship_type(
                scope, relationship_type
            )
        return {
            "success": True,
            "message": f"已设置为 {RELATIONSHIP_TYPE_LABELS.get(relationship_type, relationship_type)}",
            "relationship_type": relationship_type,
        }

    @staticmethod
    def _account_payload(data: Mapping[str, Any]) -> dict[str, str]:
        return {
            "platform_id": str(data.get("platform_id") or "").strip(),
            "user_id": str(data.get("user_id") or "").strip(),
            "bot_id": str(data.get("bot_id") or "").strip(),
            "session_id": str(data.get("session_id") or "").strip(),
            "label": str(data.get("label") or "").strip(),
            "memory_profile_id": str(
                data.get("memory_profile_id") or ""
            ).strip(),
        }

    async def _create_person(self, data: dict[str, Any]) -> dict[str, Any]:
        display_name = str(data.get("display_name") or "").strip()
        if not display_name:
            raise ValueError("DISPLAY_NAME_REQUIRED")
        request = {
            "display_name": display_name,
            "accounts": [self._account_payload(data)],
        }
        for key in (
            "person_id",
            "relationship_profile_id",
            "initial_prior",
        ):
            value = str(data.get(key) or "").strip()
            if value:
                request[key] = value
        result = self._unwrap(
            await self.plugin._save_identity_action(request)
        )
        person = result.get("person") or {}
        return {
            "success": True,
            "message": "自然人已创建并绑定首个账号",
            "person_ref": self.person_ref(str(person.get("person_id") or "")),
            "state_merged": bool(result.get("state_merged")),
        }

    async def _update_person(self, data: dict[str, Any]) -> dict[str, Any]:
        person = self._resolve_person_ref(data.get("person_ref"))
        display_name = str(data.get("display_name") or "").strip()
        if not display_name:
            raise ValueError("DISPLAY_NAME_REQUIRED")
        request = person.as_dict()
        request["display_name"] = display_name
        result = self._unwrap(
            await self.plugin._save_identity_action(request)
        )
        return {
            "success": True,
            "message": "自然人已更新",
            "person_ref": self.person_ref(person.person_id),
            "state_merged": bool(result.get("state_merged")),
        }

    async def _bind_account(self, data: dict[str, Any]) -> dict[str, Any]:
        person = self._resolve_person_ref(data.get("person_ref"))
        account = self._account_payload(data)
        result = self._unwrap(
            await self.plugin._merge_identity_service(
                {"target_person_id": person.person_id, "account": account}
            )
        )
        return {
            "success": True,
            "message": "账号已绑定到该自然人",
            "person_ref": self.person_ref(person.person_id),
            "identity_changed": bool(result.get("identity_changed")),
            "state_merged": bool(result.get("state_merged")),
        }

    async def _merge_persons(self, data: dict[str, Any]) -> dict[str, Any]:
        source = self._resolve_person_ref(data.get("source_ref"))
        target = self._resolve_person_ref(data.get("target_ref"))
        if source.person_id == target.person_id:
            raise ValueError("SAME_PERSON_IDENTITY")
        result = self._unwrap(
            await self.plugin._merge_identity_service(
                {
                    "source_person_id": source.person_id,
                    "target_person_id": target.person_id,
                }
            )
        )
        return {
            "success": True,
            "message": "自然人已合并",
            "target_ref": self.person_ref(target.person_id),
            "state_merged": bool(result.get("state_merged")),
        }

    async def _delete_person(self, data: dict[str, Any]) -> dict[str, Any]:
        person = self._resolve_person_ref(data.get("person_ref"))
        restore_account = None
        restore_ref = str(data.get("restore_account_ref") or "").strip()
        if restore_ref:
            owner, account = self._resolve_account_ref(restore_ref)
            if owner.person_id != person.person_id:
                raise ValueError("RESTORE_ACCOUNT_NOT_BOUND")
            restore_account = {
                "platform_id": account.platform_id,
                "user_id": account.user_id,
            }
        elif len(person.accounts) != 1:
            raise ValueError("RESTORE_ACCOUNT_REQUIRED")
        request: dict[str, Any] = {"person_id": person.person_id}
        if restore_account is not None:
            request["restore_account"] = restore_account
        result = self._unwrap(
            await self.plugin._delete_identity_service(request)
        )
        return {
            "success": True,
            "message": "自然人归属已解除",
            "state_migrated": bool(result.get("state_migrated")),
        }

    async def _unbind_account(self, data: dict[str, Any]) -> dict[str, Any]:
        person, account = self._resolve_account_ref(data.get("account_ref"))
        self._unwrap(
            await self.plugin._remove_account_service(
                person.person_id,
                account.platform_id,
                account.user_id,
            )
        )
        return {
            "success": True,
            "message": "账号已解绑；自然人保留",
            "person_ref": self.person_ref(person.person_id),
            "removed_account_ref": self.account_ref(account),
        }

    async def _migrate_account(self, data: dict[str, Any]) -> dict[str, Any]:
        source, account = self._resolve_account_ref(data.get("account_ref"))
        target = self._resolve_person_ref(data.get("target_ref"))
        if source.person_id == target.person_id:
            raise ValueError("SAME_PERSON_IDENTITY")
        if len(source.accounts) == 1:
            result = self._unwrap(
                await self.plugin._merge_identity_service(
                    {
                        "source_person_id": source.person_id,
                        "target_person_id": target.person_id,
                    }
                )
            )
            return {
                "success": True,
                "message": "单账号自然人已整体合并到目标，关系状态随归属迁移",
                "mode": "person_merge",
                "target_ref": self.person_ref(target.person_id),
                "state_merged": bool(result.get("state_merged")),
            }
        result = self._unwrap(
            await self.plugin._migrate_account_service(
                source.person_id,
                target.person_id,
                account.platform_id,
                account.user_id,
            )
        )
        return {
            "success": True,
            "message": (
                "账号已迁移；来源自然人保留，其既有聚合关系仍归属来源自然人"
            ),
            "mode": "account_move",
            "source_ref": self.person_ref(source.person_id),
            "target_ref": self.person_ref(target.person_id),
            "state_merged": bool(result.get("state_merged")),
        }
