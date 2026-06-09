from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from puripuly_heart.config.settings import (
    SETTINGS_SCHEMA_VERSION,
    AppSettings,
    from_dict,
    load_settings,
    load_settings_with_result,
    save_settings,
    to_dict,
)
from puripuly_heart.config.settings_vnext.schema import (
    VNEXT_SETTINGS_SCHEMA_VERSION,
    AppSettingsVNext,
    PersistedOperationalState,
    ProviderVerificationEntry,
    ProviderVerificationState,
)
from tests.config.settings_migration_fixtures import (
    legacy_compatibility_settings_fixture,
    maximal_v24_settings_fixture,
)

PROVIDER_VERIFICATION_FIELDS = (
    "deepgram",
    "soniox",
    "google",
    "openrouter",
    "deepseek",
    "alibaba_beijing",
    "alibaba_singapore",
)


def _load_module(name: str) -> ModuleType:
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"{name} should import: {exc}")


def _migration() -> ModuleType:
    return _load_module("puripuly_heart.config.settings_vnext.migration")


def _serialization() -> ModuleType:
    return _load_module("puripuly_heart.config.settings_vnext.serialization")


def _compat() -> ModuleType:
    return _load_module("puripuly_heart.config.settings_vnext.compat")


def _leaf_paths(value: object, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        paths: set[str] = set()
        for key, child in value.items():
            child_path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, dict) and child:
                paths.update(_leaf_paths(child, child_path))
            else:
                paths.add(child_path)
        return paths
    return {prefix} if prefix else set()


def _write_json_bytes(path: Path, data: dict[str, Any]) -> bytes:
    raw_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(raw_bytes)
    return raw_bytes


def test_v24_maximal_fixture_migrates_to_canonical_vnext_serialization() -> None:
    migration = _migration()
    serialization = _serialization()

    settings = migration.from_dict(maximal_v24_settings_fixture())
    serialized = serialization.to_dict(settings)

    assert isinstance(settings, AppSettingsVNext)
    assert settings.settings_version == SETTINGS_SCHEMA_VERSION + 1
    assert settings.settings_version == VNEXT_SETTINGS_SCHEMA_VERSION
    assert set(serialized) == {"settings_version", "intent", "state"}
    assert serialized["settings_version"] == VNEXT_SETTINGS_SCHEMA_VERSION
    assert serialized["intent"]["translation"]["model"] == "local_llm"
    assert serialized["intent"]["translation"]["connection"] == "ollama"
    assert serialized["intent"]["translation"]["qwen"]["region"] == "singapore"
    assert serialized["intent"]["local_llm"]["base_url"] == "http://127.0.0.1:12345/v1"
    assert serialized["intent"]["stt"]["provider"] == "deepgram"
    assert serialized["intent"]["peer_stt"]["provider"] == "soniox"
    assert serialized["intent"]["ui"]["locale"] == "ja"
    assert serialized["intent"]["integrated_context"]["enabled"] is False
    assert serialized["state"]["integrated_context"]["bootstrapped"] is True
    assert serialized["state"]["peer_translation"]["eula_accepted"] is True
    assert serialized["state"]["provider_verification"]["deepgram"]["status"] == "unknown"
    assert "ui" not in serialized["state"]
    assert "provider" not in serialized
    assert "openrouter" not in serialized
    assert "api_key_verified" not in serialized


def test_v24_boolean_api_key_verification_migrates_every_provider_to_unknown() -> None:
    migration = _migration()
    serialization = _serialization()
    raw = maximal_v24_settings_fixture()

    assert all(
        raw["api_key_verified"][provider] is True for provider in PROVIDER_VERIFICATION_FIELDS
    )

    serialized = serialization.to_dict(migration.from_dict(raw))
    provider_entries = serialized["state"]["provider_verification"]

    assert provider_entries == {
        provider: {
            "status": "unknown",
            "provider": None,
            "secret_key": None,
            "secret_revision": None,
            "secret_fingerprint": None,
            "verifier_context": {},
            "verifier_evidence": {},
        }
        for provider in PROVIDER_VERIFICATION_FIELDS
    }


