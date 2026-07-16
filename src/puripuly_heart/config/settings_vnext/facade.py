from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from puripuly_heart.config.capture_target_resolution import resolve_desktop_audio_capture_target
from puripuly_heart.config.settings_vnext import compat as vnext_compat
from puripuly_heart.config.settings_vnext import migration as vnext_migration
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


@dataclass(frozen=True, slots=True)
class FacadeSettingsLoadResult:
    status: Any
    settings: AppSettings | None = None
    migrated: bool = False
    backup_path: Path | None = None
    error: Any | None = None

    @property
    def ok(self) -> bool:
        status_value = getattr(self.status, "value", self.status)
        return status_value == "success"


def load_settings(path: Path) -> AppSettings:
    result = load_settings_with_result(path)
    if result.settings is None:
        status_value = getattr(result.status, "value", result.status)
        raise RuntimeError(result.error.message if result.error is not None else status_value)
    return result.settings


def load_settings_with_result(
    path: Path,
) -> vnext_compat.VNextSettingsLoadResult | FacadeSettingsLoadResult:
    result = vnext_compat.load_vnext_settings(path)
    if result.settings is None:
        return result
    try:
        legacy_settings_module = _legacy_settings_module()
        legacy_settings = legacy_settings_module.from_dict(
            vnext_migration.to_legacy_dict(result.settings)
        )
        legacy_settings.desktop_audio.runtime_capture_target = resolve_desktop_audio_capture_target(
            result.settings.intent.desktop_audio.capture_target
        )
    except Exception as exc:
        status = vnext_compat.SettingsPersistenceStatus.MIGRATION_FAILED
        return FacadeSettingsLoadResult(
            status=status,
            settings=None,
            migrated=result.migrated,
            backup_path=result.backup_path,
            error=vnext_compat.safe_persistence_error(status, exc),
        )
    return FacadeSettingsLoadResult(
        status=result.status,
        settings=legacy_settings,
        migrated=result.migrated,
        backup_path=result.backup_path,
        error=result.error,
    )


def save_settings(path: Path, settings: AppSettings | AppSettingsVNext) -> None:
    vnext_compat._save_vnext_settings_or_raise(
        path,
        _canonical_settings_for_save(path, settings),
    )


def save_settings_with_result(
    path: Path,
    settings: AppSettings | AppSettingsVNext,
) -> vnext_compat.VNextSettingsSaveResult:
    try:
        vnext_settings = _canonical_settings_for_save(path, settings)
    except Exception as exc:
        status = vnext_compat.SettingsPersistenceStatus.SAVE_FAILED
        return vnext_compat.VNextSettingsSaveResult(
            status=status,
            error=vnext_compat.safe_persistence_error(status, exc),
        )
    return vnext_compat.save_vnext_settings(path, vnext_settings)


def _canonical_settings_for_save(
    path: Path,
    settings: AppSettings | AppSettingsVNext,
) -> AppSettingsVNext:
    if isinstance(settings, AppSettingsVNext):
        return settings
    settings.validate()
    existing = _load_existing_canonical_projection(path)
    if existing is None:
        vnext_settings = vnext_migration.from_legacy_app_settings(settings)
    else:
        canonical, baseline = existing
        vnext_settings = vnext_migration.apply_legacy_app_settings_delta(
            canonical,
            baseline,
            settings,
        )
    return _preserve_existing_process_capture_target(path, settings, vnext_settings)


def load_vnext_settings(path: Path, **kwargs: Any) -> vnext_compat.VNextSettingsLoadResult:
    return vnext_compat.load_vnext_settings(path, **kwargs)


def save_vnext_settings(
    path: Path,
    settings: AppSettingsVNext,
) -> vnext_compat.VNextSettingsSaveResult:
    return vnext_compat.save_vnext_settings(path, settings)


def _legacy_settings_module() -> Any:
    from puripuly_heart.config import settings as legacy_settings

    return legacy_settings


def _load_existing_canonical_projection(
    path: Path,
) -> tuple[AppSettingsVNext, AppSettings] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping) or not vnext_migration.is_vnext_settings_dict(raw):
        return None
    canonical = vnext_migration.from_dict(raw)
    legacy = _legacy_settings_module().from_dict(vnext_migration.to_legacy_dict(canonical))
    return canonical, legacy


def _preserve_existing_process_capture_target(
    path: Path,
    legacy_settings: AppSettings,
    next_settings: AppSettingsVNext,
) -> AppSettingsVNext:
    runtime_capture_target = legacy_settings.desktop_audio.runtime_capture_target
    if runtime_capture_target is not None and runtime_capture_target.kind != "process":
        return next_settings
    if runtime_capture_target is None and legacy_settings.desktop_audio.output_device:
        return next_settings
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping) or not vnext_migration.is_vnext_settings_dict(raw):
            return next_settings
        existing = vnext_migration.from_dict(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return next_settings
    capture_target = existing.intent.desktop_audio.capture_target
    if capture_target.kind != "process":
        return next_settings
    return replace(
        next_settings,
        intent=replace(
            next_settings.intent,
            desktop_audio=replace(
                next_settings.intent.desktop_audio,
                capture_target=capture_target,
            ),
        ),
    )


__all__ = [
    "FacadeSettingsLoadResult",
    "load_settings",
    "load_settings_with_result",
    "load_vnext_settings",
    "save_settings",
    "save_settings_with_result",
    "save_vnext_settings",
]
