from __future__ import annotations

import json

from puripuly_heart.config import settings as legacy_settings
from puripuly_heart.config.profile_bootstrap import import_stable_settings_if_missing
from puripuly_heart.config.settings_vnext import serialization


def test_import_stable_settings_if_missing_writes_vnext_without_touching_stable(tmp_path) -> None:
    stable_path = tmp_path / "stable" / "settings.json"
    target_path = tmp_path / "vnext" / "settings.json"
    stable_path.parent.mkdir()
    legacy = legacy_settings.AppSettings()
    legacy.ui.locale = "ko"
    stable_path.write_text(
        json.dumps(legacy_settings.to_dict(legacy), ensure_ascii=False),
        encoding="utf-8",
    )
    original_stable = stable_path.read_bytes()

    result = import_stable_settings_if_missing(target_path, source_path=stable_path)

    assert result.ok
    assert result.imported
    assert result.source_path == stable_path
    assert result.target_path == target_path
    assert result.settings is not None
    assert stable_path.read_bytes() == original_stable
    saved = json.loads(target_path.read_text(encoding="utf-8"))
    assert set(saved) == serialization.CANONICAL_TOP_LEVEL_KEYS
    assert saved["intent"]["ui"]["locale"] == "ko"


def test_import_stable_settings_if_missing_does_not_overwrite_existing_target(tmp_path) -> None:
    stable_path = tmp_path / "stable" / "settings.json"
    target_path = tmp_path / "vnext" / "settings.json"
    stable_path.parent.mkdir()
    target_path.parent.mkdir()
    stable_path.write_text("{}", encoding="utf-8")
    target_path.write_text('{"existing": true}', encoding="utf-8")

    result = import_stable_settings_if_missing(target_path, source_path=stable_path)

    assert not result.imported
    assert result.error is None
    assert target_path.read_text(encoding="utf-8") == '{"existing": true}'


def test_import_stable_settings_if_missing_reports_source_parse_failure(tmp_path) -> None:
    stable_path = tmp_path / "stable" / "settings.json"
    target_path = tmp_path / "vnext" / "settings.json"
    stable_path.parent.mkdir()
    stable_path.write_text("not-json", encoding="utf-8")

    result = import_stable_settings_if_missing(target_path, source_path=stable_path)

    assert not result.imported
    assert result.error is not None
    assert not target_path.exists()


def test_import_stable_settings_rewrites_absolute_encrypted_secret_path(tmp_path) -> None:
    stable_path = tmp_path / "stable" / "settings.json"
    target_path = tmp_path / "vnext" / "settings.json"
    stable_path.parent.mkdir()
    legacy = legacy_settings.AppSettings()
    absolute_secret_path = tmp_path / "stable" / "secrets-prod.json"
    legacy.secrets.backend = legacy_settings.SecretsBackend.ENCRYPTED_FILE
    legacy.secrets.encrypted_file_path = str(absolute_secret_path)
    stable_path.write_text(
        json.dumps(legacy_settings.to_dict(legacy), ensure_ascii=False),
        encoding="utf-8",
    )

    result = import_stable_settings_if_missing(target_path, source_path=stable_path)

    assert result.ok
    assert result.source_settings is not None
    assert result.settings is not None
    assert result.source_settings.intent.secrets.encrypted_file_path == str(absolute_secret_path)
    assert result.settings.intent.secrets.encrypted_file_path == "secrets-prod.json"
    saved = json.loads(target_path.read_text(encoding="utf-8"))
    assert saved["intent"]["secrets"]["encrypted_file_path"] == "secrets-prod.json"
