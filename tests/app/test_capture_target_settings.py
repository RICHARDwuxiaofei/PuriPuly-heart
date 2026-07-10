from __future__ import annotations

import json
from pathlib import Path

import pytest

from puripuly_heart.app.services import capture_target_settings
from puripuly_heart.app.services.capture_target_settings import (
    CaptureTargetSettingsError,
    persist_desktop_audio_capture_target,
)
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.config.settings import to_dict as legacy_to_dict
from puripuly_heart.config.settings_vnext import compat
from puripuly_heart.config.settings_vnext.facade import (
    FacadeSettingsLoadResult,
    load_vnext_settings,
    save_vnext_settings,
)
from puripuly_heart.config.settings_vnext.migration import from_legacy_app_settings
from puripuly_heart.config.settings_vnext.schema import (
    CaptureTargetIntent,
    ProcessCaptureTargetIntent,
)


def _process_target() -> CaptureTargetIntent:
    return CaptureTargetIntent.process_target(
        ProcessCaptureTargetIntent.vrchat(r"C:\VRChat\VRChat.exe")
    )


def test_capture_target_persistence_creates_an_absent_settings_file(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"

    saved = persist_desktop_audio_capture_target(path, AppSettings(), _process_target())

    assert path.is_file()
    assert saved.desktop_audio.runtime_capture_target.kind == "process"
    loaded = load_vnext_settings(path)
    assert loaded.ok
    assert loaded.settings is not None
    assert loaded.settings.intent.desktop_audio.capture_target.kind == "process"


def test_capture_target_persistence_updates_valid_canonical_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save_vnext_settings(path, from_legacy_app_settings(AppSettings()))

    saved = persist_desktop_audio_capture_target(path, AppSettings(), _process_target())

    assert saved.desktop_audio.runtime_capture_target.kind == "process"
    loaded = load_vnext_settings(path)
    assert loaded.ok
    assert loaded.settings is not None
    assert loaded.settings.intent.desktop_audio.capture_target.kind == "process"


def test_capture_target_persistence_rejects_malformed_existing_settings_without_overwrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    original_bytes = b'{"intent":'
    path.write_bytes(original_bytes)

    with pytest.raises(CaptureTargetSettingsError) as raised:
        persist_desktop_audio_capture_target(path, AppSettings(), _process_target())

    assert raised.value.status == "parse_failed"
    assert "JSON" not in str(raised.value)
    assert path.read_bytes() == original_bytes


def test_capture_target_persistence_rejects_unreadable_existing_settings_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.json"
    original_bytes = b'{"legacy": "must remain untouched"}'
    path.write_bytes(original_bytes)
    save_calls: list[object] = []

    def fail_load(_path: Path):
        raise PermissionError("raw unreadable settings detail")

    monkeypatch.setattr(capture_target_settings, "load_vnext_settings", fail_load)
    monkeypatch.setattr(
        capture_target_settings,
        "save_vnext_settings",
        lambda *_args: save_calls.append(_args),
    )

    with pytest.raises(CaptureTargetSettingsError) as raised:
        persist_desktop_audio_capture_target(path, AppSettings(), _process_target())

    assert raised.value.status == "load_failed"
    assert "raw unreadable settings detail" not in str(raised.value)
    assert save_calls == []
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "status",
    [
        compat.SettingsPersistenceStatus.PARSE_FAILED,
        compat.SettingsPersistenceStatus.MIGRATION_FAILED,
        compat.SettingsPersistenceStatus.BACKUP_FAILED,
    ],
)
def test_capture_target_persistence_never_overwrites_a_failed_existing_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: compat.SettingsPersistenceStatus,
) -> None:
    path = tmp_path / "settings.json"
    original_bytes = b'{"legacy": "must remain untouched"}'
    path.write_bytes(original_bytes)
    failure = compat.VNextSettingsLoadResult(
        status=status,
        error=compat.SettingsPersistenceError(status, "raw secret failure detail"),
    )
    save_calls: list[object] = []
    monkeypatch.setattr(capture_target_settings, "load_vnext_settings", lambda _path: failure)
    monkeypatch.setattr(
        capture_target_settings,
        "save_vnext_settings",
        lambda *_args: save_calls.append(_args),
    )

    with pytest.raises(CaptureTargetSettingsError) as raised:
        persist_desktop_audio_capture_target(path, AppSettings(), _process_target())

    assert raised.value.status == status.value
    assert "raw secret failure detail" not in str(raised.value)
    assert save_calls == []
    assert path.read_bytes() == original_bytes


