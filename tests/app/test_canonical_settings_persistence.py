from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from puripuly_heart.app.adapters import (
    settings_vnext_canonical_persistence as adapter_module,
)
from puripuly_heart.app.adapters.settings_vnext_canonical_persistence import (
    SettingsVNextCanonicalPersistenceAdapter,
)
from puripuly_heart.app.ports.canonical_settings_persistence import (
    CanonicalSettingsPersistencePort,
)
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


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
