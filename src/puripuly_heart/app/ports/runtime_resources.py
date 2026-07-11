from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping, Protocol

from puripuly_heart.config.resolved import ResolvedApplicationRuntimeConfig

RuntimeResourceAction = Literal["retain", "replace", "clear"]
RuntimeResourceSlot = Literal["llm", "self_stt", "peer_stt"]


class AsyncProviderResource(Protocol):
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ResourceRef:
    identity: str
    resource: AsyncProviderResource

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("resource identity must be non-empty")


@dataclass(frozen=True, slots=True)
class RuntimeResourceReplacementPlan:
    llm: RuntimeResourceAction
    self_stt: RuntimeResourceAction
    peer_stt: RuntimeResourceAction

    @property
    def is_noop(self) -> bool:
        return self.llm == self.self_stt == self.peer_stt == "retain"


def _validated_refs(
    values: Mapping[RuntimeResourceSlot, ResourceRef],
) -> Mapping[RuntimeResourceSlot, ResourceRef]:
    identities: dict[str, object] = {}
    refs: dict[RuntimeResourceSlot, ResourceRef] = {}
    for slot, ref in values.items():
        existing = identities.setdefault(ref.identity, ref.resource)
        if existing is not ref.resource:
            raise ValueError("one resource identity cannot reference multiple objects")
        refs[slot] = ref
    return MappingProxyType(refs)


def _validate_identity_objects(refs) -> None:  # noqa: ANN001
    identities: dict[str, object] = {}
    for ref in refs:
        existing = identities.setdefault(ref.identity, ref.resource)
        if existing is not ref.resource:
            raise ValueError("one resource identity cannot reference multiple objects")


@dataclass(frozen=True, slots=True)
class StagedRuntimeResources:
    plan: RuntimeResourceReplacementPlan
    candidates: Mapping[RuntimeResourceSlot, ResourceRef]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", _validated_refs(self.candidates))
        for slot in ("llm", "self_stt", "peer_stt"):
            action = getattr(self.plan, slot)
            if (action == "replace") != (slot in self.candidates):
                raise ValueError("replace slots require exactly one staged candidate")


@dataclass(frozen=True, slots=True)
class InstalledRuntimeState:
    slots: Mapping[RuntimeResourceSlot, ResourceRef]

    def __post_init__(self) -> None:
        object.__setattr__(self, "slots", _validated_refs(self.slots))

    @property
    def identities(self) -> frozenset[str]:
        return frozenset(ref.identity for ref in self.slots.values())


@dataclass(frozen=True, slots=True)
class RuntimeInstallSuccess:
    active: InstalledRuntimeState
    adopted_ids: frozenset[str]
    displaced: tuple[ResourceRef, ...] = ()
    unadopted: tuple[ResourceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "adopted_ids", frozenset(self.adopted_ids))
        object.__setattr__(self, "displaced", tuple(self.displaced))
        object.__setattr__(self, "unadopted", tuple(self.unadopted))
        _validate_identity_objects((*self.active.slots.values(), *self.displaced, *self.unadopted))


@dataclass(frozen=True, slots=True)
class RuntimeInstallFailure:
    active: InstalledRuntimeState
    returned_candidates: tuple[ResourceRef, ...]
    restored: bool
    cause_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "returned_candidates", tuple(self.returned_candidates))
        _validate_identity_objects((*self.active.slots.values(), *self.returned_candidates))
        if not self.cause_code.strip():
            raise ValueError("install failure cause code must be non-empty")


RuntimeInstallResult = RuntimeInstallSuccess | RuntimeInstallFailure


class RuntimeResourceBuildError(Exception):
    def __init__(self, staged: StagedRuntimeResources) -> None:
        super().__init__("runtime resource build failed")
        self.staged = staged


class RuntimeResourceInstallError(Exception):
    def __init__(self, cause_code: str) -> None:
        super().__init__("runtime resource install failed")
        self.cause_code = cause_code


class ResolvedRuntimeResourceFactoryPort(Protocol):
    async def build_resources(
        self, config: ResolvedApplicationRuntimeConfig, plan: RuntimeResourceReplacementPlan
    ) -> StagedRuntimeResources: ...


class RuntimeHostInstallPort(Protocol):
    async def install_runtime_resources(
        self, staged: StagedRuntimeResources
    ) -> RuntimeInstallResult: ...

    async def current_runtime_state(self) -> InstalledRuntimeState: ...


class RuntimeResourcePlannerPort(Protocol):
    def plan(
        self,
        current: ResolvedApplicationRuntimeConfig | None,
        target: ResolvedApplicationRuntimeConfig,
    ) -> RuntimeResourceReplacementPlan: ...


__all__ = [name for name in globals() if not name.startswith("_")]
