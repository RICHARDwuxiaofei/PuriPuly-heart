from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ProviderVerificationEvidence:
    """Service-owned operational-state DTO for provider verification evidence.

    Mirrors the persisted vNext ``state.provider_verification.{provider}`` shape
    without importing the schema module into app services. The helper below turns
    this value object into the nested mapping supplied to
    ``SettingsCommitRequest.values`` / ``RuntimeApplyRequest.settings_values``.
    """

    status: str
    provider: str
    secret_key: str
    secret_revision: str | None
    secret_fingerprint: str
    verifier_context: Mapping[str, object] = field(default_factory=dict)
    verifier_evidence: Mapping[str, object] = field(default_factory=dict)


def provider_verification_state_values(
    evidence: ProviderVerificationEvidence,
) -> dict[str, object]:
    """Return a nested vNext-style operational-state payload for one provider.

    The returned mapping uses ``state.provider_verification.{provider}`` as a
    nested dict path, not the legacy flat settings string, so it is clearly not
    a generic settings-mutation payload.
    """
    return {
        "state": {
            "provider_verification": {
                evidence.provider: {
                    "status": evidence.status,
                    "provider": evidence.provider,
                    "secret_key": evidence.secret_key,
                    "secret_revision": evidence.secret_revision,
                    "secret_fingerprint": evidence.secret_fingerprint,
                    "verifier_context": _copy_payload_value(evidence.verifier_context),
                    "verifier_evidence": _copy_payload_value(evidence.verifier_evidence),
                }
            }
        }
    }


def _copy_payload_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _copy_payload_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_copy_payload_value(item) for item in value]
    if isinstance(value, list):
        return [_copy_payload_value(item) for item in value]
    return value


__all__ = [
    "ProviderVerificationEvidence",
    "provider_verification_state_values",
]
