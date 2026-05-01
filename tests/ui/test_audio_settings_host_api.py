from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.audio_host_api import (
    WINDOWS_DIRECTSOUND_HOST_API,
    WINDOWS_WASAPI_COMPATIBILITY_HOST_API,
    WINDOWS_WASAPI_HOST_API,
)
from puripuly_heart.ui.components.settings.audio_settings import AudioSettings
from puripuly_heart.ui.i18n import get_locale, set_locale, t


def _fake_sounddevice(monkeypatch: pytest.MonkeyPatch, *, hostapis, devices=()) -> None:
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            query_hostapis=lambda: hostapis,
            query_devices=lambda: devices,
        ),
    )


def test_host_api_options_include_wasapi_compatibility_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_locale = get_locale()
    try:
        set_locale("ko")
        _fake_sounddevice(
            monkeypatch,
            hostapis=[
                {"name": "MME"},
                {"name": WINDOWS_WASAPI_HOST_API},
                {"name": WINDOWS_DIRECTSOUND_HOST_API},
            ],
        )

        settings = AudioSettings()

        options = settings._get_host_api_options()

        assert [option.value for option in options] == [
            "",
            WINDOWS_WASAPI_HOST_API,
            WINDOWS_WASAPI_COMPATIBILITY_HOST_API,
            WINDOWS_DIRECTSOUND_HOST_API,
        ]
        compatibility_label = options[2].label
        assert compatibility_label == t(
            "settings.audio_host_api.option.windows_wasapi_compatibility"
        )
        assert compatibility_label != WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    finally:
        set_locale(old_locale)


def test_compatibility_mode_enumerates_wasapi_microphones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_sounddevice(
        monkeypatch,
        hostapis=[
            {"name": WINDOWS_WASAPI_HOST_API},
            {"name": WINDOWS_DIRECTSOUND_HOST_API},
        ],
        devices=[
            {"name": "WASAPI Mic", "hostapi": 0, "max_input_channels": 1},
            {"name": "DirectSound Mic", "hostapi": 1, "max_input_channels": 1},
            {"name": "WASAPI Output", "hostapi": 0, "max_input_channels": 0},
        ],
    )
    settings = AudioSettings()
    settings.host_api = WINDOWS_WASAPI_COMPATIBILITY_HOST_API

    options = settings._get_microphone_options()

    assert [option.value for option in options] == ["", "WASAPI Mic"]


def test_host_api_selection_resets_selected_microphone() -> None:
    settings = AudioSettings()
    settings.host_api = WINDOWS_WASAPI_HOST_API
    settings.microphone = "Previous Mic"

    settings._on_host_api_selected(WINDOWS_WASAPI_COMPATIBILITY_HOST_API)

    assert settings.host_api == WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    assert settings.microphone == ""
