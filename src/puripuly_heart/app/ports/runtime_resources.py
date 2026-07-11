from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from puripuly_heart.config.resolved import ResolvedApplicationRuntimeConfig

RuntimeResourceAction = Literal["retain", "replace", "clear"]


class AsyncProviderResource(Protocol):
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeResourceReplacementPlan:
    llm: RuntimeResourceAction
    self_stt: RuntimeResourceAction
    peer_stt: RuntimeResourceAction

    @property
    def is_noop(self) -> bool:
        return self.llm == self.self_stt == self.peer_stt == "retain"


@dataclass(frozen=True, slots=True)
class RuntimeProviderResources:
    llm: AsyncProviderResource | None = None
    self_stt: AsyncProviderResource | None = None
    peer_stt: AsyncProviderResource | None = None

    def identities(self) -> set[int]:
        return {
            id(resource)
            for resource in (self.llm, self.self_stt, self.peer_stt)
            if resource is not None
        }


@dataclass(frozen=True, slots=True)
class RuntimeResourceInstallResult:
    active: RuntimeProviderResources
    displaced: RuntimeProviderResources


class RuntimeResourceBuildError(Exception):
    def __init__(self, staged: RuntimeProviderResources) -> None:
        super().__init__("runtime resource build failed")
        self.staged = staged


class ResolvedRuntimeResourceFactoryPort(Protocol):
    async def build_resources(
        self,
        config: ResolvedApplicationRuntimeConfig,
        plan: RuntimeResourceReplacementPlan,
    ) -> RuntimeProviderResources: ...


class RuntimeHostInstallPort(Protocol):
    async def install_runtime_resources(
        self,
        plan: RuntimeResourceReplacementPlan,
        staged: RuntimeProviderResources,
    ) -> RuntimeResourceInstallResult: ...


class RuntimeResourcePlannerPort(Protocol):
    def plan(
        self,
        current: ResolvedApplicationRuntimeConfig | None,
        target: ResolvedApplicationRuntimeConfig,
    ) -> RuntimeResourceReplacementPlan: ...


__all__ = [
    "AsyncProviderResource",
    "ResolvedRuntimeResourceFactoryPort",
    "RuntimeHostInstallPort",
    "RuntimeProviderResources",
    "RuntimeResourceBuildError",
    "RuntimeResourceInstallResult",
    "RuntimeResourcePlannerPort",
    "RuntimeResourceReplacementPlan",
]
