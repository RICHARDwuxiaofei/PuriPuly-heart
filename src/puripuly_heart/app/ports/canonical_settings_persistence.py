from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt

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
        expected_revision: str,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt: ...

    def initialize(
        self,
        path: Path,
        settings: CanonicalSettingsT,
        *,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt: ...

    def load_receipt(
        self,
        path: Path,
        *,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt: ...

    def legacy_projection(self, settings: CanonicalSettingsT) -> LegacySettingsT: ...

    def values_for(self, settings: CanonicalSettingsT) -> Mapping[str, object]: ...

    def envelope_from_values(self, values: Mapping[str, object]) -> CanonicalSettingsT: ...

    def receipt_for(
        self,
        settings: CanonicalSettingsT,
        *,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt: ...

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
