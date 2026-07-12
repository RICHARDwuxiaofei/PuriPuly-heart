from __future__ import annotations

from dataclasses import dataclass

from puripuly_heart.app.ports.post_commit_runtime import (
    PostCommitRuntimeExecutionResult,
    RuntimeMutationProvenance,
    RuntimeMutationSurface,
    RuntimeOperationalSnapshot,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.post_commit_runtime import (
    PostCommitRuntimePlanBuilder,
    PostCommitRuntimeTransactionOwner,
)


@dataclass(slots=True)
class SelectiveSurfaceRuntimeTransactionPort:
    plan_builder: PostCommitRuntimePlanBuilder
    owner: PostCommitRuntimeTransactionOwner
    migrated_surfaces: frozenset[RuntimeMutationSurface]

    async def apply_surface_runtime(
        self,
        *,
        before: SettingsCommitReceipt | None,
        after: SettingsCommitReceipt,
        provenance: RuntimeMutationProvenance,
        operational: RuntimeOperationalSnapshot,
    ) -> PostCommitRuntimeExecutionResult:
        if provenance.surface not in self.migrated_surfaces:
            raise LookupError("runtime surface has not migrated to the additive seam")
        plan = self.plan_builder.build(
            before=before,
            after=after,
            provenance=provenance,
            operational=operational,
        )
        return await self.owner.apply(plan)


__all__ = ["SelectiveSurfaceRuntimeTransactionPort"]
