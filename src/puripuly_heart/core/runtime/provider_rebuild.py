from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

ProviderFactory = Callable[[], object | Awaitable[object | None] | None]
ProviderReplacer = Callable[[object | None], Awaitable[object | None] | Awaitable[None]]


class PeerRuntimePolicyPort(Protocol):
    async def apply_policy(self, *, config: object, desired_active: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderRebuildOutcome:
    provider: object | None
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class ProviderRuntimeRebuildService:
    async def rebuild_llm_provider(
        self,
        *,
        replace_provider: ProviderReplacer,
        create_provider: ProviderFactory,
    ) -> ProviderRebuildOutcome:
        await replace_provider(None)
        outcome = await self._create_provider(create_provider)
        await replace_provider(outcome.provider)
        return outcome

    async def rebuild_stt_provider(
        self,
        *,
        replace_provider: ProviderReplacer,
        create_provider: ProviderFactory,
    ) -> ProviderRebuildOutcome:
        outcome = await self._create_provider(create_provider)
        await replace_provider(outcome.provider)
        return outcome

    async def apply_peer_policy(
        self,
        *,
        peer_runtime: PeerRuntimePolicyPort,
        config: object,
        desired_active: bool,
    ) -> None:
        await peer_runtime.apply_policy(config=config, desired_active=desired_active)

    async def _create_provider(self, create_provider: ProviderFactory) -> ProviderRebuildOutcome:
        try:
            result = create_provider()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            return ProviderRebuildOutcome(provider=None, error=exc)
        return ProviderRebuildOutcome(provider=result, error=None)


__all__ = [
    "PeerRuntimePolicyPort",
    "ProviderRebuildOutcome",
    "ProviderRuntimeRebuildService",
]
