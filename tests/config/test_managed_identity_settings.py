from __future__ import annotations

import json

from puripuly_heart.config.settings import (
    SETTINGS_SCHEMA_VERSION,
    AppSettings,
    from_dict,
    load_settings,
    to_dict,
)


def test_managed_identity_settings_round_trip() -> None:
    settings = AppSettings()
    settings.managed_identity.installation_id = "01961ad7-a7c1-7000-8000-0123456789ab"
    settings.managed_identity.release_token = "release-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:00:45.000Z"

    restored = from_dict(to_dict(settings))

    assert restored.managed_identity == settings.managed_identity


def test_load_settings_backfills_managed_identity_defaults(tmp_path) -> None:
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy["settings_version"] = SETTINGS_SCHEMA_VERSION - 1
    legacy.pop("managed_identity", None)
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert loaded.managed_identity.installation_id == ""
    assert loaded.managed_identity.release_token is None
    assert loaded.managed_identity.release_token_expires_at is None
    assert persisted["settings_version"] == SETTINGS_SCHEMA_VERSION
    assert persisted["managed_identity"] == {
        "installation_id": "",
        "release_token": None,
        "release_token_expires_at": None,
    }
