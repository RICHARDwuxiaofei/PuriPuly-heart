from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

LegacySettingsT = TypeVar("LegacySettingsT", contravariant=True)
CanonicalSettingsT = TypeVar("CanonicalSettingsT")


@runtime_checkable
class CanonicalSettingsPersistencePort(Protocol[LegacySettingsT, CanonicalSettingsT]):
    def load(self, path: Path, compatibility_settings: LegacySettingsT) -> CanonicalSettingsT: ...

    def persist(self, path: Path, settings: CanonicalSettingsT) -> None: ...

    def persist_delta(
        self,
        path: Path,
        *,
        baseline: CanonicalSettingsT,
        next_settings: CanonicalSettingsT,
    ) -> CanonicalSettingsT: ...

    def project(
        self,
        settings: LegacySettingsT,
        *,
        canonical: CanonicalSettingsT | None,
        authoritative: bool,
    ) -> CanonicalSettingsT: ...

    def apply_legacy_delta(
        self,
        *,
        canonical: CanonicalSettingsT | None,
        base_settings: LegacySettingsT | None,
        next_settings: LegacySettingsT,
    ) -> CanonicalSettingsT: ...

    def snapshot(self, canonical: CanonicalSettingsT | None) -> CanonicalSettingsT | None: ...

    def rollback(self, snapshot: CanonicalSettingsT | None) -> CanonicalSettingsT | None: ...
