from __future__ import annotations

from types import SimpleNamespace

import pytest

from puripuly_heart.app.adapters.application_runtime_production import (
    ProductionRuntimeSynchronization,
)
from puripuly_heart.app.adapters.openrouter_pkce_production import (
    ApplicationHostPkceRuntimeApply,
    ProductionCanonicalSettingsRepository,
)
from puripuly_heart.app.adapters.overlay_ui_projection import ProductionUiProjection
from puripuly_heart.app.adapters.settings_vnext_canonical_persistence import (
    SettingsVNextCanonicalPersistenceAdapter,
)
from puripuly_heart.app.ports.post_commit_runtime import (
    DashboardRetryFactsDirective,
    PostCommitRuntimeExecutionResult,
    RuntimeMutationProvenance,
    RuntimeOperationalSnapshot,
)
from puripuly_heart.app.ports.runtime_apply import RuntimeApplyRequest
from puripuly_heart.app.ports.settings_repository import SettingsCommitRequest
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.app.services.post_commit_runtime import (
    PostCommitRuntimePlanBuilder,
    PostCommitRuntimeTransactionOwner,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.messages import (
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_FAILED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
    RuntimeApplyResult,
    TransactionResult,
)


def operational() -> RuntimeOperationalSnapshot:
    return RuntimeOperationalSnapshot(*([False] * 13))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transaction_status", "reconciliation", "expected_status"),
    (
        (
            TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
            False,
            RUNTIME_APPLY_STATUS_APPLIED,
        ),
        (
            TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
            True,
            RUNTIME_APPLY_STATUS_FAILED,
        ),
    ),
)
async def test_real_repository_and_runtime_adapter_preserve_receipt_and_execution(
    tmp_path, transaction_status: str, reconciliation: bool, expected_status: str
) -> None:
    path = tmp_path / "settings.json"
    persistence = SettingsVNextCanonicalPersistenceAdapter()
    initial = AppSettingsVNext()
    before = persistence.initialize(path, initial, reason="bootstrap", correlation_id=None)
    repository = ProductionCanonicalSettingsRepository(persistence, path)
    loaded = await repository.load_receipt()
    committed = await repository.save(
        SettingsCommitRequest(
            persistence.values_for(initial), loaded.revision, "openrouter_pkce", "corr"
        )
    )
    assert committed.receipt is not None

    execution = PostCommitRuntimeExecutionResult(
        TransactionResult(transaction_status, None, None),
        ("provider_activation",),
        "dashboard_retry_facts" if reconciliation else None,
        (),
        reconciliation,
    )
    host = SimpleNamespace(
        apply_committed_runtime=lambda **_kwargs: None,
    )

    async def apply_committed_runtime(**kwargs):  # noqa: ANN003, ANN202
        assert kwargs["before"].revision == before.revision
        assert kwargs["after"] is committed.receipt
        assert kwargs["cause"] == "pkce"
        assert kwargs["surface"] == "openrouter_pkce"
        return execution

    host.apply_committed_runtime = apply_committed_runtime
    adapter = ApplicationHostPkceRuntimeApply(host, repository, operational())
    result = await adapter.apply_runtime(RuntimeApplyRequest(committed.receipt))

    assert result.status == expected_status
    assert result.completed == ("provider_activation",)
    assert result.failed == ("dashboard_retry_facts" if reconciliation else None)
    assert result.reconciliation_required is reconciliation


