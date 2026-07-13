from __future__ import annotations

import inspect
import re
from types import SimpleNamespace

import pytest

from puripuly_heart.app.ports.dashboard_application import (
    DashboardApplicationPort,
    DashboardCommandStatus,
    SetOverlayEnabled,
    SetPeerEulaAccepted,
)
from puripuly_heart.app.services.dashboard_application import DashboardApplication


def test_dashboard_application_is_dependency_injected_not_controller_shaped() -> None:
    parameters = inspect.signature(DashboardApplication).parameters

    assert "controller" not in parameters
    assert set(parameters) == {
        "runtime_host",
        "ui_settings",
        "runtime_logging",
        "managed_authentication",
        "github_star_prompt",
        "telemetry_owner",
        "overlay_commands",
        "overlay_state",
        "canonical_settings",
        "debug_audio_faults_enabled",
    }


def test_dashboard_port_exposes_only_typed_commands_and_results() -> None:
    source = inspect.getsource(DashboardApplicationPort)

    for forbidden in ("Any", "AppSettings", "SecretStore", "config_path", "controller", "hub"):
        assert re.search(rf"\b{forbidden}\b", source) is None


@pytest.mark.asyncio
async def test_overlay_enable_disable_and_retry_use_typed_owner_and_resnapshot() -> None:
    lifecycle = SimpleNamespace(state=SimpleNamespace(value="stopped"), failure_reason=None)
    configured: list[tuple[object, bool, str, str]] = []
    enabled: list[bool] = []

    class Commands:
        def configure_intent(
            self, settings, *, enabled, runtime_logging_mode, interaction_mode
        ):  # noqa: ANN001, ANN201
            configured.append((settings, enabled, runtime_logging_mode, interaction_mode))

        async def set_enabled(self, value: bool) -> None:
            enabled.append(value)
            if len(enabled) == 1:
                lifecycle.state.value = "failed"
                lifecycle.failure_reason = "startup_failed"
            else:
                lifecycle.state.value = "connected" if value else "stopped"
                lifecycle.failure_reason = None

    class State:
        def runtime_snapshot(self):  # noqa: ANN201
            return SimpleNamespace(interaction_mode="locked")

        def lifecycle_snapshot(self):  # noqa: ANN201
            return lifecycle

    dashboard = object.__new__(DashboardApplication)
    dashboard._overlay_commands = Commands()
    dashboard._overlay_state = State()
    dashboard._runtime_logging = SimpleNamespace(mode="basic")
    intent = object()

    configured_receipt = SimpleNamespace(envelope=intent, revision="configured")

    async def current_settings():  # noqa: ANN202
        return configured_receipt

    snapshots: list[object] = []

    async def snapshot():  # noqa: ANN202
        value = object()
        snapshots.append(value)
        return value

    dashboard._canonical_settings = current_settings
    dashboard.snapshot = snapshot

    failed = await dashboard.set_overlay_enabled(SetOverlayEnabled(True))
    retried = await dashboard.set_overlay_enabled(SetOverlayEnabled(True))
    disabled = await dashboard.set_overlay_enabled(SetOverlayEnabled(False))

    assert failed.status == DashboardCommandStatus.FAILED
    assert failed.detail_code == "startup_failed"
    assert retried.status == DashboardCommandStatus.APPLIED
    assert disabled.status == DashboardCommandStatus.APPLIED
    assert enabled == [True, True, False]
    assert configured == [
        (intent, True, "basic", "locked"),
        (intent, True, "basic", "locked"),
        (intent, False, "basic", "locked"),
    ]
    assert [failed.snapshot, retried.snapshot, disabled.snapshot] == snapshots
    assert failed.receipt is configured_receipt
    assert retried.receipt is configured_receipt
    assert disabled.receipt is configured_receipt


@pytest.mark.asyncio
async def test_eula_result_preserves_command_receipt_across_later_commit() -> None:
    exact_receipt = object()

    class UiSettings:
        async def set_peer_translation_eula(
            self, accepted, expected_revision
        ):  # noqa: ANN001, ANN201
            assert accepted is True
            assert expected_revision == "before"
            return SimpleNamespace(status="applied", receipt=exact_receipt)

    dashboard = object.__new__(DashboardApplication)
    dashboard._ui_settings = UiSettings()

    async def later_receipt():  # noqa: ANN202
        raise AssertionError("must not reload a later canonical receipt")

    snapshot = object()

    async def authoritative_snapshot():  # noqa: ANN202
        return snapshot

    dashboard._canonical_settings = later_receipt
    dashboard.snapshot = authoritative_snapshot

    result = await dashboard.set_peer_eula(SetPeerEulaAccepted(True, "before"))

    assert result.status == DashboardCommandStatus.APPLIED
    assert result.receipt is exact_receipt
    assert result.snapshot is snapshot
