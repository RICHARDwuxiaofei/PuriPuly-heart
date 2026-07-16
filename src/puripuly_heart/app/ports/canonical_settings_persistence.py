from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

LegacySettingsT = TypeVar("LegacySettingsT", contravariant=True)
CanonicalSettingsT = TypeVar("CanonicalSettingsT")


@dataclass(frozen=True, slots=True)
class ProviderVerificationBinding:
    provider: str
    secret_key: str
    secret_revision: str | None
    secret_fingerprint: str | None
    verifier_context: Mapping[str, object] = field(default_factory=dict)
    verifier_evidence: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class CanonicalSettingsPersistencePort(Protocol[LegacySettingsT, CanonicalSettingsT]):
    def load(self, path: Path, compatibility_settings: LegacySettingsT) -> CanonicalSettingsT: ...

    def persist(self, path: Path, settings: CanonicalSettingsT) -> None: ...

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

    def bind_provider_verification(
        self,
        canonical: CanonicalSettingsT,
        binding: ProviderVerificationBinding,
    ) -> CanonicalSettingsT: ...

    def snapshot(self, canonical: CanonicalSettingsT | None) -> CanonicalSettingsT | None: ...

    def rollback(self, snapshot: CanonicalSettingsT | None) -> CanonicalSettingsT | None: ...
