from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

import pytest

from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.runtime_apply import RuntimeApplyRequest
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.app.services.runtime_activation import RuntimeActivationOwner
from puripuly_heart.config.resolved import ResolvedApplicationRuntimeConfig
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.messages import RUNTIME_APPLY_STATUS_APPLIED, RUNTIME_APPLY_STATUS_FAILED


def _receipt(revision: str, concurrency: int) -> SettingsCommitReceipt:
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            translation=replace(settings.intent.translation, concurrency_limit=concurrency),
        ),
    )
    return SettingsCommitReceipt(settings, revision, "test", f"corr-{revision}")


@dataclass
class RecordingRuntime:
    fail_calls: set[int]

    def __post_init__(self) -> None:
        self.configs: list[ResolvedApplicationRuntimeConfig] = []
        self.requests: list[ResolvedRuntimeActivationRequest] = []
        self.closed: list[int] = []

    async def replace_runtime(self, request: ResolvedRuntimeActivationRequest) -> None:
        call = len(self.configs) + 1
        await asyncio.sleep(0)
        self.closed.append(call)
        self.requests.append(request)
        self.configs.append(request.config)
        if call in self.fail_calls:
            raise RuntimeError("replace failed")


@dataclass
class CommittedSettings:
    receipt: SettingsCommitReceipt

    async def load_receipt(self) -> SettingsCommitReceipt:
        return self.receipt


@pytest.mark.asyncio
async def test_owner_serializes_stale_and_same_revision_activation() -> None:
    runtime = RecordingRuntime(set())
    first = _receipt("r1", 2)
    second = _receipt("r2", 3)
    committed = CommittedSettings(second)
    owner = RuntimeActivationOwner(CanonicalRuntimeConfigResolver(), runtime, committed)

    first_result, second_result = await asyncio.gather(
        owner.apply_runtime(RuntimeApplyRequest(first)),
        owner.apply_runtime(RuntimeApplyRequest(second)),
    )
    stale_result = await owner.apply_runtime(RuntimeApplyRequest(first))
    same_result = await owner.apply_runtime(RuntimeApplyRequest(second))

    assert first_result.status == second_result.status == RUNTIME_APPLY_STATUS_APPLIED
    assert stale_result.status == same_result.status == RUNTIME_APPLY_STATUS_APPLIED
    assert owner.active_revision == "r2"
    assert len(runtime.configs) == 1
    assert runtime.configs[-1].llm.concurrency_limit == 3
    assert runtime.requests[-1].correlation_id == "corr-r2"


@pytest.mark.asyncio
async def test_activation_failure_compensates_without_changing_active_revision() -> None:
    runtime = RecordingRuntime({2})
    first = _receipt("r1", 2)
    second = _receipt("r2", 3)
    committed = CommittedSettings(first)
    owner = RuntimeActivationOwner(CanonicalRuntimeConfigResolver(), runtime, committed)
    assert (
        await owner.apply_runtime(RuntimeApplyRequest(first))
    ).status == RUNTIME_APPLY_STATUS_APPLIED

    committed.receipt = second
    result = await owner.apply_runtime(RuntimeApplyRequest(second))

    assert result.status == RUNTIME_APPLY_STATUS_FAILED
    assert owner.active_revision == "r1"
    assert [config.llm.concurrency_limit for config in runtime.configs] == [2, 3, 2]
    assert runtime.closed == [1, 2, 3]


@pytest.mark.asyncio
async def test_failed_compensation_is_reported_and_keeps_last_active_metadata() -> None:
    runtime = RecordingRuntime({2, 3})
    first = _receipt("r1", 2)
    second = _receipt("r2", 3)
    committed = CommittedSettings(first)
    owner = RuntimeActivationOwner(CanonicalRuntimeConfigResolver(), runtime, committed)
    await owner.apply_runtime(RuntimeApplyRequest(first))

    committed.receipt = second
    result = await owner.apply_runtime(RuntimeApplyRequest(second))

    assert result.status == RUNTIME_APPLY_STATUS_FAILED
    assert result.diagnostics is not None
    assert result.diagnostics.code == "runtime_activation_compensation_failed"
    assert owner.active_revision is None
    runtime.fail_calls.clear()
    retry = await owner.apply_runtime(RuntimeApplyRequest(second))
    assert retry.status == RUNTIME_APPLY_STATUS_APPLIED
    assert owner.active_revision == "r2"


@pytest.mark.asyncio
async def test_commit_change_during_apply_activates_latest_receipt() -> None:
    first = _receipt("r1", 2)
    second = _receipt("r2", 9)
    committed = CommittedSettings(first)

    class CommitChangingRuntime(RecordingRuntime):
        async def replace_runtime(self, request: ResolvedRuntimeActivationRequest) -> None:
            await super().replace_runtime(request)
            if len(self.requests) == 1:
                committed.receipt = second

    runtime = CommitChangingRuntime(set())
    owner = RuntimeActivationOwner(CanonicalRuntimeConfigResolver(), runtime, committed)

    result = await owner.apply_runtime(RuntimeApplyRequest(first))

    assert result.status == RUNTIME_APPLY_STATUS_APPLIED
    assert owner.active_revision == "r2"
    assert [config.llm.concurrency_limit for config in runtime.configs] == [2, 9]


