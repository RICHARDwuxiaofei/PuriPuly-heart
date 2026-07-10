from __future__ import annotations

from puripuly_heart.app.adapters.settings_vnext_canonical_persistence import (
    SettingsVNextCanonicalPersistenceAdapter,
)
from puripuly_heart.app.ports.canonical_settings_persistence import (
    CanonicalSettingsPersistencePort,
)
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


def compose_canonical_settings_persistence() -> (
    CanonicalSettingsPersistencePort[AppSettings, AppSettingsVNext]
):
    return SettingsVNextCanonicalPersistenceAdapter()