def test_capture_target_persistence_rejects_absent_file_migration_failure_without_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.json"

    def fail_migration(*_args, **_kwargs):
        raise RuntimeError("raw migration failure")

    monkeypatch.setattr(capture_target_settings, "from_legacy_app_settings", fail_migration)

    with pytest.raises(CaptureTargetSettingsError) as raised:
        persist_desktop_audio_capture_target(path, AppSettings(), _process_target())

    assert raised.value.status == "migration_failed"
    assert "raw migration failure" not in str(raised.value)
    assert not path.exists()


def _post_save_load_failure() -> FacadeSettingsLoadResult:
    status = compat.SettingsPersistenceStatus.PARSE_FAILED
    return FacadeSettingsLoadResult(
        status=status,
        error=compat.SettingsPersistenceError(status, "raw post-save reload detail"),
    )


def test_capture_target_persistence_rolls_back_existing_source_after_post_save_load_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.json"
    save_vnext_settings(path, from_legacy_app_settings(AppSettings()))
    original_bytes = path.read_bytes()
    live_settings = AppSettings()
    previous_runtime_target = live_settings.desktop_audio.runtime_capture_target
    monkeypatch.setattr(
        capture_target_settings,
        "load_settings_with_result",
        lambda _path: _post_save_load_failure(),
    )

    with pytest.raises(CaptureTargetSettingsError) as raised:
        persist_desktop_audio_capture_target(path, live_settings, _process_target())

    assert raised.value.status == "parse_failed"
    assert "raw post-save reload detail" not in str(raised.value)
    assert path.read_bytes() == original_bytes
    assert live_settings.desktop_audio.runtime_capture_target == previous_runtime_target


def test_capture_target_persistence_removes_new_file_after_post_save_load_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.json"
    live_settings = AppSettings()
    previous_runtime_target = live_settings.desktop_audio.runtime_capture_target
    monkeypatch.setattr(
        capture_target_settings,
        "load_settings_with_result",
        lambda _path: _post_save_load_failure(),
    )

    with pytest.raises(CaptureTargetSettingsError) as raised:
        persist_desktop_audio_capture_target(path, live_settings, _process_target())

    assert raised.value.status == "parse_failed"
    assert not path.exists()
    assert live_settings.desktop_audio.runtime_capture_target == previous_runtime_target


def test_capture_target_persistence_keeps_migration_backup_during_post_save_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.json"
    original_bytes = json.dumps(legacy_to_dict(AppSettings()), ensure_ascii=False).encode("utf-8")
    path.write_bytes(original_bytes)
    monkeypatch.setattr(
        capture_target_settings,
        "load_settings_with_result",
        lambda _path: _post_save_load_failure(),
    )

    with pytest.raises(CaptureTargetSettingsError):
        persist_desktop_audio_capture_target(path, AppSettings(), _process_target())

    backups = list(tmp_path.glob("settings.json.pre-v*.bak"))
    assert path.read_bytes() == original_bytes
    assert len(backups) == 1
    assert backups[0].read_bytes() == original_bytes


def test_capture_target_persistence_reports_safe_rollback_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.json"
    save_vnext_settings(path, from_legacy_app_settings(AppSettings()))

    def fail_restore(_path: Path, _original_bytes: bytes | None) -> None:
        raise OSError("raw rollback failure")

    monkeypatch.setattr(
        capture_target_settings,
        "load_settings_with_result",
        lambda _path: _post_save_load_failure(),
    )
    monkeypatch.setattr(capture_target_settings, "_restore_source_bytes", fail_restore)

    with pytest.raises(CaptureTargetSettingsError) as raised:
        persist_desktop_audio_capture_target(path, AppSettings(), _process_target())

    assert raised.value.status == "rollback_failed"
    assert "raw rollback failure" not in str(raised.value)
