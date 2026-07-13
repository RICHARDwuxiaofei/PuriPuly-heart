from __future__ import annotations

from puripuly_heart.app.ports.provider_verifier import ProviderVerifierPort


def create_provider_verifier() -> ProviderVerifierPort:
    from puripuly_heart.app.adapters.provider_verifier import ProviderVerifierAdapter

    return ProviderVerifierAdapter()
