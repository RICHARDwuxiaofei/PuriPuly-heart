from __future__ import annotations

import pytest

from puripuly_heart.app.ports.application_settings import SettingChange, SettingsField
from puripuly_heart.app.ports.ui_settings import (
    InteractionStatus,
    ManagedAction,
    ManagedActionStatus,
    MicrophoneTestStatus,
    OverlayAction,
    PkceStartRequest,
    UiSettingsDelta,
)
from puripuly_heart.ui.debug_settings import InertDebugUiSettingsApplication


@pytest.mark.asyncio
async def test_debug_application_is_deterministic_and_never_commits() -> None:
    application = InertDebugUiSettingsApplication()
    before = await application.snapshot()
    result = await application.apply(
        UiSettingsDelta(
            before.canonical_revision,
            (SettingChange(SettingsField.UI_LOCALE, "ja"),),
        )
    )
    after = await application.snapshot()
    assert result.snapshot is before
    assert after is before
    assert after.canonical_revision == "debug-0"
    capture = await application.select_capture_target("process:vrchat", "debug-0")
    assert capture.snapshot is before


@pytest.mark.asyncio
async def test_debug_interactions_reject_every_external_or_mutating_operation() -> None:
    interactions = InertDebugUiSettingsApplication().interactions
    with pytest.raises(RuntimeError, match="mutation unavailable"):
        await interactions.set_secret("openrouter_api_key", "secret")
    with pytest.raises(RuntimeError, match="mutation unavailable"):
        await interactions.clear_secret("openrouter_api_key")
    with pytest.raises(RuntimeError, match="access unavailable"):
        await interactions.secret_metadata("openrouter_api_key")
    assert (await interactions.query_audio_devices()).status == InteractionStatus.UNAVAILABLE
    assert (await interactions.capture_targets()).options == ()
    assert (await interactions.verify_provider("openrouter", "key")).status == "unavailable"
    assert (
        await interactions.start_pkce(PkceStartRequest("alias", "model", "debug-0"))
    ).status == InteractionStatus.UNAVAILABLE
    assert (
        await interactions.set_telemetry_consent("allow")
    ).status == InteractionStatus.UNAVAILABLE
    assert (
        await interactions.overlay_action(OverlayAction.START)
    ).status == InteractionStatus.UNAVAILABLE
    assert (await interactions.microphone_test(True)).status == MicrophoneTestStatus.UNAVAILABLE
    assert (await interactions.managed_action(ManagedAction.CONNECT)).status == (
        ManagedActionStatus.UNAVAILABLE
    )
