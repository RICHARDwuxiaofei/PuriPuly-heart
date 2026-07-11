from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from puripuly_heart.app.ports.application_runtime import (
    ApplicationRuntimePort,
    ResolvedRuntimeActivationRequest,
)
from puripuly_heart.app.ports.runtime_resources import (
    ResolvedRuntimeResourceFactoryPort,
    RuntimeHostInstallPort,
    RuntimeProviderResources,
    RuntimeResourceBuildError,
    RuntimeResourcePlannerPort,
    RuntimeResourceReplacementPlan,
)
from puripuly_heart.config.resolved import ResolvedApplicationRuntimeConfig


@dataclass(frozen=True, slots=True)
class RuntimeResourceCleanupDiagnostic:
    operation: str
    failed_resources: int


class ProviderConfigDiffPlanner:
    def plan(
        self,
        current: ResolvedApplicationRuntimeConfig | None,
        target: ResolvedApplicationRuntimeConfig,
    ) -> RuntimeResourceReplacementPlan:
        if current is None:
            return RuntimeResourceReplacementPlan("replace", "replace", "replace")
        return RuntimeResourceReplacementPlan(
            "retain" if current.llm == target.llm else "replace",
            "retain" if current.self_stt == target.self_stt else "replace",
            "retain" if current.peer_stt == target.peer_stt else "replace",
        )


@dataclass(slots=True)
class ResolvedRuntimeResourceAdapter(ApplicationRuntimePort):
    factory: ResolvedRuntimeResourceFactoryPort
    host: RuntimeHostInstallPort
    planner: RuntimeResourcePlannerPort = field(default_factory=ProviderConfigDiffPlanner)
    _active_config: ResolvedApplicationRuntimeConfig | None = field(
        default=None, init=False, repr=False
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    cleanup_diagnostics: list[RuntimeResourceCleanupDiagnostic] = field(
        default_factory=list, init=False
    )

    async def replace_runtime(self, request: ResolvedRuntimeActivationRequest) -> None:
        async with self._lock:
            plan = self.planner.plan(self._active_config, request.config)
            if plan.is_noop:
                self._active_config = request.config
                return
            try:
                staged = await self.factory.build_resources(request.config, plan)
            except RuntimeResourceBuildError as exc:
                await self._cleanup_preserving_primary(exc.staged, "build_failure")
                raise
            try:
                installed = await self.host.install_runtime_resources(plan, staged)
            except BaseException:
                await self._cleanup_preserving_primary(staged, "install_failure")
                raise
            self._active_config = request.config
            await self._close_resources(
                installed.displaced,
                excluding=installed.active.identities(),
                operation="displaced_close",
                preserve_primary=False,
            )

    async def _cleanup_preserving_primary(
        self, resources: RuntimeProviderResources, operation: str
    ) -> None:
        await self._close_resources(
            resources,
            excluding=set(),
            operation=operation,
            preserve_primary=True,
        )

    async def _close_resources(
        self,
        resources: RuntimeProviderResources,
        *,
        excluding: set[int],
        operation: str,
        preserve_primary: bool,
    ) -> None:
        closed: set[int] = set()
        failures: list[Exception] = []
        cancellation: asyncio.CancelledError | None = None
        for resource in (resources.peer_stt, resources.self_stt, resources.llm):
            if resource is None or id(resource) in excluding or id(resource) in closed:
                continue
            closed.add(id(resource))
            try:
                await resource.close()
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
            except Exception as exc:
                failures.append(exc)
        failure_count = len(failures) + (1 if cancellation is not None else 0)
        if failure_count:
            self.cleanup_diagnostics.append(
                RuntimeResourceCleanupDiagnostic(operation, failure_count)
            )
            if not preserve_primary and cancellation is not None:
                raise cancellation
            if not preserve_primary:
                if len(failures) == 1:
                    raise failures[0]
                raise ExceptionGroup("runtime resource close failed", failures)


__all__ = [
    "ProviderConfigDiffPlanner",
    "ResolvedRuntimeResourceAdapter",
    "RuntimeResourceCleanupDiagnostic",
]