def test_public_facade_save_treats_bare_api_key_verified_booleans_as_unknown(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings()
    for provider in PROVIDER_VERIFICATION_FIELDS:
        setattr(settings.api_key_verified, provider, True)

    save_settings(path, settings)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {"settings_version", "intent", "state"}
    assert raw["settings_version"] == VNEXT_SETTINGS_SCHEMA_VERSION
    assert {
        provider: raw["state"]["provider_verification"][provider]
        for provider in PROVIDER_VERIFICATION_FIELDS
    } == {
        provider: {
            "status": "unknown",
            "provider": None,
            "secret_key": None,
            "secret_revision": None,
            "secret_fingerprint": None,
            "verifier_context": {},
            "verifier_evidence": {},
        }
        for provider in PROVIDER_VERIFICATION_FIELDS
    }

    loaded = load_settings(path)
    assert all(
        getattr(loaded.api_key_verified, provider) is False
        for provider in PROVIDER_VERIFICATION_FIELDS
    )


def test_evidence_bound_verified_entry_serializes_and_projects_legacy_true(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    verified_openrouter = ProviderVerificationEntry(
        status="verified",
        provider="openrouter",
        secret_key="openrouter_api_key",
        secret_revision="secret-r1",
        secret_fingerprint="sha256:0123456789abcdef",
        verifier_context={"flow": "settings.verify_api_key"},
        verifier_evidence={"verifier": "openrouter", "latency_ms": 12.5},
    )
    settings = AppSettingsVNext(
        state=PersistedOperationalState(
            provider_verification=ProviderVerificationState(openrouter=verified_openrouter)
        )
    )

    save_settings(path, settings)

    raw = json.loads(path.read_text(encoding="utf-8"))
    openrouter_entry = raw["state"]["provider_verification"]["openrouter"]
    assert openrouter_entry == {
        "status": "verified",
        "provider": "openrouter",
        "secret_key": "openrouter_api_key",
        "secret_revision": "secret-r1",
        "secret_fingerprint": "sha256:0123456789abcdef",
        "verifier_context": {"flow": "settings.verify_api_key"},
        "verifier_evidence": {"verifier": "openrouter", "latency_ms": 12.5},
    }

    loaded = load_settings(path)
    assert loaded.api_key_verified.openrouter is True
    assert loaded.api_key_verified.deepgram is False


def test_current_vnext_status_only_provider_verification_entries_load_as_unknown() -> None:
    migration = _migration()
    serialization = _serialization()
    raw = serialization.to_dict(AppSettingsVNext())
    raw["state"]["provider_verification"] = {
        "deepgram": {"status": "verified"},
        "soniox": {"status": "failed"},
        "google": {"status": "skipped"},
        "openrouter": {"status": "verified"},
        "deepseek": {"status": "failed"},
        "alibaba_beijing": {"status": "skipped"},
        "alibaba_singapore": {"status": "verified"},
    }

    for loader in (serialization.from_dict, migration.from_dict):
        settings = loader(raw)
        serialized = serialization.to_dict(settings)

        assert serialized["state"]["provider_verification"] == {
            provider: {
                "status": "unknown",
                "provider": None,
                "secret_key": None,
                "secret_revision": None,
                "secret_fingerprint": None,
                "verifier_context": {},
                "verifier_evidence": {},
            }
            for provider in PROVIDER_VERIFICATION_FIELDS
        }
        assert migration.to_legacy_dict(settings)["api_key_verified"] == {
            provider: False for provider in PROVIDER_VERIFICATION_FIELDS
        }


def test_current_vnext_evidence_bound_provider_verification_entry_survives_compatibility_shim() -> (
    None
):
    migration = _migration()
    serialization = _serialization()
    raw = serialization.to_dict(AppSettingsVNext())
    raw["state"]["provider_verification"]["openrouter"] = {
        "status": "verified",
        "provider": "openrouter",
        "secret_key": "openrouter_api_key",
        "secret_revision": None,
        "secret_fingerprint": "sha256:0123456789abcdef",
        "verifier_context": {"flow": "settings.verify_api_key"},
        "verifier_evidence": {"verifier": "openrouter"},
    }

    settings = migration.from_dict(raw)
    serialized = serialization.to_dict(settings)

    assert (
        serialized["state"]["provider_verification"]["openrouter"]
        == raw["state"]["provider_verification"]["openrouter"]
    )
    assert migration.to_legacy_dict(settings)["api_key_verified"]["openrouter"] is True


def test_legacy_accepted_keys_read_without_reintroducing_legacy_write_projection() -> None:
    migration = _migration()
    serialization = _serialization()

    settings = migration.from_dict(legacy_compatibility_settings_fixture())
    serialized = serialization.to_dict(settings)

    assert settings.intent.overlay.calibration.offset_x == 0.42
    assert settings.intent.overlay.show_translation is False
    assert settings.intent.overlay.show_peer_original is False
    assert settings.intent.peer_stt.provider == "soniox"
    serialized_paths = _leaf_paths(serialized)
    assert {
        "overlay_calibration.offset_x",
        "intent.ui.overlay_enabled",
        "state.ui.overlay_enabled",
        "intent.ui.peer_translation_enabled",
        "state.ui.peer_translation_enabled",
        "peer_qwen_asr_stt.model",
        "peer_soniox_stt.endpoint",
        "system_prompts.legacy",
    }.isdisjoint(serialized_paths)


def test_current_vnext_dict_reads_and_serializes_idempotently() -> None:
    migration = _migration()
    serialization = _serialization()

    original = AppSettingsVNext()
    raw = serialization.to_dict(original)

    loaded = migration.from_dict(raw)
    serialized = serialization.to_dict(loaded)

    assert loaded == original
    assert serialized == raw


def test_save_vnext_settings_normalizes_stale_settings_version_to_current(
    tmp_path: Path,
) -> None:
    compat = _compat()
    path = tmp_path / "settings.json"
    stale = replace(AppSettingsVNext(), settings_version=VNEXT_SETTINGS_SCHEMA_VERSION - 1)

    result = compat.save_vnext_settings(path, stale)

    assert result.ok
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {"settings_version", "intent", "state"}
    assert raw["settings_version"] == VNEXT_SETTINGS_SCHEMA_VERSION


def test_migration_on_load_creates_byte_identical_backup_and_writes_vnext_with_collision(
    tmp_path: Path,
) -> None:
    compat = _compat()
    fixed_now = datetime(2026, 6, 9, 1, 2, 3, tzinfo=timezone.utc)
    path = tmp_path / "settings.json"
    original_bytes = _write_json_bytes(path, maximal_v24_settings_fixture())
    colliding_backup = tmp_path / "settings.json.pre-v24.20260609T010203Z.bak"
    colliding_backup.write_bytes(b"existing backup")

    result = compat.load_vnext_settings(path, now=fixed_now)

    assert result.status == compat.SettingsPersistenceStatus.SUCCESS
    assert result.migrated is True
    assert result.backup_path == tmp_path / "settings.json.pre-v24.20260609T010203Z.1.bak"
    assert result.backup_path.read_bytes() == original_bytes
    assert colliding_backup.read_bytes() == b"existing backup"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert set(persisted) == {"settings_version", "intent", "state"}
    assert persisted["settings_version"] == VNEXT_SETTINGS_SCHEMA_VERSION


def test_backup_creation_failure_aborts_vnext_save_and_leaves_original_bytes(
    tmp_path: Path,
) -> None:
    compat = _compat()
    fixed_now = datetime(2026, 6, 9, 1, 2, 3, tzinfo=timezone.utc)
    path = tmp_path / "settings.json"
    original_bytes = _write_json_bytes(path, maximal_v24_settings_fixture())
    first_backup = tmp_path / "settings.json.pre-v24.20260609T010203Z.bak"
    first_backup.write_bytes(b"collision")

    result = compat.load_vnext_settings(path, now=fixed_now, max_backup_attempts=1)

    assert result.status == compat.SettingsPersistenceStatus.BACKUP_FAILED
    assert result.settings is None
    assert path.read_bytes() == original_bytes
    assert first_backup.read_bytes() == b"collision"


def test_save_failure_before_final_replace_leaves_original_and_backup_safe(
    tmp_path: Path,
) -> None:
    compat = _compat()
    fixed_now = datetime(2026, 6, 9, 1, 2, 3, tzinfo=timezone.utc)
    path = tmp_path / "settings.json"
    original_bytes = _write_json_bytes(path, maximal_v24_settings_fixture())
    (tmp_path / "settings.json.tmp").mkdir()

    result = compat.load_vnext_settings(path, now=fixed_now)

    assert result.status == compat.SettingsPersistenceStatus.SAVE_FAILED
    assert result.settings is None
    assert path.read_bytes() == original_bytes
    backup_path = tmp_path / "settings.json.pre-v24.20260609T010203Z.bak"
    assert backup_path.read_bytes() == original_bytes


def test_parse_and_migration_failures_return_explicit_results_without_overwrite(
    tmp_path: Path,
) -> None:
    compat = _compat()
    parse_path = tmp_path / "parse-settings.json"
    parse_path.write_text("not json", encoding="utf-8")

    parse_result = compat.load_vnext_settings(parse_path)

    assert parse_result.status == compat.SettingsPersistenceStatus.PARSE_FAILED
    assert parse_path.read_text(encoding="utf-8") == "not json"

    migration_path = tmp_path / "migration-settings.json"
    migration_bytes = _write_json_bytes(
        migration_path,
        {
            **maximal_v24_settings_fixture(),
            "overlay": {"calibration": {"anchor": "unsupported_anchor"}},
        },
    )

    migration_result = compat.load_vnext_settings(migration_path)
    assert migration_result.status == compat.SettingsPersistenceStatus.MIGRATION_FAILED
    assert migration_path.read_bytes() == migration_bytes


@pytest.mark.parametrize(
    ("raw", "case_id"),
    [
        (
            {"settings_version": VNEXT_SETTINGS_SCHEMA_VERSION, "intent": {}},
            "missing-state",
        ),
        (
            {"settings_version": VNEXT_SETTINGS_SCHEMA_VERSION, "intent": [], "state": {}},
            "non-object-intent",
        ),
        (
            {"settings_version": VNEXT_SETTINGS_SCHEMA_VERSION, "intent": {}, "state": []},
            "non-object-state",
        ),
    ],
)
def test_malformed_current_vnext_top_level_shape_fails_without_backup_or_overwrite(
    tmp_path: Path,
    raw: dict[str, Any],
    case_id: str,
) -> None:
    compat = _compat()
    path = tmp_path / f"{case_id}.json"
    original_bytes = _write_json_bytes(path, raw)

    result = compat.load_vnext_settings(path)

    assert result.status == compat.SettingsPersistenceStatus.MIGRATION_FAILED
    assert result.settings is None
    assert result.backup_path is None
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob("*.bak"))


def test_facade_projection_failure_returns_explicit_result_without_overwrite(
    tmp_path: Path,
) -> None:
    compat = _compat()
    serialization = _serialization()
    path = tmp_path / "settings.json"
    raw = serialization.to_dict(AppSettingsVNext())
    raw["intent"]["osc"]["port"] = "not-an-int"
    original_bytes = _write_json_bytes(path, raw)

    result = load_settings_with_result(path)

    assert result.status == compat.SettingsPersistenceStatus.MIGRATION_FAILED
    assert result.settings is None
    assert result.error is not None
    assert path.read_bytes() == original_bytes
    with pytest.raises(RuntimeError):
        load_settings(path)


def test_raw_provider_api_key_fields_are_absent_from_vnext_serialized_output() -> None:
    migration = _migration()
    serialization = _serialization()
    raw = maximal_v24_settings_fixture()
    raw.update(
        {
            "google_api_key": "raw-google-secret",
            "openrouter_api_key": "raw-openrouter-secret",
            "deepgram_api_key": "raw-deepgram-secret",
            "soniox_api_key": "raw-soniox-secret",
            "local_llm_api_key": "raw-local-secret",
        }
    )

    serialized = serialization.to_dict(migration.from_dict(raw))
    encoded = json.dumps(serialized, ensure_ascii=False)
    forbidden_names = {
        "alibaba_api_key",
        "alibaba_api_key_beijing",
        "alibaba_api_key_singapore",
        "deepgram_api_key",
        "deepseek_api_key",
        "google_api_key",
        "local_llm_api_key",
        "openrouter_api_key",
        "openrouter_managed_api_key",
        "soniox_api_key",
    }

    assert forbidden_names.isdisjoint(
        path.rsplit(".", maxsplit=1)[-1] for path in _leaf_paths(serialized)
    )
    assert "raw-google-secret" not in encoded
    assert "raw-openrouter-secret" not in encoded
    assert "raw-deepgram-secret" not in encoded
    assert "raw-soniox-secret" not in encoded
    assert "raw-local-secret" not in encoded


def test_public_settings_facade_keeps_legacy_imports_and_reads_vnext_dict() -> None:
    settings_module = import_module("puripuly_heart.config.settings")
    serialization = _serialization()

    assert settings_module.AppSettings is AppSettings
    assert settings_module.AppSettingsVNext is AppSettingsVNext
    assert "provider" in to_dict(AppSettings())
    assert set(serialization.to_dict(AppSettingsVNext())) == {"settings_version", "intent", "state"}

    vnext = replace(
        AppSettingsVNext(),
        settings_version=VNEXT_SETTINGS_SCHEMA_VERSION,
    )
    legacy = from_dict(serialization.to_dict(vnext))

    assert isinstance(legacy, AppSettings)
    assert legacy.settings_version == SETTINGS_SCHEMA_VERSION
    assert hasattr(settings_module, "load_vnext_settings")
    assert hasattr(settings_module, "save_settings_with_result")
