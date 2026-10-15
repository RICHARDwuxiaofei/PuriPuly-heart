from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from puripuly_heart.app.ports.settings_secrets import (
    SettingsSecretLoadResult,
    SettingsSecretValuesSnapshot,
)
from puripuly_heart.config.wrist_vision import WristVisionIntent
from puripuly_heart.core.vision.translation import VisionError, VisionTranslationService
from tests.app.test_ui_application_boundary import RecordingBackend, UiApplicationBoundary


@pytest.mark.asyncio
async def test_vision_reuses_secret_port_and_effective_language_without_touching_text_pipeline(
    monkeypatch,
):
    backend = RecordingBackend()
    backend.settings = replace(
        backend.settings,
        intent=replace(backend.settings.intent, wrist_vision=WristVisionIntent(enabled=True)),
    )
    app = UiApplicationBoundary(backend)
    reads = []

    def load(keys):
        reads.append(keys)
        return SettingsSecretLoadResult(
            snapshot=SettingsSecretValuesSnapshot(
                values=(("deepseek_api_key", "fake-private-test-key"),)
            )
        )

    app._settings_secrets = SimpleNamespace(load_values=load)
    app._input_runtime = SimpleNamespace(quick_window_context=lambda: dict(target_language="ja"))
    translate = AsyncMock(return_value=[])
    monkeypatch.setattr(VisionTranslationService, "translate", translate)
    await app.translate_wrist_image(b"mock-image")
    assert reads == [("deepseek_api_key",)]
    assert translate.await_args.kwargs["key"] == "fake-private-test-key"
    assert translate.await_args.kwargs["target_language"] == "ja"
    assert not any(event[0] == "submit" for event in backend.events)


@pytest.mark.asyncio
async def test_disabled_vision_cannot_read_credentials_or_upload(monkeypatch):
    app = UiApplicationBoundary(RecordingBackend())
    reads = []
    app._settings_secrets = SimpleNamespace(load_values=lambda keys: reads.append(keys))
    translate = AsyncMock()
    monkeypatch.setattr(VisionTranslationService, "translate", translate)
    with pytest.raises(VisionError, match="vision_disabled"):
        await app.translate_wrist_image(b"mock-image")
    assert not reads
    translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_spatial_without_fallback_does_not_spend(monkeypatch):
    backend = RecordingBackend()
    backend.settings = replace(
        backend.settings,
        intent=replace(
            backend.settings.intent,
            wrist_vision=WristVisionIntent(
                enabled=True, spatial_enabled=True, fallback_to_wrist=False
            ),
        ),
    )
    app = UiApplicationBoundary(backend)
    translate = AsyncMock()
    monkeypatch.setattr(VisionTranslationService, "translate", translate)
    with pytest.raises(VisionError, match="spatial_not_available"):
        await app.translate_wrist_image(b"mock-image")
    translate.assert_not_awaited()
