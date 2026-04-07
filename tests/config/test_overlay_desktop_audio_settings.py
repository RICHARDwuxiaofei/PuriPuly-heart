from __future__ import annotations

import json

from puripuly_heart.config.settings import from_dict, to_dict


def test_overlay_enabled_is_session_only_while_other_overlay_preferences_round_trip() -> None:
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

    assert settings.ui.overlay_enabled is False
    assert settings.ui.peer_translation_enabled is True
    assert settings.ui.integrated_context_enabled is True
    assert settings.desktop_audio.output_device == "Headphones (Loopback)"

    settings.ui.overlay_enabled = True
    data = to_dict(settings)

    assert "overlay_enabled" not in data["ui"]
    assert data["ui"]["integrated_context_bootstrapped"] is True
    assert data["desktop_audio"]["vad_hangover_ms"] == 950


def test_desktop_audio_settings_round_trip_with_defaults() -> None:
    settings = from_dict({})

    assert settings.desktop_audio.output_device == ""
    assert settings.desktop_audio.vad_speech_threshold == 0.6
    assert settings.desktop_audio.vad_hangover_ms == 600
    assert settings.desktop_audio.vad_pre_roll_ms == 500


def test_desktop_audio_output_device_null_defaults_to_empty_string() -> None:
    settings = from_dict({"desktop_audio": {"output_device": None}})

    assert settings.desktop_audio.output_device == ""


def test_overlay_calibration_round_trips_with_defaults() -> None:
    settings = from_dict(
        {
            "overlay_calibration": {
                "anchor": "head_locked",
                "offset_x": 0.15,
                "offset_y": -0.2,
                "distance": 1.2,
                "text_scale": 1.1,
                "background_alpha": 0.4,
            }
        }
    )

    assert settings.overlay_calibration.anchor == "head_locked"
    assert settings.overlay_calibration.distance == 1.2

    data = to_dict(settings)

    assert data["overlay_calibration"]["offset_x"] == 0.15
    assert data["overlay_calibration"]["background_alpha"] == 0.4


def test_load_settings_drops_legacy_overlay_enabled_flag(tmp_path) -> None:
    from puripuly_heart.config.settings import load_settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "ui": {
                    "locale": "ko",
                    "overlay_enabled": True,
                    "peer_translation_enabled": True,
                }
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(path)
    reloaded = json.loads(path.read_text(encoding="utf-8"))

    assert settings.ui.overlay_enabled is False
    assert settings.ui.peer_translation_enabled is True
    assert "overlay_enabled" not in reloaded["ui"]


def test_load_settings_forces_desktop_vad_threshold_to_v3_default(tmp_path) -> None:
    from puripuly_heart.config.settings import SETTINGS_SCHEMA_VERSION, load_settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "settings_version": 2,
                "desktop_audio": {
                    "vad_speech_threshold": 0.72,
                },
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(path)
    reloaded = json.loads(path.read_text(encoding="utf-8"))

    assert settings.settings_version == SETTINGS_SCHEMA_VERSION
    assert settings.desktop_audio.vad_speech_threshold == 0.6
    assert reloaded["settings_version"] == SETTINGS_SCHEMA_VERSION
    assert reloaded["desktop_audio"]["vad_speech_threshold"] == 0.6


def test_load_settings_migrates_legacy_desktop_vad_hangover_to_new_default(tmp_path) -> None:
    from puripuly_heart.config.settings import SETTINGS_SCHEMA_VERSION, load_settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "settings_version": 6,
                "desktop_audio": {
                    "vad_hangover_ms": 900,
                },
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(path)
    reloaded = json.loads(path.read_text(encoding="utf-8"))

    assert settings.settings_version == SETTINGS_SCHEMA_VERSION
    assert settings.desktop_audio.vad_hangover_ms == 600
    assert reloaded["settings_version"] == SETTINGS_SCHEMA_VERSION
    assert reloaded["desktop_audio"]["vad_hangover_ms"] == 600


def test_load_settings_migrates_desktop_vad_hangover_700_to_600(tmp_path) -> None:
    from puripuly_heart.config.settings import SETTINGS_SCHEMA_VERSION, load_settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "settings_version": 7,
                "desktop_audio": {
                    "vad_hangover_ms": 700,
                },
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(path)
    reloaded = json.loads(path.read_text(encoding="utf-8"))

    assert settings.settings_version == SETTINGS_SCHEMA_VERSION
    assert settings.desktop_audio.vad_hangover_ms == 600
    assert reloaded["settings_version"] == SETTINGS_SCHEMA_VERSION
    assert reloaded["desktop_audio"]["vad_hangover_ms"] == 600
