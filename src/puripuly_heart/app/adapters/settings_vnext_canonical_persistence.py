from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

from puripuly_heart.app.ports.canonical_settings_persistence import ProviderVerificationBinding
from puripuly_heart.config.settings_vnext.facade import load_vnext_settings, save_vnext_settings
from puripuly_heart.config.settings_vnext.migration import (
    apply_legacy_app_settings_delta,
    from_legacy_app_settings,
)
from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
    ProviderVerificationEntry,
)


class SettingsVNextCanonicalPersistenceAdapter:
    def load(self, path: Path, compatibility_settings: object) -> AppSettingsVNext:
        result = load_vnext_settings(path)
        if result.settings is not None:
            return result.settings
        return from_legacy_app_settings(compatibility_settings)

    def persist(self, path: Path, settings: AppSettingsVNext) -> None:
        result = save_vnext_settings(path, settings)
        if not result.ok:
            status = getattr(result.status, "value", result.status)
            message = result.error.message if result.error is not None else status
            raise RuntimeError(message)

    def project(
        self,
        settings: object,
        *,
        canonical: AppSettingsVNext | None,
        authoritative: bool,
    ) -> AppSettingsVNext:
        if canonical is None or not authoritative:
            return from_legacy_app_settings(settings)
        return canonical

    def apply_legacy_delta(
        self,
        *,
        canonical: AppSettingsVNext | None,
        base_settings: object | None,
        next_settings: object,
    ) -> AppSettingsVNext:
        if canonical is None or base_settings is None:
            return from_legacy_app_settings(next_settings)
        return apply_legacy_app_settings_delta(
            canonical,
            base_settings,
            next_settings,
        )

    def bind_provider_verification(
        self,
        canonical: AppSettingsVNext,
        binding: ProviderVerificationBinding,
    ) -> AppSettingsVNext:
        verification = canonical.state.provider_verification
        if not hasattr(verification, binding.provider):
            raise ValueError(f"unsupported provider verification binding: {binding.provider}")
        entry = ProviderVerificationEntry(
            status="verified",
            provider=binding.provider,
            secret_key=binding.secret_key,
            secret_revision=binding.secret_revision,
            secret_fingerprint=binding.secret_fingerprint,
            verifier_context=dict(binding.verifier_context),
            verifier_evidence=dict(binding.verifier_evidence),
        )
        return replace(
            canonical,
            state=replace(
                canonical.state,
                provider_verification=replace(
                    verification,
                    **{binding.provider: entry},
                ),
            ),
        )

    def snapshot(self, canonical: AppSettingsVNext | None) -> AppSettingsVNext | None:
        return copy.deepcopy(canonical)

    def rollback(self, snapshot: AppSettingsVNext | None) -> AppSettingsVNext | None:
        return copy.deepcopy(snapshot)
