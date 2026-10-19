"""解禁内部事件不产生关系互动；沿用入口测试的 AstrBot 替身。"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import test_main_entry as entry


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setitem(entry._DATA_DIR, "path", str(tmp_path))
    monkeypatch.setattr(entry.main.RelationshipPlugin, "_current_instance", None)
    return entry.main.RelationshipPlugin(
        entry.FakeContext(), {"SAVE_INTERVAL_SECONDS": 0}
    )


def recovery_artifact(**changes):
    artifact = {
        "version": "1.0",
        "kind": "mute_recovery",
        "is_user_message": False,
        "episode_id": "episode-1",
        "platform_id": "qq-main",
        "bot_id": "bot-1",
        "group_id": "group-1",
    }
    artifact.update(changes)
    return artifact


def attach_artifact(event, artifact, owner=entry.main.OWNER_CONVERSATION_FLOW):
    context = entry.main.ensure_context(event, entry.main.PHASE_LLM_REQUEST)
    entry.main.set_artifact(context, owner, "mute_recovery", artifact)
    return context


@pytest.mark.parametrize("sender_id", ["bot-1", "operator-1"])
@pytest.mark.parametrize("addressed_user_id", [None, "bound-user", "unbound-user"])
def test_internal_recovery_preserves_existing_relationships_and_files(
    plugin, monkeypatch, sender_id, addressed_user_id
):
    plugin.identity_registry.upsert(
        {
            "person_id": "bound-person",
            "display_name": "已绑定用户",
            "accounts": [
                {
                    "platform_id": "qq-main",
                    "bot_id": "bot-1",
                    "user_id": "bound-user",
                }
            ],
        }
    )
    for user_id in ("bound-user", "operator-1"):
        entry._run(
            plugin.on_llm_request(
                entry.FakeEvent(
                    sender_id=user_id,
                    group_id="group-1",
                    message_id=f"real-{user_id}",
                ),
                object(),
            )
        )

    states_before = deepcopy(plugin.manager._states)
    events_before = deepcopy(plugin.manager._events)
    observations_before = deepcopy(plugin.account_observations._items)
    registry_before = plugin.identity_registry.snapshot()
    files_before = {
        path.name: path.read_bytes()
        for path in plugin._data_dir.iterdir()
        if path.is_file()
    }
    record_interaction = AsyncMock()
    record_manager = AsyncMock()
    observe_account = Mock()
    write_registry = Mock()
    monkeypatch.setattr(plugin, "_record_platform_interaction", record_interaction)
    monkeypatch.setattr(plugin.manager, "record", record_manager)
    monkeypatch.setattr(plugin.account_observations, "record", observe_account)
    monkeypatch.setattr(plugin.identity_registry, "_write", write_registry)

    event = entry.FakeEvent(
        sender_id=sender_id,
        group_id="group-1",
        text="内部恢复接话背景：操作者 operator-1 刚刚解除了禁言",
        message_id="recovery-1",
    )
    artifact = recovery_artifact()
    if addressed_user_id is not None:
        artifact["addressed_user_id"] = addressed_user_id
    context = attach_artifact(event, artifact)
    flow_before = deepcopy(context["artifacts"][entry.main.OWNER_CONVERSATION_FLOW])
    req = SimpleNamespace(extra_user_content_parts=[], system_prompt="原有人设")
    request_before = deepcopy(vars(req))
    plugin._cross_platform_memory_enabled = True

    entry._run(plugin.on_llm_request(event, req))
    entry._run(plugin.on_cross_platform_memory(event, req))

    record_interaction.assert_not_called()
    record_manager.assert_not_called()
    observe_account.assert_not_called()
    write_registry.assert_not_called()
    assert plugin.manager._states == states_before
    assert plugin.manager._events == events_before
    assert plugin.account_observations._items == observations_before
    assert plugin.identity_registry.snapshot() == registry_before
    assert {
        path.name: path.read_bytes()
        for path in plugin._data_dir.iterdir()
        if path.is_file()
    } == files_before
    assert vars(req) == request_before
    assert entry.main.OWNER_RELATIONSHIP not in context["artifacts"]
    assert entry.main.OWNER_RELATIONSHIP not in context["flags"]
    assert context["artifacts"][entry.main.OWNER_CONVERSATION_FLOW] == flow_before
    assert context["diagnostics"][entry.main.OWNER_RELATIONSHIP]["reasons"] == [
        "MUTE_RECOVERY_RELATIONSHIP_SKIPPED"
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"version": "2.0"},
        {"version": 1.0},
        {"kind": "group_ban"},
        {"episode_id": None},
        {"episode_id": 1},
        {"episode_id": " "},
        {"platform_id": "qq-other"},
        {"bot_id": "other-bot"},
        {"group_id": "other-group"},
        {"group_id": None},
        {"group_id": ""},
        {"group_id": 123},
    ],
)
def test_invalid_internal_marker_never_falls_back_to_recording(
    plugin, monkeypatch, changes
):
    event = entry.FakeEvent(sender_id="operator-1", group_id="group-1")
    context = attach_artifact(event, recovery_artifact(**changes))
    record_interaction = AsyncMock()
    monkeypatch.setattr(plugin, "_record_platform_interaction", record_interaction)
    req = SimpleNamespace(extra_user_content_parts=[], system_prompt="原有人设")

    entry._run(plugin.on_llm_request(event, req))

    record_interaction.assert_not_called()
    assert plugin.manager._states == {}
    assert plugin.manager._events == []
    assert plugin.account_observations._items == {}
    assert plugin.identity_registry.snapshot() == {}
    assert list(plugin._data_dir.iterdir()) == []
    assert vars(req) == {"extra_user_content_parts": [], "system_prompt": "原有人设"}
    assert context["diagnostics"][entry.main.OWNER_RELATIONSHIP]["reasons"] == [
        "MUTE_RECOVERY_CONTEXT_INVALID"
    ]


def test_internal_marker_requires_exact_platform_instance(plugin):
    event = entry.FakeEvent(group_id="group-1", platform_id="qq-other")
    event.get_platform_name = lambda: "qq-main"
    context = attach_artifact(event, recovery_artifact())

    entry._run(plugin.on_llm_request(event, object()))

    assert plugin.manager._states == {}
    assert plugin.account_observations._items == {}
    assert context["diagnostics"][entry.main.OWNER_RELATIONSHIP]["reasons"] == [
        "MUTE_RECOVERY_CONTEXT_INVALID"
    ]


def test_internal_group_marker_is_invalid_in_private_chat(plugin):
    event = entry.FakeEvent(sender_id="operator-1")
    context = attach_artifact(event, recovery_artifact())

    entry._run(plugin.on_llm_request(event, object()))

    assert plugin.manager._states == {}
    assert plugin.account_observations._items == {}
    assert context["diagnostics"][entry.main.OWNER_RELATIONSHIP]["reasons"] == [
        "MUTE_RECOVERY_CONTEXT_INVALID"
    ]


@pytest.mark.parametrize("group_id", [None, "group-1"])
@pytest.mark.parametrize("marker", ["absent", "background", True, None, 0, "false"])
def test_real_messages_keep_recording_with_optional_recovery_background(
    plugin, group_id, marker
):
    event = entry.FakeEvent(group_id=group_id, text="谢谢你，继续刚才的话题吧")
    context = entry.main.ensure_context(event, entry.main.PHASE_LLM_REQUEST)
    if marker != "absent":
        artifact = recovery_artifact()
        artifact.pop("is_user_message")
        if marker != "background":
            artifact["is_user_message"] = marker
        context = attach_artifact(event, artifact)
    req = SimpleNamespace(extra_user_content_parts=[], system_prompt="原有人设")

    entry._run(plugin.on_llm_request(event, req))

    state = plugin.manager._states["persona:default:account:bot-1:user:user-1"]
    assert state.interaction_count == 1
    assert len(plugin.manager._events) == 1
    assert plugin.account_observations.get("bot-1", "user-1")["platform_id"] == (
        "qq-main"
    )
    assert plugin.identity_registry.snapshot() == {}
    assert context["flags"][entry.main.OWNER_RELATIONSHIP]["snapshot_ready"] is True
    assert context["artifacts"][entry.main.OWNER_RELATIONSHIP]["snapshot"]
    assert req.extra_user_content_parts


def test_internal_marker_under_other_owner_does_not_suppress_real_message(plugin):
    event = entry.FakeEvent(group_id="group-1")
    attach_artifact(event, recovery_artifact(), owner=entry.main.OWNER_RELATIONSHIP)

    entry._run(plugin.on_llm_request(event, object()))

    state = plugin.manager._states["persona:default:account:bot-1:user:user-1"]
    assert state.interaction_count == 1
    assert plugin.account_observations.get("bot-1", "user-1") is not None
