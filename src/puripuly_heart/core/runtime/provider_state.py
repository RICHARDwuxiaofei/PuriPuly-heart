from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Mapping
from uuid import uuid4

ProviderSlot = Literal["llm", "self_stt", "peer_stt"]


@dataclass(frozen=True, slots=True)
class ResourceRef:
    identity: str
    resource: object

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("resource identity must be non-empty")


@dataclass(frozen=True, slots=True)
class ProviderSlotState:
    ref: ResourceRef | None
    generation: int

    @property
    def provider(self) -> object | None:
        return None if self.ref is None else self.ref.resource

    @property
    def identity(self) -> str | None:
        return None if self.ref is None else self.ref.identity


@dataclass(frozen=True, slots=True)
class ProviderStateSnapshot:
    epoch: int
    llm: ProviderSlotState
    self_stt: ProviderSlotState
    peer_stt: ProviderSlotState

    def slot(self, slot: ProviderSlot) -> ProviderSlotState:
        return getattr(self, slot)


@dataclass(frozen=True, slots=True)
class ProviderLease:
    _cell: "ProviderStateCell"
    slot: ProviderSlot
    identity: str
    generation: int

    @property
    def current(self) -> object | None:
        state = self._cell.snapshot().slot(self.slot)
        if state.generation != self.generation or state.identity != self.identity:
            return None
        return state.provider

    @property
    def is_current(self) -> bool:
        return self.current is not None


class ProviderStateCell:
    def __init__(
        self,
        *,
        llm: object | ResourceRef | None = None,
        self_stt: object | ResourceRef | None = None,
        peer_stt: object | ResourceRef | None = None,
    ) -> None:
        self._snapshot = ProviderStateSnapshot(
            epoch=0,
            llm=_initial_slot(llm),
            self_stt=_initial_slot(self_stt),
            peer_stt=_initial_slot(peer_stt),
        )

    def snapshot(self) -> ProviderStateSnapshot:
        return self._snapshot

    def lease(self, slot: ProviderSlot) -> ProviderLease | None:
        state = self._snapshot.slot(slot)
        if state.identity is None:
            return None
        return ProviderLease(self, slot, state.identity, state.generation)

    def replace(self, slot: ProviderSlot, provider: object | None) -> ProviderStateSnapshot:
        ref = provider if isinstance(provider, ResourceRef) else _new_ref(provider)
        return self.transition({slot: ref})

    def transition(
        self, replacements: Mapping[ProviderSlot, ResourceRef | None]
    ) -> ProviderStateSnapshot:
        current = self._snapshot
        if all(current.slot(slot).ref is ref for slot, ref in replacements.items()):
            return current
        updates = {
            slot: ProviderSlotState(ref=ref, generation=current.slot(slot).generation + 1)
            for slot, ref in replacements.items()
            if current.slot(slot).ref is not ref
        }
        self._snapshot = replace(current, epoch=current.epoch + 1, **updates)
        return self._snapshot


def _new_ref(provider: object | None) -> ResourceRef | None:
    if provider is None:
        return None
    return ResourceRef(f"provider-{uuid4().hex}", provider)


def _initial_slot(provider: object | ResourceRef | None) -> ProviderSlotState:
    ref = provider if isinstance(provider, ResourceRef) else _new_ref(provider)
    return ProviderSlotState(ref=ref, generation=0)


__all__ = [
    "ProviderSlot",
    "ProviderLease",
    "ResourceRef",
    "ProviderSlotState",
    "ProviderStateCell",
    "ProviderStateSnapshot",
]
