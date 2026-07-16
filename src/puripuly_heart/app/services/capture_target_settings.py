from __future__ import annotations

import contextlib
import os
from dataclasses import replace
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from puripuly_heart.config.capture_target_resolution import resolve_desktop_audio_capture_target
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.config.settings_vnext.facade import (
    load_settings_with_result,
    load_vnext_settings,
    save_vnext_settings,
)
from puripuly_heart.config.settings_vnext.migration import from_legacy_app_settings
from puripuly_heart.config.settings_vnext.schema import CaptureTargetIntent, with_capture_target


class CaptureTargetSettingsError(RuntimeError):
    def __init__(self, status: object) -> None:
        self.status = _status_value(status)
        super().__init__(f"capture_target_settings_{self.status}")


def persist_desktop_audio_capture_target(
    path: Path,
    settings: AppSettings,
    capture_target: CaptureTargetIntent,
) -> AppSettings:
    original_bytes = _read_source_bytes(path)
    if original_bytes is None:
        try:
            settings.validate()
            vnext = from_legacy_app_settings(settings)
        except Exception:
            raise CaptureTargetSettingsError("migration_failed") from None
    else:
        try:
            loaded = load_vnext_settings(path)
        except Exception:
            raise CaptureTargetSettingsError("load_failed") from None
        if loaded.settings is None:
            raise CaptureTargetSettingsError(loaded.status)
        vnext = loaded.settings
    try:
        vnext = replace(
            vnext,
            intent=replace(
                vnext.intent,
                desktop_audio=replace(
                    vnext.intent.desktop_audio,
                    vad_speech_threshold=settings.desktop_audio.vad_speech_threshold,
                    vad_hangover_ms=settings.desktop_audio.vad_hangover_ms,
                    vad_pre_roll_ms=settings.desktop_audio.vad_pre_roll_ms,
                ),
            ),
        )
        vnext = with_capture_target(vnext, capture_target)
    except Exception:
        raise CaptureTargetSettingsError("migration_failed") from None
    try:
        result = save_vnext_settings(path, vnext)
    except Exception:
        raise CaptureTargetSettingsError("save_failed") from None
    if not result.ok:
        raise CaptureTargetSettingsError(result.status)
    return _reload_after_save(path, original_bytes, capture_target)


def _reload_after_save(
    path: Path,
    original_bytes: bytes | None,
    capture_target: CaptureTargetIntent,
) -> AppSettings:
    try:
        reloaded_result = load_settings_with_result(path)
    except Exception:
        _rollback_post_save_failure(path, original_bytes, "load_failed")
    if not reloaded_result.ok or reloaded_result.settings is None:
        _rollback_post_save_failure(path, original_bytes, reloaded_result.status)
    try:
        reloaded = reloaded_result.settings
        reloaded.desktop_audio.runtime_capture_target = resolve_desktop_audio_capture_target(
            capture_target
        )
    except Exception:
        _rollback_post_save_failure(path, original_bytes, "load_failed")
    return reloaded


def _read_source_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise CaptureTargetSettingsError("load_failed") from None


def _rollback_post_save_failure(
    path: Path,
    original_bytes: bytes | None,
    status: object,
) -> NoReturn:
    try:
        _restore_source_bytes(path, original_bytes)
    except Exception:
        raise CaptureTargetSettingsError("rollback_failed") from None
    raise CaptureTargetSettingsError(status)


def _restore_source_bytes(path: Path, original_bytes: bytes | None) -> None:
    if original_bytes is None:
        path.unlink(missing_ok=True)
        return
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.capture-target-rollback")
    try:
        with temporary_path.open("xb") as handle:
            handle.write(original_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        with contextlib.suppress(FileNotFoundError, OSError):
            temporary_path.unlink()


def _status_value(status: object) -> str:
    return str(getattr(status, "value", status))


__all__ = ["CaptureTargetSettingsError", "persist_desktop_audio_capture_target"]
