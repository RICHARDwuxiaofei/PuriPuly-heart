from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

ProviderSlot = Literal["llm", "self_stt", "peer_stt"]


@dataclass(frozen=True, slots=True)
class ProviderSlotState:
    provider: object | None
    identity: int | None
    generation: int


@dataclass(frozen=True, slots=True)
class ProviderStateSnapshot:
    epoch: int
    llm: ProviderSlotState
    self_stt: ProviderSlotState
    peer_stt: ProviderSlotState

    def slot(self, slot: ProviderSlot) -> ProviderSlotState:
        return getattr(self, slot)


class ProviderStateCell:
    def __init__(
        self,
        *,
        llm: object | None = None,
        self_stt: object | None = None,
        peer_stt: object | None = None,
    ) -> None:
        self._snapshot = ProviderStateSnapshot(
            epoch=0,
            llm=_initial_slot(llm),
            self_stt=_initial_slot(self_stt),
            peer_stt=_initial_slot(peer_stt),
        )

    def snapshot(self) -> ProviderStateSnapshot:
        return self._snapshot

    def replace(self, slot: ProviderSlot, provider: object | None) -> ProviderStateSnapshot:
        current = self._snapshot
        prior = current.slot(slot)
        if prior.provider is provider:
            return current
        updated = ProviderSlotState(
            provider=provider,
            identity=id(provider) if provider is not None else None,
            generation=prior.generation + 1,
        )
        self._snapshot = replace(current, epoch=current.epoch + 1, **{slot: updated})
        return self._snapshot


def _initial_slot(provider: object | None) -> ProviderSlotState:
    return ProviderSlotState(
        provider=provider,
        identity=id(provider) if provider is not None else None,
        generation=0,
    )


__all__ = [
    "ProviderSlot",
    "ProviderSlotState",
    "ProviderStateCell",
    "ProviderStateSnapshot",
]
