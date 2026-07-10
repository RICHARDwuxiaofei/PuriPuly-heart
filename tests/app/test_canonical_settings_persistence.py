from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from puripuly_heart.app.adapters import (
    settings_vnext_canonical_persistence as adapter_module,
)
from puripuly_heart.app.adapters.settings_vnext_canonical_persistence import (
    SettingsVNextCanonicalPersistenceAdapter,
)
from puripuly_heart.app.ports.canonical_settings_persistence import (
    CanonicalSettingsPersistencePort,
)
from puripuly_heart.app.wiring_composition import create_canonical_state_repositories
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.config.settings_vnext.facade import save_vnext_settings
from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
    TelemetryOperationalState,
)


def test_canonical_settings_persistence_port_covers_load_project_delta_save_and_rollback(
    monkeypatch,
) -> None:
    adapter = SettingsVNextCanonicalPersistenceAdapter()
    settings = AppSettings()
    canonical = AppSettingsVNext()
    path = Path("settings.json")
    saved: list[AppSettingsVNext] = []

    assert isinstance(adapter, CanonicalSettingsPersistencePort)

    monkeypatch.setattr(
        adapter_module,
        "load_vnext_settings",
        lambda _path: SimpleNamespace(settings=canonical),
    )
    assert adapter.load(path, settings) is canonical

    monkeypatch.setattr(
        adapter_module,
        "load_vnext_settings",
        lambda _path: SimpleNamespace(settings=None),
    )
    projected = adapter.load(path, settings)
    assert projected.intent.languages.peer_source_mode == "manual"
    assert projected.intent.languages.peer_expected_languages == []

    assert adapter.project(settings, canonical=canonical, authoritative=True) is canonical
    assert adapter.project(settings, canonical=canonical, authoritative=False) == projected

    updated = copy.deepcopy(settings)
    updated.ui.locale = "ja"
    updated_canonical = adapter.apply_legacy_delta(
        canonical=projected,
        base_settings=settings,
        next_settings=updated,
    )
    assert updated_canonical.intent.ui.locale == "ja"

    monkeypatch.setattr(
        adapter_module,
        "save_vnext_settings",
        lambda _path, value: saved.append(value) or SimpleNamespace(ok=True),
    )
    adapter.persist(path, updated_canonical)
    assert saved == [updated_canonical]

    snapshot = adapter.snapshot(updated_canonical)
    assert snapshot == updated_canonical
    assert snapshot is not updated_canonical
    restored = adapter.rollback(snapshot)
    assert restored == updated_canonical
    assert restored is not snapshot


def test_stale_full_envelope_delta_does_not_overwrite_repository_operational_state(
    tmp_path,
) -> None:
    path = tmp_path / "settings.json"
    initial = AppSettingsVNext()
    assert save_vnext_settings(path, initial).ok
    adapter = SettingsVNextCanonicalPersistenceAdapter()
    cached_baseline = adapter.load(path, AppSettings())
    repositories = create_canonical_state_repositories(path)
    state = repositories.operational_state.load()
    repositories.operational_state.save(
        replace(
            state.value,
            telemetry=TelemetryOperationalState(
                anonymous_id="repository-owned-id",
                sent_translation_success_dates_utc=("2026-07-11",),
            ),
        ),
        expected_revision=state.revision,
    )
    desired = replace(
        cached_baseline,
        intent=replace(
            cached_baseline.intent,
            ui=replace(cached_baseline.intent.ui, locale="ja"),
        ),
    )

    committed = adapter.persist_delta(
        path,
        baseline=cached_baseline,
        next_settings=desired,
    )

    assert committed.intent.ui.locale == "ja"
    assert committed.state.telemetry.anonymous_id == "repository-owned-id"
    assert committed.state.telemetry.sent_translation_success_dates_utc == ("2026-07-11",)


@pytest.mark.parametrize("status", ["parse_failed", "migration_failed", "backup_failed"])
def test_persist_delta_fails_closed_when_latest_envelope_cannot_load(
    tmp_path,
    monkeypatch,
    status: str,
) -> None:
    path = tmp_path / "settings.json"
    original = b'{"historical":"bytes"}'
    path.write_bytes(original)
    adapter = SettingsVNextCanonicalPersistenceAdapter()
    baseline = AppSettingsVNext()
    desired = replace(
        baseline,
        intent=replace(baseline.intent, ui=replace(baseline.intent.ui, locale="ja")),
    )
    monkeypatch.setattr(
        adapter_module,
        "load_vnext_settings",
        lambda _path: SimpleNamespace(
            settings=None,
            status=status,
            error=SimpleNamespace(message=f"safe {status}"),
        ),
    )
    save_called = False

    def unexpected_save(*_args, **_kwargs):
        nonlocal save_called
        save_called = True
        raise AssertionError("save must not run after latest-load failure")

    monkeypatch.setattr(adapter_module, "save_vnext_settings", unexpected_save)
    with pytest.raises(RuntimeError, match=f"canonical latest load failed: safe {status}"):
        adapter.persist_delta(path, baseline=baseline, next_settings=desired)
    assert not save_called
    assert path.read_bytes() == original
