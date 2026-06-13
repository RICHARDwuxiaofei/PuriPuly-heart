from __future__ import annotations

from collections.abc import Mapping

from puripuly_heart.app.services.operational_state_payloads import (
    ProviderVerificationEvidence,
    provider_verification_state_values,
)


def test_provider_verification_state_values_copies_nested_metadata() -> None:
    verifier_context = {
        "flow": "pkce",
        "nested": {"phase": "before"},
        "steps": ["created"],
    }
    verifier_evidence = {
        "verifier": "openrouter",
        "nested": {"phase": "verified"},
        "attempts": [1],
    }

    values = provider_verification_state_values(
        ProviderVerificationEvidence(
            status="verified",
            provider="openrouter",
            secret_key="openrouter_api_key",
            secret_revision="secret-r1",
            secret_fingerprint="sha256:fixture",
            verifier_context=verifier_context,
            verifier_evidence=verifier_evidence,
        )
    )

    verifier_context["nested"]["phase"] = "after"
    verifier_context["steps"].append("mutated")
    verifier_evidence["nested"]["phase"] = "mutated"
    verifier_evidence["attempts"].append(2)

    entry = _provider_entry(values, "openrouter")
    context = entry["verifier_context"]
    assert isinstance(context, Mapping)
    assert context["nested"] == {"phase": "before"}
    assert context["steps"] == ["created"]
    evidence = entry["verifier_evidence"]
    assert isinstance(evidence, Mapping)
    assert evidence["nested"] == {"phase": "verified"}
    assert evidence["attempts"] == [1]


def _provider_entry(values: Mapping[str, object], provider: str) -> Mapping[str, object]:
    state = values["state"]
    assert isinstance(state, Mapping)
    provider_verification = state["provider_verification"]
    assert isinstance(provider_verification, Mapping)
    entry = provider_verification[provider]
    assert isinstance(entry, Mapping)
    return entry
