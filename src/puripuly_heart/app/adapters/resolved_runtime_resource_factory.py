from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from puripuly_heart.app.ports.runtime_resource_factory import (
    LLMResourceBuilderPort,
    ManagedDelegatePort,
    ManagedReleaseServicePort,
    RuntimeClockPort,
    RuntimeFactoryDiagnosticsPort,
    RuntimeLoggingPort,
    RuntimeSecretReadPort,
    STTResourceBuilderPort,
)
from puripuly_heart.app.ports.runtime_resources import (
    AsyncProviderResource,
    ResourceRef,
    RuntimeResourceReplacementPlan,
    RuntimeResourceSlot,
    StagedRuntimeResources,
)
from puripuly_heart.config.resolved import ResolvedApplicationRuntimeConfig


@dataclass(slots=True)
class ResolvedRuntimeResourceFactory:
    secrets: RuntimeSecretReadPort
    clock: RuntimeClockPort
    diagnostics: RuntimeFactoryDiagnosticsPort
    llm_builder: LLMResourceBuilderPort
    stt_builder: STTResourceBuilderPort
    runtime_logging: RuntimeLoggingPort | None = None
    managed_release_service: ManagedReleaseServicePort | None = None
    managed_release_owner: object | None = None
    managed_delegate: ManagedDelegatePort | None = None

    async def build_resources(
        self,
        config: ResolvedApplicationRuntimeConfig,
        plan: RuntimeResourceReplacementPlan,
    ) -> StagedRuntimeResources:
        candidates: dict[RuntimeResourceSlot, ResourceRef] = {}
        owned: list[ResourceRef] = []
        refs_by_resource: dict[int, ResourceRef] = {}
        try:
            if plan.llm == "replace":
                managed_release_service = self.managed_release_service
                if self.managed_release_owner is not None:
                    managed_release_service = self.managed_release_owner.construction_service()
                ref = self._ref_for_resource(
                    "llm",
                    self.llm_builder.build_llm(
                        config.llm,
                        secrets=self.secrets,
                        managed_release_service=managed_release_service,
                        managed_delegate=self.managed_delegate,
                        runtime_logging=self.runtime_logging,
                    ),
                    refs_by_resource,
                )
                candidates["llm"] = ref
                owned.append(ref)
            if plan.self_stt == "replace":
                ref = self._ref_for_resource(
                    "self_stt", self._build_stt(config.self_stt), refs_by_resource
                )
                candidates["self_stt"] = ref
                owned.append(ref)
            if plan.peer_stt == "replace":
                ref = self._ref_for_resource(
                    "peer_stt", self._build_stt(config.peer_stt), refs_by_resource
                )
                candidates["peer_stt"] = ref
                owned.append(ref)
            staged = StagedRuntimeResources(plan=plan, candidates=candidates)
        except BaseException:
            await self._settle_partial(owned)
            raise
        return staged

    def _build_stt(self, config):  # noqa: ANN001, ANN202
        return self.stt_builder.build_stt(
            config,
            secrets=self.secrets,
            clock=self.clock,
            runtime_logging=self.runtime_logging,
            diagnostics=self.diagnostics,
        )

    async def _settle_partial(self, refs: list[ResourceRef]) -> None:
        settled: set[int] = set()
        for ref in reversed(refs):
            key = id(ref.resource)
            if key in settled:
                continue
            settled.add(key)
            close = getattr(ref.resource, "close", None)
            if not callable(close):
                continue
            try:
                await close()
            except BaseException as exc:
                try:
                    self.diagnostics.record_cleanup_failure(
                        slot=ref.identity.split("-", 1)[0],
                        exception_class=type(exc).__name__,
                    )
                except BaseException:
                    pass

    @staticmethod
    def _ref_for_resource(
        slot: RuntimeResourceSlot,
        resource: AsyncProviderResource,
        refs_by_resource: dict[int, ResourceRef],
    ) -> ResourceRef:
        key = id(resource)
        existing = refs_by_resource.get(key)
        if existing is not None and existing.resource is resource:
            return existing
        ref = ResourceRef(f"{slot}-{uuid4().hex}", resource)
        refs_by_resource[key] = ref
        return ref


__all__ = ["ResolvedRuntimeResourceFactory"]
