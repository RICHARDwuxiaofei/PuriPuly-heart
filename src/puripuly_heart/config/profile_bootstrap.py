from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from puripuly_heart.config.paths import stable_settings_path
from puripuly_heart.config.settings_vnext import compat, migration
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


@dataclass(frozen=True, slots=True)
class StableSettingsImportResult:
    imported: bool
    target_path: Path
    source_path: Path | None = None
    settings: AppSettingsVNext | None = None
    source_settings: AppSettingsVNext | None = None
    error: compat.SettingsPersistenceError | None = None

    @property
    def ok(self) -> bool:
        return self.imported and self.error is None


def import_stable_settings_if_missing(
    target_path: Path,
    *,
    source_path: Path | None = None,
) -> StableSettingsImportResult:
    if target_path.exists():
        return StableSettingsImportResult(imported=False, target_path=target_path)

    source_path = source_path or stable_settings_path()
    if _same_path(target_path, source_path) or not source_path.exists():
        return StableSettingsImportResult(
            imported=False,
            target_path=target_path,
            source_path=source_path,
        )

    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return StableSettingsImportResult(
            imported=False,
            target_path=target_path,
            source_path=source_path,
            error=_error(compat.SettingsPersistenceStatus.PARSE_FAILED, exc),
        )
    if not isinstance(raw, dict):
        return StableSettingsImportResult(
            imported=False,
            target_path=target_path,
            source_path=source_path,
            error=compat.SettingsPersistenceError(
                compat.SettingsPersistenceStatus.PARSE_FAILED,
                "settings file must contain a JSON object",
            ),
        )

    try:
        source_settings = migration.from_dict(raw)
        settings = _settings_for_vnext_profile(source_settings)
    except Exception as exc:
        return StableSettingsImportResult(
            imported=False,
            target_path=target_path,
            source_path=source_path,
            error=_error(compat.SettingsPersistenceStatus.MIGRATION_FAILED, exc),
        )

    save_result = compat.save_vnext_settings(target_path, settings)
    if not save_result.ok:
        return StableSettingsImportResult(
            imported=False,
            target_path=target_path,
            source_path=source_path,
            settings=settings,
            source_settings=source_settings,
            error=save_result.error,
        )

    return StableSettingsImportResult(
        imported=True,
        target_path=target_path,
        source_path=source_path,
        settings=settings,
        source_settings=source_settings,
    )


def _settings_for_vnext_profile(settings: AppSettingsVNext) -> AppSettingsVNext:
    secret_path = Path(settings.intent.secrets.encrypted_file_path)
    if not secret_path.is_absolute():
        return settings
    filename = secret_path.name or "secrets.json"
    return replace(
        settings,
        intent=replace(
            settings.intent,
            secrets=replace(settings.intent.secrets, encrypted_file_path=filename),
        ),
    )


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _error(
    status: compat.SettingsPersistenceStatus,
    exc: Exception,
) -> compat.SettingsPersistenceError:
    return compat.SettingsPersistenceError(status, f"{type(exc).__name__}: {exc}")


__all__ = [
    "StableSettingsImportResult",
    "import_stable_settings_if_missing",
]
