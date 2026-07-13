from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

from puripuly_heart.app.ports.application_runtime import (
    ApplicationRuntimePort,
    ResolvedRuntimeActivationRequest,
)
from puripuly_heart.app.ports.runtime_resources import (
    InstalledRuntimeState,
    ResolvedRuntimeResourceFactoryPort,
    ResourceRef,
    RuntimeCommittedSettlementFailure,
    RuntimeHostInstallPort,
    RuntimeInstallFailure,
    RuntimeResourceBuildError,
    RuntimeResourceInstallCancelled,
    RuntimeResourceInstallError,
    RuntimeResourcePlannerPort,
    RuntimeResourceReplacementPlan,
)
from puripuly_heart.config.resolved import ResolvedApplicationRuntimeConfig
from puripuly_heart.core.lifecycle import LifecycleScope, start_lifecycle_task


@dataclass(frozen=True, slots=True)
class RuntimeResourceCleanupDiagnostic:
    operation: str
    failed_resources: int


class ProviderConfigDiffPlanner:
    def plan(self, current, target) -> RuntimeResourceReplacementPlan:  # noqa: ANN001
        if current is None:
            return RuntimeResourceReplacementPlan("replace", "replace", "replace")
        return RuntimeResourceReplacementPlan(
            "retain" if current.llm == target.llm else "replace",
            "retain" if current.self_stt == target.self_stt else "replace",
            "retain" if current.peer_stt == target.peer_stt else "replace",
        )