@pytest.mark.asyncio
async def test_production_dashboard_projection_publishes_pkce_retry_facts() -> None:
    projection = ProductionUiProjection()
    published: list[DashboardRetryFactsDirective] = []
    projection.subscribe_dashboard(published.append)
    hub = SimpleNamespace(
        runtime_dashboard_facts=None,
        translation_enabled=False,
        clear_context=lambda: None,
        provider_state_snapshot=lambda: SimpleNamespace(llm=SimpleNamespace(provider=object())),
    )
    synchronization = ProductionRuntimeSynchronization(
        hub, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), projection
    )
    directive = DashboardRetryFactsDirective(
        "dashboard_retry_facts", False, True, True, False, True, False
    )

    result = await synchronization.synchronize_runtime(
        SimpleNamespace(),
        directive,
        before=None,
        after=SimpleNamespace(revision="r2"),
        operational=operational(),
    )

    assert result.status == RUNTIME_APPLY_STATUS_APPLIED
    assert hub.runtime_dashboard_facts.llm_available is True
    assert hub.runtime_dashboard_facts.llm_retry_pending is False
    assert hub.runtime_dashboard_facts.settings_revision == "r2"
    assert published == [hub.runtime_dashboard_facts]


@pytest.mark.asyncio
@pytest.mark.parametrize("activation_succeeds", (True, False))
async def test_pkce_surface_publishes_authoritative_post_activation_dashboard_facts(
    activation_succeeds: bool,
) -> None:
    projection = ProductionUiProjection()
    published: list[DashboardRetryFactsDirective] = []
    projection.subscribe_dashboard(published.append)
    old_provider = object()
    replacement = object()
    provider_cell = [old_provider]
    hub = SimpleNamespace(
        runtime_dashboard_facts=None,
        translation_enabled=False,
        llm=old_provider,
        clear_context=lambda: None,
        provider_state_snapshot=lambda: SimpleNamespace(
            llm=SimpleNamespace(provider=provider_cell[0])
        ),
    )
    synchronization = ProductionRuntimeSynchronization(
        hub, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), projection
    )

    class Activation:
        async def activate_providers(self, request, directive):  # noqa: ANN001, ANN201
            _ = (request, directive)
            if activation_succeeds:
                provider_cell[0] = replacement
                hub.llm = replacement
                return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)
            return RuntimeApplyResult(RUNTIME_APPLY_STATUS_FAILED, None, None)

    settings = AppSettingsVNext()
    before = SettingsVNextCanonicalPersistenceAdapter().receipt_for(
        settings, reason="before", correlation_id="corr"
    )
    after = SettingsVNextCanonicalPersistenceAdapter().receipt_for(
        settings, reason="openrouter_pkce", correlation_id="corr"
    )
    facts = operational()
    facts = RuntimeOperationalSnapshot(
        True,
        facts.self_stt_enabled,
        facts.self_stt_running,
        facts.self_stt_staged,
        facts.peer_stt_enabled,
        facts.peer_stt_running,
        facts.peer_stt_staged,
        False,
        True,
        facts.self_stt_available,
        facts.self_stt_retry_pending,
        facts.peer_stt_available,
        facts.peer_stt_retry_pending,
    )
    plan = PostCommitRuntimePlanBuilder(CanonicalRuntimeConfigResolver()).build(
        before=before,
        after=after,
        provenance=RuntimeMutationProvenance("openrouter_pkce", "pkce", "openrouter_pkce", "corr"),
        operational=facts,
    )

    result = await PostCommitRuntimeTransactionOwner(Activation(), synchronization).apply(plan)

    assert published[-1].llm_available is True
    assert published[-1].llm_retry_pending is False
    assert published[-1].translation_desired is True
    assert published[-1].translation_effective is activation_succeeds
    assert published[-1].settings_revision == after.revision
    assert provider_cell[0] is (replacement if activation_succeeds else old_provider)
    assert result.reconciliation_required is (not activation_succeeds)
    assert set(result.completed).isdisjoint(result.skipped)
    assert result.failed not in result.completed
    assert result.failed not in result.skipped
    if activation_succeeds:
        assert result.completed == (
            "provider_activation",
            "translation_policy",
            "dashboard_retry_facts",
        )
        assert hub.translation_enabled is True
    else:
        assert result.completed == ("dashboard_retry_facts",)
        assert result.skipped == ("translation_policy",)
