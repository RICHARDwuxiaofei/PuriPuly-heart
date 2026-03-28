from __future__ import annotations

from puripuly_heart.config.settings import from_dict, to_dict


def test_overlay_peer_translation_and_integrated_context_settings_round_trip() -> None:
    settings = from_dict(
        {
            "ui": {
                "overlay_enabled": True,
                "peer_translation_enabled": True,
                "integrated_context_enabled": True,
                "integrated_context_bootstrapped": True,
            },
            "desktop_audio": {
                "output_device": "Headphones (Loopback)",
                "vad_speech_threshold": 0.7,
                "vad_hangover_ms": 950,
                "vad_pre_roll_ms": 450,
            },
        }
    )

    assert settings.ui.overlay_enabled is True
    assert settings.ui.peer_translation_enabled is True
    assert settings.ui.integrated_context_enabled is True
    assert settings.desktop_audio.output_device == "Headphones (Loopback)"

    data = to_dict(settings)

    assert data["ui"]["integrated_context_bootstrapped"] is True
    assert data["desktop_audio"]["vad_hangover_ms"] == 950


def test_desktop_audio_settings_round_trip_with_defaults() -> None:
    settings = from_dict({})

    assert settings.desktop_audio.output_device == ""
    assert settings.desktop_audio.vad_speech_threshold == 0.65
    assert settings.desktop_audio.vad_hangover_ms == 900
    assert settings.desktop_audio.vad_pre_roll_ms == 500


def test_desktop_audio_output_device_null_defaults_to_empty_string() -> None:
    settings = from_dict({"desktop_audio": {"output_device": None}})

    assert settings.desktop_audio.output_device == ""
