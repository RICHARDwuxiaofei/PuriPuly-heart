from __future__ import annotations

import pytest

from puripuly_heart.core.runtime.provider_rebuild import ProviderRuntimeRebuildService


@pytest.mark.asyncio
async def test_rebuild_llm_provider_clears_before_creating_and_replaces_new_provider() -> None:
    service = ProviderRuntimeRebuildService()
    events: list[tuple[str, object | None]] = []

    async def replace_provider(provider: object | None) -> None:
        events.append(("replace", provider))

    def create_provider() -> object:
        events.append(("create", None))
        return "llm"

    outcome = await service.rebuild_llm_provider(
        replace_provider=replace_provider,
        create_provider=create_provider,
    )

    assert outcome.provider == "llm"
    assert outcome.error is None
    assert events == [("replace", None), ("create", None), ("replace", "llm")]


@pytest.mark.asyncio
async def test_rebuild_stt_provider_replaces_none_after_factory_failure() -> None:
    service = ProviderRuntimeRebuildService()
    events: list[tuple[str, object | None]] = []
    expected_error = RuntimeError("stt unavailable")

    async def replace_provider(provider: object | None) -> None:
        events.append(("replace", provider))

    def create_provider() -> object:
        events.append(("create", None))
        raise expected_error

    outcome = await service.rebuild_stt_provider(
        replace_provider=replace_provider,
        create_provider=create_provider,
    )

    assert outcome.provider is None
    assert outcome.error is expected_error
    assert events == [("create", None), ("replace", None)]


@pytest.mark.asyncio
async def test_apply_peer_policy_delegates_to_peer_runtime() -> None:
    service = ProviderRuntimeRebuildService()
    calls: list[tuple[object, bool]] = []

    class PeerRuntime:
        async def apply_policy(self, *, config: object, desired_active: bool) -> None:
            calls.append((config, desired_active))

    config = object()

    await service.apply_peer_policy(
        peer_runtime=PeerRuntime(),
        config=config,
        desired_active=True,
    )

    assert calls == [(config, True)]