def _same_ref(left: ResourceRef | None, right: ResourceRef | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.identity == right.identity and left.resource is right.resource


def _same_ref_map(left, right) -> bool:  # noqa: ANN001
    return set(left) == set(right) and all(_same_ref(left[key], right[key]) for key in left)


@dataclass(slots=True)
class ResolvedRuntimeResourceAdapter(ApplicationRuntimePort):
    factory: ResolvedRuntimeResourceFactoryPort
    host: RuntimeHostInstallPort
    planner: RuntimeResourcePlannerPort = field(default_factory=ProviderConfigDiffPlanner)
    _active_config: ResolvedApplicationRuntimeConfig | None = field(
        default=None, init=False, repr=False
    )
    _active_state: InstalledRuntimeState = field(
        default_factory=lambda: InstalledRuntimeState({}), init=False, repr=False
    )
    _ownership_state_known: bool = field(default=False, init=False, repr=False)
    _pending_settlement: dict[tuple[str, int], ResourceRef] = field(
        default_factory=dict, init=False, repr=False
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    cleanup_diagnostics: list[RuntimeResourceCleanupDiagnostic] = field(
        default_factory=list, init=False
    )
    _scope: LifecycleScope = field(
        default_factory=lambda: LifecycleScope("resolved-runtime-resource-adapter"),
        init=False,
        repr=False,
    )

    async def replace_runtime(self, request: ResolvedRuntimeActivationRequest) -> None:
        await self._replace_runtime(request, explicit_plan=None, commit_guard=None)

    async def replace_runtime_with_plan(
        self,
        request: ResolvedRuntimeActivationRequest,
        plan: RuntimeResourceReplacementPlan,
    ) -> None:
        await self._replace_runtime(request, explicit_plan=plan, commit_guard=None)

    async def replace_runtime_with_plan_guarded(
        self,
        request: ResolvedRuntimeActivationRequest,
        plan: RuntimeResourceReplacementPlan,
        commit_guard: object,
    ) -> None:
        await self._replace_runtime(request, explicit_plan=plan, commit_guard=commit_guard)

    async def _replace_runtime(
        self,
        request: ResolvedRuntimeActivationRequest,
        *,
        explicit_plan: RuntimeResourceReplacementPlan | None,
        commit_guard: object | None = None,
    ) -> None:
        async with self._lock:
            if not self._ownership_state_known or self._pending_settlement:
                try:
                    self._active_state = await self.host.current_runtime_state()
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    self.cleanup_diagnostics.append(
                        RuntimeResourceCleanupDiagnostic("host_state_refresh", 1)
                    )
                    self._active_config = None
                    raise RuntimeResourceInstallError("host_state_query_failed")
                self._ownership_state_known = True
                await self._settle_pending(self._active_state)
            plan = explicit_plan or self.planner.plan(self._active_config, request.config)
            if plan.is_noop:
                self._active_config = request.config
                return
            try:
                staged = await self.factory.build_resources(request.config, plan)
                if commit_guard is not None:
                    staged = replace(staged, commit_guard=commit_guard)
            except RuntimeResourceBuildError as exc:
                await self._settle_build_failure(exc.staged)
                raise
            if self._cross_transaction_identity_conflict(staged):
                await self._settle_known(self._active_state, staged, "staged_identity_conflict")
                raise RuntimeResourceInstallError("staged_identity_conflict")
            cancellation: asyncio.CancelledError | None = None
            install_task = start_lifecycle_task(
                self._scope,
                self.host.install_runtime_resources(staged),
                name="install-runtime-resources",
            )
            try:
                result = await asyncio.shield(install_task)
            except asyncio.CancelledError as exc:
                cancellation = exc
                try:
                    result = await install_task
                except BaseException:
                    await self._settle_host_exception(staged)
                    raise exc
            except BaseException:
                await self._settle_host_exception(staged)
                raise
            if not self._valid_result(staged, result):
                await self._settle_known(result.active, staged, "invalid_install_result")
                self._active_state = result.active
                self._ownership_state_known = True
                self._active_config = None
                if cancellation is not None:
                    raise RuntimeResourceInstallCancelled(provider_state_committed=False)
                raise RuntimeResourceInstallError("invalid_install_result")
            if isinstance(result, RuntimeInstallFailure):
                await self._close_refs(
                    (*result.displaced_prior, *result.returned_candidates),
                    set(result.active.identities),
                    "install_failure",
                    True,
                )
                self._active_state = result.active
                self._ownership_state_known = True
                if not result.restored:
                    self._active_config = None
                if cancellation is not None:
                    raise RuntimeResourceInstallCancelled(provider_state_committed=False)
                raise RuntimeResourceInstallError(result.cause_code)
            self._active_state = result.active
            self._ownership_state_known = True
            self._active_config = request.config
            failed, close_cancelled = await self._close_committed_refs(
                (*result.displaced, *result.unadopted), set(result.active.identities)
            )
            if failed:
                raise RuntimeCommittedSettlementFailure(
                    failed,
                    cancellation_requested=cancellation is not None or close_cancelled,
                )
            if cancellation is not None:
                raise RuntimeResourceInstallCancelled(provider_state_committed=True)

    async def _close_committed_refs(
        self, refs: tuple[ResourceRef, ...], active_ids: set[str]
    ) -> tuple[tuple[ResourceRef, ...], bool]:
        closed: set[str] = set()
        failed: list[ResourceRef] = []
        cancelled = False
        for ref in refs:
            if ref.identity in active_ids or ref.identity in closed:
                continue
            closed.add(ref.identity)
            try:
                await ref.resource.close()
            except asyncio.CancelledError:
                failed.append(ref)
                cancelled = True
            except BaseException:
                failed.append(ref)
        if failed:
            self.cleanup_diagnostics.append(
                RuntimeResourceCleanupDiagnostic("ownership_return", len(failed))
            )
        return tuple(failed), cancelled

    async def _settle_host_exception(self, staged) -> None:  # noqa: ANN001
        try:
            active = await self.host.current_runtime_state()
        except asyncio.CancelledError:
            self._retain_known(staged)
            self._active_config = None
            self._ownership_state_known = False
            raise
        except BaseException:
            self._retain_known(staged)
            self.cleanup_diagnostics.append(RuntimeResourceCleanupDiagnostic("host_state_query", 1))
            self._active_config = None
            self._ownership_state_known = False
            return
        await self._settle_known(active, staged, "host_exception")
        self._active_state = active
        self._ownership_state_known = True
        self._active_config = None

    async def _settle_build_failure(self, staged) -> None:  # noqa: ANN001
        try:
            active = await self.host.current_runtime_state()
        except BaseException:
            self._retain_known(staged)
            self._active_config = None
            self._ownership_state_known = False
            self.cleanup_diagnostics.append(
                RuntimeResourceCleanupDiagnostic("build_failure_state_query", 1)
            )
            return
        await self._settle_known(active, staged, "build_failure")
        self._active_state = active
        self._ownership_state_known = True

    def _retain_known(self, staged) -> None:  # noqa: ANN001
        for ref in (*staged.candidates.values(), *self._active_state.slots.values()):
            self._pending_settlement[(ref.identity, id(ref.resource))] = ref

    async def _settle_pending(self, active: InstalledRuntimeState) -> None:
        active_refs = tuple(active.slots.values())
        remaining: dict[tuple[str, int], ResourceRef] = {}
        failures = 0
        cancellation: asyncio.CancelledError | None = None
        for key, ref in self._pending_settlement.items():
            if any(_same_ref(ref, active_ref) for active_ref in active_refs):
                continue
            try:
                await ref.resource.close()
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
                remaining[key] = ref
                failures += 1
            except Exception:
                remaining[key] = ref
                failures += 1
        self._pending_settlement = remaining
        if failures:
            self.cleanup_diagnostics.append(
                RuntimeResourceCleanupDiagnostic("pending_settlement", failures)
            )
        if cancellation is not None:
            raise cancellation

    def _cross_transaction_identity_conflict(self, staged) -> bool:  # noqa: ANN001
        active_by_id = {ref.identity: ref for ref in self._active_state.slots.values()}
        return any(
            ref.identity in active_by_id and active_by_id[ref.identity].resource is not ref.resource
            for ref in staged.candidates.values()
        )

    async def _settle_known(self, active, staged, operation: str) -> None:  # noqa: ANN001
        refs = (*staged.candidates.values(), *self._active_state.slots.values())
        inactive = tuple(
            ref
            for ref in refs
            if not any(_same_ref(ref, active_ref) for active_ref in active.slots.values())
        )
        await self._close_refs(
            inactive,
            set(),
            operation,
            True,
        )

    def _valid_result(self, staged, result) -> bool:  # noqa: ANN001
        prior = self._active_state
        candidates = staged.candidates
        active = result.active
        candidate_ids = {ref.identity for ref in candidates.values()}
        if isinstance(result, RuntimeInstallFailure):
            expected_returned = {
                ref.identity: ref
                for ref in candidates.values()
                if not any(_same_ref(ref, active_ref) for active_ref in active.slots.values())
            }
            actual_returned = {ref.identity: ref for ref in result.returned_candidates}
            if not _same_ref_map(actual_returned, expected_returned):
                return False
            expected_displaced = {
                ref.identity: ref
                for ref in prior.slots.values()
                if not any(_same_ref(ref, active_ref) for active_ref in active.slots.values())
            }
            actual_displaced = {ref.identity: ref for ref in result.displaced_prior}
            if not _same_ref_map(actual_displaced, expected_displaced):
                return False
            return not result.restored or _same_ref_map(active.slots, prior.slots)
        active_by_id = {ref.identity: ref for ref in active.slots.values()}
        expected_adopted = {
            ref.identity
            for ref in candidates.values()
            if _same_ref(active_by_id.get(ref.identity), ref)
        }
        expected_unadopted = {
            ref.identity: ref for ref in candidates.values() if ref.identity not in expected_adopted
        }
        actual_unadopted = {ref.identity: ref for ref in result.unadopted}
        if result.adopted_ids != expected_adopted:
            return False
        if not _same_ref_map(actual_unadopted, expected_unadopted):
            return False
        if result.adopted_ids | set(actual_unadopted) != candidate_ids:
            return False
        for slot in ("llm", "self_stt", "peer_stt"):
            action = getattr(staged.plan, slot)
            active_ref = active.slots.get(slot)
            prior_ref = prior.slots.get(slot)
            candidate = candidates.get(slot)
            if action == "retain" and not _same_ref(active_ref, prior_ref):
                return False
            if action == "clear" and active_ref is not None:
                return False
            if action == "replace" and not _same_ref(active_ref, candidate):
                return False
        expected_displaced = {
            ref.identity: ref
            for ref in prior.slots.values()
            if not any(_same_ref(ref, active_ref) for active_ref in active.slots.values())
        }
        actual_displaced = {ref.identity: ref for ref in result.displaced}
        return _same_ref_map(actual_displaced, expected_displaced)

    async def _close_refs(
        self,
        refs: tuple[ResourceRef, ...],
        active_ids: set[str],
        operation: str,
        preserve_primary: bool,
    ) -> None:
        closed: set[str] = set()
        failures: list[BaseException] = []
        for ref in refs:
            if ref.identity in active_ids or ref.identity in closed:
                continue
            closed.add(ref.identity)
            try:
                await ref.resource.close()
            except BaseException as exc:
                failures.append(exc)
                self._pending_settlement[(ref.identity, id(ref.resource))] = ref
        if failures:
            self.cleanup_diagnostics.append(
                RuntimeResourceCleanupDiagnostic(operation, len(failures))
            )
            if not preserve_primary:
                cancellation = next(
                    (item for item in failures if isinstance(item, asyncio.CancelledError)), None
                )
                if cancellation is not None:
                    raise cancellation
                exceptions = [item for item in failures if isinstance(item, Exception)]
                if len(exceptions) == 1:
                    raise exceptions[0]
                raise ExceptionGroup("runtime resource close failed", exceptions)


__all__ = [
    "ProviderConfigDiffPlanner",
    "ResolvedRuntimeResourceAdapter",
    "RuntimeResourceCleanupDiagnostic",
]