@pytest.mark.asyncio
async def test_commit_changes_during_later_replacements_converge_to_latest() -> None:
    receipts = [_receipt("r1", 1), _receipt("r2", 2), _receipt("r3", 3)]
    committed = CommittedSettings(receipts[0])

    class MultiCommitRuntime(RecordingRuntime):
        async def replace_runtime(self, request: ResolvedRuntimeActivationRequest) -> None:
            await super().replace_runtime(request)
            if len(self.requests) < len(receipts):
                committed.receipt = receipts[len(self.requests)]

    runtime = MultiCommitRuntime(set())
    owner = RuntimeActivationOwner(CanonicalRuntimeConfigResolver(), runtime, committed)

    result = await owner.apply_runtime(RuntimeApplyRequest(receipts[0]))

    assert result.status == RUNTIME_APPLY_STATUS_APPLIED
    assert owner.active_revision == "r3"
    assert [config.llm.concurrency_limit for config in runtime.configs] == [1, 2, 3]


@pytest.mark.asyncio
async def test_bounded_commit_churn_returns_safe_degraded_result() -> None:
    receipts = [_receipt(f"r{index}", index) for index in range(1, 5)]
    committed = CommittedSettings(receipts[0])

    class ChurningRuntime(RecordingRuntime):
        async def replace_runtime(self, request: ResolvedRuntimeActivationRequest) -> None:
            await super().replace_runtime(request)
            committed.receipt = receipts[min(len(self.requests), len(receipts) - 1)]

    runtime = ChurningRuntime(set())
    owner = RuntimeActivationOwner(
        CanonicalRuntimeConfigResolver(), runtime, committed, max_convergence_attempts=2
    )

    result = await owner.apply_runtime(RuntimeApplyRequest(receipts[0]))

    assert result.status == RUNTIME_APPLY_STATUS_FAILED
    assert result.diagnostics is not None
    assert result.diagnostics.code == "runtime_activation_commit_churn"
    assert owner.active_revision == "r2"


def test_canonical_resolver_keeps_receipt_and_resolved_config_separate() -> None:
    receipt = _receipt("r1", 7)
    resolved = CanonicalRuntimeConfigResolver().resolve(receipt)

    assert resolved.llm.concurrency_limit == 7
    assert resolved.self_stt.source_language == receipt.envelope.intent.languages.source_language
    assert resolved is not receipt.envelope


def test_canonical_resolver_maps_nondefault_runtime_intent_without_secrets() -> None:
    settings = AppSettingsVNext()
    translation = replace(
        settings.intent.translation,
        model="gemma4_31b_cerebras",
        connection="official_byok",
        qwen=replace(settings.intent.translation.qwen, region="singapore"),
        cerebras=replace(settings.intent.translation.cerebras, llm_model="cerebras-custom"),
    )
    stt = replace(
        settings.intent.stt,
        provider="qwen_asr",
        drain_timeout_s=4.5,
        low_latency_mode=False,
        low_latency_merge_gap_ms=321,
        low_latency_spec_retry_max=4,
        soniox=replace(
            settings.intent.stt.soniox,
            model="soniox-custom",
            endpoint="wss://example.invalid/stt",
        ),
    )
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            translation=translation,
            stt=stt,
            peer_stt=replace(settings.intent.peer_stt, provider="soniox"),
            languages=replace(
                settings.intent.languages,
                peer_source_mode="soniox_auto",
                peer_expected_languages=["zh-CN", "zh-TW", "zh-CN", "ja"],
            ),
            audio=replace(settings.intent.audio, ring_buffer_ms=777),
            overlay=replace(
                settings.intent.overlay,
                calibration=replace(settings.intent.overlay.calibration, distance=2.5),
                desktop_flet=replace(settings.intent.overlay.desktop_flet, size_preset="large"),
            ),
        ),
    )
    receipt = SettingsCommitReceipt(settings, "r-nondefault", "test", "corr-nondefault")

    resolved = CanonicalRuntimeConfigResolver().resolve(receipt)

    assert resolved.llm.primary.model == "cerebras-custom"
    assert resolved.self_stt.region == "singapore"
    assert resolved.peer_stt.provider == "soniox"
    assert resolved.peer_stt.ring_buffer_ms == 777
    assert resolved.peer_stt.drain_timeout_s == 4.5
    assert resolved.peer_stt.low_latency_enabled is False
    assert resolved.peer_stt.model == "soniox-custom"
    assert resolved.peer_stt.provider_options["enable_language_identification"] is True
    assert resolved.peer_stt.provider_options["language_hints"] == ("zh", "ja")
    assert resolved.overlay.calibration["distance"] == 2.5
    assert resolved.overlay.desktop_overlay_options["size_preset"] == "large"
    assert "sk-test" not in repr(resolved).lower()
    assert "bearer " not in repr(resolved).lower()
