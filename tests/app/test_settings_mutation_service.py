from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import get_type_hints

import pytest

from puripuly_heart.app.ports import runtime_apply, settings_repository
from puripuly_heart.core import messages

SERVICE_MODULE = "puripuly_heart.app.services.settings_mutation"

FORBIDDEN_SERVICE_IMPORT_PREFIXES = (
    "flet",
    "keyring",
    "puripuly_heart.app.adapters",
    "puripuly_heart.app.wiring",
    "puripuly_heart.config.settings",
    "puripuly_heart.config.settings_vnext",
    "puripuly_heart.config.runtime_resolution",
    "puripuly_heart.core.managed_openrouter_broker_client",
    "puripuly_heart.core.orchestrator",
    "puripuly_heart.core.runtime",
    "puripuly_heart.core.storage",
    "puripuly_heart.providers",
    "puripuly_heart.ui",
)


@dataclass(slots=True)
class RecordingSettingsRepository:
    result: settings_repository.SettingsCommitResult
    saved_requests: list[settings_repository.SettingsCommitRequest] = field(default_factory=list)

    async def load(self) -> settings_repository.SettingsSnapshot:
        raise AssertionError("SettingsMutationService should not load in these scenarios")

    async def save(
        self,
        request: settings_repository.SettingsCommitRequest,
    ) -> settings_repository.SettingsCommitResult:
        self.saved_requests.append(request)
        return self.result


@dataclass(slots=True)
class RecordingRuntimeApply:
    result: messages.RuntimeApplyResult
    requests: list[runtime_apply.RuntimeApplyRequest] = field(default_factory=list)

    async def apply_runtime(
        self,
        request: runtime_apply.RuntimeApplyRequest,
    ) -> messages.RuntimeApplyResult:
        self.requests.append(request)
        return self.result


@dataclass(slots=True)
class RaisingRuntimeApply:
    exception: Exception
    requests: list[runtime_apply.RuntimeApplyRequest] = field(default_factory=list)

    async def apply_runtime(
        self,
        request: runtime_apply.RuntimeApplyRequest,
    ) -> messages.RuntimeApplyResult:
        self.requests.append(request)
        raise self.exception


@dataclass(slots=True)
class RecordingSettingsSnapshotPublisher:
    publications: list[tuple[settings_repository.SettingsSnapshot, str | None]] = field(
        default_factory=list
    )

    async def publish_settings_snapshot(
        self,
        snapshot: settings_repository.SettingsSnapshot,
        *,
        correlation_id: str | None,
    ) -> None:
        self.publications.append((snapshot, correlation_id))


@dataclass(slots=True)
class RaisingSettingsSnapshotPublisher:
    publications: list[tuple[settings_repository.SettingsSnapshot, str | None]] = field(
        default_factory=list
    )

    async def publish_settings_snapshot(
        self,
        snapshot: settings_repository.SettingsSnapshot,
        *,
        correlation_id: str | None,
    ) -> None:
        self.publications.append((snapshot, correlation_id))
        raise RuntimeError("raw snapshot publisher failure should not leak")


@dataclass(slots=True)
class RecordingRuntimeResultPublisher:
    publications: list[tuple[messages.RuntimeApplyResult, str | None]] = field(default_factory=list)

    async def publish_runtime_apply_result(
        self,
        result: messages.RuntimeApplyResult,
        *,
        correlation_id: str | None,
    ) -> None:
        self.publications.append((result, correlation_id))


@dataclass(slots=True)
class RaisingRuntimeResultPublisher:
    publications: list[tuple[messages.RuntimeApplyResult, str | None]] = field(default_factory=list)

    async def publish_runtime_apply_result(
        self,
        result: messages.RuntimeApplyResult,
        *,
        correlation_id: str | None,
    ) -> None:
        self.publications.append((result, correlation_id))
        raise RuntimeError("raw runtime result publisher failure should not leak")


@dataclass(slots=True)
class RecordingSettingsMutationValidator:
    result: object
    requests: list[object] = field(default_factory=list)

    async def validate(self, request: object) -> object:
        self.requests.append(request)
        return self.result


def _service_module():
    return importlib.import_module(SERVICE_MODULE)


def _message(
    key: str,
    *,
    severity: messages.Severity = messages.SEVERITY_ERROR,
) -> messages.UserMessageRef:
    return messages.UserMessageRef(
        key=key,
        params={"phase": "settings_mutation"},
        severity=severity,
    )


def _diagnostics(code: str, *, operation: str) -> messages.ErrorDiagnostics:
    return messages.ErrorDiagnostics(
        component="settings_mutation",
        operation=operation,
        code=code,
        category=messages.DIAGNOSTIC_CATEGORY_TRANSACTION,
        visibility=messages.DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=messages.CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=None,
        fields={"phase": operation},
    )


def _service(
    *,
    repository: RecordingSettingsRepository,
    runtime: RecordingRuntimeApply | RaisingRuntimeApply,
    snapshot_publisher: (
        RecordingSettingsSnapshotPublisher | RaisingSettingsSnapshotPublisher | None
    ) = None,
    runtime_result_publisher: (
        RecordingRuntimeResultPublisher | RaisingRuntimeResultPublisher | None
    ) = None,
    validator: RecordingSettingsMutationValidator | None = None,
):
    settings_mutation = _service_module()
    if validator is None:
        validator = RecordingSettingsMutationValidator(
            settings_mutation.SettingsMutationValidationResult(
                succeeded=True,
                message=None,
                diagnostics=None,
            )
        )
    kwargs = {
        "settings_repository": repository,
        "runtime_apply": runtime,
        "snapshot_publisher": snapshot_publisher,
        "runtime_result_publisher": runtime_result_publisher,
        "validator": validator,
    }
    return settings_mutation.SettingsMutationService(**kwargs)


def test_settings_mutation_request_is_frozen_slotted_and_deep_freezes_payload() -> None:
    settings_mutation = _service_module()

    values = {
        "provider": {
            "aliases": ["openrouter"],
            "options": {"streaming": True},
        }
    }
    request = settings_mutation.SettingsMutationRequest(
        values=values,
        expected_revision="settings-r1",
        reason="user_patch",
        correlation_id="corr-1",
    )

    values["provider"]["aliases"].append("qwen")
    values["provider"]["options"]["streaming"] = False

    assert is_dataclass(request)
    assert not hasattr(request, "__dict__")
    assert {field.name for field in fields(request)} == {
        "values",
        "expected_revision",
        "reason",
        "correlation_id",
    }

    with pytest.raises(FrozenInstanceError):
        request.reason = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        request.values["provider"] = "qwen"  # type: ignore[index]

    provider = request.values["provider"]
    assert isinstance(provider, Mapping)
    assert provider["aliases"] == ("openrouter",)
    assert isinstance(provider["options"], Mapping)
    assert provider["options"]["streaming"] is True

    hints = get_type_hints(settings_mutation.SettingsMutationRequest)
    assert hints["values"] == Mapping[str, object]
    assert hints["expected_revision"] == str | None
    assert hints["reason"] == str | None
    assert hints["correlation_id"] == str | None


def test_settings_mutation_validation_contract_is_frozen_slotted_async_protocol() -> None:
    settings_mutation = _service_module()
    validation_message = _message("settings.validation.failed")
    validation_diagnostics = _diagnostics("validation_failed", operation="validate")

    result = settings_mutation.SettingsMutationValidationResult(
        succeeded=False,
        message=validation_message,
        diagnostics=validation_diagnostics,
    )

    assert is_dataclass(result)
    assert not hasattr(result, "__dict__")
    assert {field.name for field in fields(result)} == {
        "succeeded",
        "message",
        "diagnostics",
    }

    with pytest.raises(FrozenInstanceError):
        result.succeeded = True  # type: ignore[misc]

    result_hints = get_type_hints(settings_mutation.SettingsMutationValidationResult)
    assert result_hints["succeeded"] is bool
    assert result_hints["message"] == messages.UserMessageRef | None
    assert result_hints["diagnostics"] == messages.ErrorDiagnostics | None

    validator = settings_mutation.SettingsMutationValidator
    assert getattr(validator, "_is_protocol", False)
    assert inspect.iscoroutinefunction(validator.validate)

    validator_hints = get_type_hints(validator.validate)
    assert validator_hints["request"] == settings_mutation.SettingsMutationRequest
    assert validator_hints["return"] == settings_mutation.SettingsMutationValidationResult


def test_settings_mutation_service_requires_validation_owner_dependency() -> None:
    settings_mutation = _service_module()

    signature = inspect.signature(settings_mutation.SettingsMutationService)

    assert "validator" in signature.parameters
    assert signature.parameters["validator"].default is inspect.Signature.empty


@pytest.mark.asyncio
async def test_validation_failure_returns_transaction_failure_without_save_runtime_or_publications() -> (
    None
):
    settings_mutation = _service_module()
    validation_message = _message("settings.validation.failed")
    validation_diagnostics = _diagnostics("validation_failed", operation="validate")
    validator = RecordingSettingsMutationValidator(
        settings_mutation.SettingsMutationValidationResult(
            succeeded=False,
            message=validation_message,
            diagnostics=validation_diagnostics,
        )
    )
    committed_snapshot = settings_repository.SettingsSnapshot(
        values={"provider": "openrouter"},
        revision="settings-r2",
    )
    repository = RecordingSettingsRepository(
        settings_repository.SettingsCommitResult(
            succeeded=True,
            snapshot=committed_snapshot,
            message=None,
            diagnostics=None,
        )
    )
    runtime = RecordingRuntimeApply(
        messages.RuntimeApplyResult(
            status=messages.RUNTIME_APPLY_STATUS_APPLIED,
            message=None,
            diagnostics=None,
        )
    )
    snapshot_publisher = RecordingSettingsSnapshotPublisher()
    runtime_result_publisher = RecordingRuntimeResultPublisher()
    request = settings_mutation.SettingsMutationRequest(
        values={"provider": "openrouter"},
        expected_revision="settings-r1",
        reason="user_patch",
        correlation_id="corr-validation",
    )

    result = await _service(
        repository=repository,
        runtime=runtime,
        snapshot_publisher=snapshot_publisher,
        runtime_result_publisher=runtime_result_publisher,
        validator=validator,
    ).mutate(request)

    assert result == messages.TransactionResult(
        status=messages.TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED,
        message=validation_message,
        diagnostics=validation_diagnostics,
    )
    assert validator.requests == [request]
    assert repository.saved_requests == []
    assert runtime.requests == []
    assert snapshot_publisher.publications == []
    assert runtime_result_publisher.publications == []


@pytest.mark.asyncio
async def test_commit_failure_returns_transaction_failure_without_runtime_or_publications() -> None:
    settings_mutation = _service_module()
    commit_message = _message("settings.commit.failed")
    commit_diagnostics = _diagnostics("commit_failed", operation="save")
    repository = RecordingSettingsRepository(
        settings_repository.SettingsCommitResult(
            succeeded=False,
            snapshot=None,
            message=commit_message,
            diagnostics=commit_diagnostics,
        )
    )
    runtime = RecordingRuntimeApply(
        messages.RuntimeApplyResult(
            status=messages.RUNTIME_APPLY_STATUS_APPLIED,
            message=None,
            diagnostics=None,
        )
    )
    snapshot_publisher = RecordingSettingsSnapshotPublisher()
    runtime_result_publisher = RecordingRuntimeResultPublisher()

    result = await _service(
        repository=repository,
        runtime=runtime,
        snapshot_publisher=snapshot_publisher,
        runtime_result_publisher=runtime_result_publisher,
    ).mutate(
        settings_mutation.SettingsMutationRequest(
            values={"provider": "openrouter"},
            expected_revision="settings-r1",
            reason="user_patch",
            correlation_id="corr-1",
        )
    )

    assert result == messages.TransactionResult(
        status=messages.TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED,
        message=commit_message,
        diagnostics=commit_diagnostics,
    )
    assert len(repository.saved_requests) == 1
    assert repository.saved_requests[0].expected_revision == "settings-r1"
    assert repository.saved_requests[0].reason == "user_patch"
    assert runtime.requests == []
    assert snapshot_publisher.publications == []
    assert runtime_result_publisher.publications == []


@pytest.mark.asyncio
async def test_commit_success_with_runtime_applied_publishes_snapshot_and_runtime_result() -> None:
    settings_mutation = _service_module()
    committed_snapshot = settings_repository.SettingsSnapshot(
        values={"provider": {"aliases": ["committed"]}},
        revision="settings-r2",
    )
    repository = RecordingSettingsRepository(
        settings_repository.SettingsCommitResult(
            succeeded=True,
            snapshot=committed_snapshot,
            message=_message("settings.commit.applied", severity=messages.SEVERITY_INFO),
            diagnostics=None,
        )
    )
    runtime_message = _message("runtime.apply.applied", severity=messages.SEVERITY_INFO)
    runtime_result = messages.RuntimeApplyResult(
        status=messages.RUNTIME_APPLY_STATUS_APPLIED,
        message=runtime_message,
        diagnostics=None,
    )
    runtime = RecordingRuntimeApply(runtime_result)
    snapshot_publisher = RecordingSettingsSnapshotPublisher()
    runtime_result_publisher = RecordingRuntimeResultPublisher()

    result = await _service(
        repository=repository,
        runtime=runtime,
        snapshot_publisher=snapshot_publisher,
        runtime_result_publisher=runtime_result_publisher,
    ).mutate(
        settings_mutation.SettingsMutationRequest(
            values={"provider": {"aliases": ["draft"]}},
            expected_revision="settings-r1",
            reason="user_patch",
            correlation_id="corr-2",
        )
    )

    assert result == messages.TransactionResult(
        status=messages.TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
        message=runtime_message,
        diagnostics=None,
    )
    assert len(repository.saved_requests) == 1
    assert repository.saved_requests[0].values["provider"]["aliases"] == ("draft",)
    assert repository.saved_requests[0].expected_revision == "settings-r1"
    assert repository.saved_requests[0].reason == "user_patch"
    assert snapshot_publisher.publications == [(committed_snapshot, "corr-2")]
    assert runtime.requests == [
        runtime_apply.RuntimeApplyRequest(
            settings_values=committed_snapshot.values,
            reason="user_patch",
            correlation_id="corr-2",
        )
    ]
    assert runtime_result_publisher.publications == [(runtime_result, "corr-2")]


@pytest.mark.parametrize(
    "runtime_status",
    [messages.RUNTIME_APPLY_STATUS_DEGRADED, messages.RUNTIME_APPLY_STATUS_FAILED],
)
@pytest.mark.asyncio
async def test_commit_success_with_runtime_degraded_or_failed_returns_degraded_transaction(
    runtime_status: messages.RuntimeApplyStatus,
) -> None:
    settings_mutation = _service_module()
    committed_snapshot = settings_repository.SettingsSnapshot(
        values={"provider": "openrouter"},
        revision="settings-r2",
    )
    repository = RecordingSettingsRepository(
        settings_repository.SettingsCommitResult(
            succeeded=True,
            snapshot=committed_snapshot,
            message=None,
            diagnostics=None,
        )
    )
    runtime_message = _message("runtime.apply.degraded", severity=messages.SEVERITY_WARNING)
    runtime_diagnostics = _diagnostics(f"runtime_{runtime_status}", operation="apply")
    runtime_result = messages.RuntimeApplyResult(
        status=runtime_status,
        message=runtime_message,
        diagnostics=runtime_diagnostics,
    )
    runtime = RecordingRuntimeApply(runtime_result)
    snapshot_publisher = RecordingSettingsSnapshotPublisher()
    runtime_result_publisher = RecordingRuntimeResultPublisher()

    result = await _service(
        repository=repository,
        runtime=runtime,
        snapshot_publisher=snapshot_publisher,
        runtime_result_publisher=runtime_result_publisher,
    ).mutate(
        settings_mutation.SettingsMutationRequest(
            values={"provider": "openrouter"},
            expected_revision=None,
            reason="user_patch",
            correlation_id="corr-3",
        )
    )

    assert result == messages.TransactionResult(
        status=messages.TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
        message=runtime_message,
        diagnostics=runtime_diagnostics,
    )
    assert runtime.requests == [
        runtime_apply.RuntimeApplyRequest(
            settings_values=committed_snapshot.values,
            reason="user_patch",
            correlation_id="corr-3",
        )
    ]
    assert snapshot_publisher.publications == [(committed_snapshot, "corr-3")]
    assert runtime_result_publisher.publications == [(runtime_result, "corr-3")]


@pytest.mark.asyncio
async def test_snapshot_publisher_failure_still_applies_runtime_and_returns_runtime_result() -> (
    None
):
    settings_mutation = _service_module()
    committed_snapshot = settings_repository.SettingsSnapshot(
        values={"provider": "openrouter"},
        revision="settings-r2",
    )
    repository = RecordingSettingsRepository(
        settings_repository.SettingsCommitResult(
            succeeded=True,
            snapshot=committed_snapshot,
            message=_message("settings.commit.applied", severity=messages.SEVERITY_INFO),
            diagnostics=None,
        )
    )
    runtime_message = _message("runtime.apply.applied", severity=messages.SEVERITY_INFO)
    runtime_diagnostics = _diagnostics("runtime_applied", operation="apply")
    runtime_result = messages.RuntimeApplyResult(
        status=messages.RUNTIME_APPLY_STATUS_APPLIED,
        message=runtime_message,
        diagnostics=runtime_diagnostics,
    )
    runtime = RecordingRuntimeApply(runtime_result)
    snapshot_publisher = RaisingSettingsSnapshotPublisher()
    runtime_result_publisher = RecordingRuntimeResultPublisher()

    result = await _service(
        repository=repository,
        runtime=runtime,
        snapshot_publisher=snapshot_publisher,
        runtime_result_publisher=runtime_result_publisher,
    ).mutate(
        settings_mutation.SettingsMutationRequest(
            values={"provider": "draft"},
            expected_revision="settings-r1",
            reason="user_patch",
            correlation_id="corr-snapshot-publisher",
        )
    )

    assert result == messages.TransactionResult(
        status=messages.TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
        message=runtime_message,
        diagnostics=runtime_diagnostics,
    )
    assert snapshot_publisher.publications == [(committed_snapshot, "corr-snapshot-publisher")]
    assert runtime.requests == [
        runtime_apply.RuntimeApplyRequest(
            settings_values=committed_snapshot.values,
            reason="user_patch",
            correlation_id="corr-snapshot-publisher",
        )
    ]
    assert runtime_result_publisher.publications == [(runtime_result, "corr-snapshot-publisher")]


@pytest.mark.asyncio
async def test_runtime_result_publisher_failure_returns_known_runtime_result() -> None:
    settings_mutation = _service_module()
    committed_snapshot = settings_repository.SettingsSnapshot(
        values={"provider": "openrouter"},
        revision="settings-r2",
    )
    repository = RecordingSettingsRepository(
        settings_repository.SettingsCommitResult(
            succeeded=True,
            snapshot=committed_snapshot,
            message=None,
            diagnostics=None,
        )
    )
    runtime_message = _message("runtime.apply.degraded", severity=messages.SEVERITY_WARNING)
    runtime_diagnostics = _diagnostics("runtime_degraded", operation="apply")
    runtime_result = messages.RuntimeApplyResult(
        status=messages.RUNTIME_APPLY_STATUS_DEGRADED,
        message=runtime_message,
        diagnostics=runtime_diagnostics,
    )
    runtime = RecordingRuntimeApply(runtime_result)
    snapshot_publisher = RecordingSettingsSnapshotPublisher()
    runtime_result_publisher = RaisingRuntimeResultPublisher()

    result = await _service(
        repository=repository,
        runtime=runtime,
        snapshot_publisher=snapshot_publisher,
        runtime_result_publisher=runtime_result_publisher,
    ).mutate(
        settings_mutation.SettingsMutationRequest(
            values={"provider": "draft"},
            expected_revision="settings-r1",
            reason="user_patch",
            correlation_id="corr-runtime-publisher",
        )
    )

    assert result == messages.TransactionResult(
        status=messages.TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
        message=runtime_message,
        diagnostics=runtime_diagnostics,
    )
    assert snapshot_publisher.publications == [(committed_snapshot, "corr-runtime-publisher")]
    assert runtime.requests == [
        runtime_apply.RuntimeApplyRequest(
            settings_values=committed_snapshot.values,
            reason="user_patch",
            correlation_id="corr-runtime-publisher",
        )
    ]
    assert runtime_result_publisher.publications == [(runtime_result, "corr-runtime-publisher")]


@pytest.mark.asyncio
async def test_runtime_apply_exception_returns_controlled_degraded_result_without_runtime_publish() -> (
    None
):
    settings_mutation = _service_module()
    committed_snapshot = settings_repository.SettingsSnapshot(
        values={"provider": "openrouter"},
        revision="settings-r2",
    )
    repository = RecordingSettingsRepository(
        settings_repository.SettingsCommitResult(
            succeeded=True,
            snapshot=committed_snapshot,
            message=None,
            diagnostics=None,
        )
    )
    runtime = RaisingRuntimeApply(RuntimeError("raw runtime secret-token failure"))
    snapshot_publisher = RecordingSettingsSnapshotPublisher()
    runtime_result_publisher = RecordingRuntimeResultPublisher()

    result = await _service(
        repository=repository,
        runtime=runtime,
        snapshot_publisher=snapshot_publisher,
        runtime_result_publisher=runtime_result_publisher,
    ).mutate(
        settings_mutation.SettingsMutationRequest(
            values={"provider": "draft"},
            expected_revision="settings-r1",
            reason="user_patch",
            correlation_id="corr-runtime-exception",
        )
    )

    assert result == messages.TransactionResult(
        status=messages.TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
        message=messages.UserMessageRef(
            key="settings.mutation.runtime_apply_failed",
            params={"phase": "runtime_apply"},
            severity=messages.SEVERITY_WARNING,
        ),
        diagnostics=messages.ErrorDiagnostics(
            component="settings_mutation",
            operation="runtime_apply",
            code="runtime_apply_exception",
            category=messages.DIAGNOSTIC_CATEGORY_LIFECYCLE,
            visibility=messages.DIAGNOSTIC_VISIBILITY_BASIC,
            content_policy=messages.CONTENT_POLICY_METADATA_ONLY,
            status_code=None,
            retry_after_ms=None,
            fields={"phase": "runtime_apply"},
        ),
    )
    assert "raw runtime secret-token failure" not in repr(result)
    assert snapshot_publisher.publications == [(committed_snapshot, "corr-runtime-exception")]
    assert runtime.requests == [
        runtime_apply.RuntimeApplyRequest(
            settings_values=committed_snapshot.values,
            reason="user_patch",
            correlation_id="corr-runtime-exception",
        )
    ]
    assert runtime_result_publisher.publications == []


@pytest.mark.asyncio
async def test_commit_success_message_and_diagnostics_are_runtime_fallbacks() -> None:
    settings_mutation = _service_module()
    committed_snapshot = settings_repository.SettingsSnapshot(
        values={"provider": "openrouter"},
        revision="settings-r2",
    )
    commit_message = _message("settings.commit.applied", severity=messages.SEVERITY_INFO)
    commit_diagnostics = _diagnostics("settings_commit_applied", operation="save")
    repository = RecordingSettingsRepository(
        settings_repository.SettingsCommitResult(
            succeeded=True,
            snapshot=committed_snapshot,
            message=commit_message,
            diagnostics=commit_diagnostics,
        )
    )
    runtime = RecordingRuntimeApply(
        messages.RuntimeApplyResult(
            status=messages.RUNTIME_APPLY_STATUS_APPLIED,
            message=None,
            diagnostics=None,
        )
    )

    result = await _service(repository=repository, runtime=runtime).mutate(
        settings_mutation.SettingsMutationRequest(
            values={"provider": "draft"},
            expected_revision="settings-r1",
            reason="user_patch",
            correlation_id="corr-fallback",
        )
    )

    assert result == messages.TransactionResult(
        status=messages.TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
        message=commit_message,
        diagnostics=commit_diagnostics,
    )
    assert runtime.requests == [
        runtime_apply.RuntimeApplyRequest(
            settings_values=committed_snapshot.values,
            reason="user_patch",
            correlation_id="corr-fallback",
        )
    ]


@pytest.mark.asyncio
async def test_runtime_message_and_diagnostics_take_precedence_over_commit_values() -> None:
    settings_mutation = _service_module()
    committed_snapshot = settings_repository.SettingsSnapshot(
        values={"provider": "openrouter"},
        revision="settings-r2",
    )
    repository = RecordingSettingsRepository(
        settings_repository.SettingsCommitResult(
            succeeded=True,
            snapshot=committed_snapshot,
            message=_message("settings.commit.applied", severity=messages.SEVERITY_INFO),
            diagnostics=_diagnostics("settings_commit_applied", operation="save"),
        )
    )
    runtime_message = _message("runtime.apply.degraded", severity=messages.SEVERITY_WARNING)
    runtime_diagnostics = _diagnostics("runtime_degraded", operation="apply")
    runtime = RecordingRuntimeApply(
        messages.RuntimeApplyResult(
            status=messages.RUNTIME_APPLY_STATUS_DEGRADED,
            message=runtime_message,
            diagnostics=runtime_diagnostics,
        )
    )

    result = await _service(repository=repository, runtime=runtime).mutate(
        settings_mutation.SettingsMutationRequest(
            values={"provider": "draft"},
            expected_revision="settings-r1",
            reason="user_patch",
            correlation_id="corr-runtime-precedence",
        )
    )

    assert result == messages.TransactionResult(
        status=messages.TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
        message=runtime_message,
        diagnostics=runtime_diagnostics,
    )


def test_order21_translation_provider_patch_records_initial_covered_surface_list() -> None:
    settings_mutation = _service_module()

    assert set(settings_mutation.ORDER21_TRANSLATION_PROVIDER_SETTINGS_PATHS) == {
        "translation.model",
        "translation.connection",
        "translation.connection_history",
        "provider.llm",
        "gemini.llm_model",
        "openrouter.llm_model",
        "openrouter.routing_mode",
        "openrouter.provider_routing",
        "openrouter.selected_source",
        "openrouter.selection_alias",
        "openrouter.fallback_selection_alias",
        "openrouter.broker_base_url",
        "qwen.llm_model",
        "qwen.region",
        "deepseek.llm_model",
        "local_llm.backend",
        "local_llm.base_url",
        "local_llm.model",
        "local_llm.extra_body",
        "llm.concurrency_limit",
    }


def test_order22_stt_language_audio_patch_records_initial_covered_surface_list() -> None:
    settings_mutation = _service_module()

    assert set(settings_mutation.ORDER22_STT_LANGUAGE_AUDIO_SETTINGS_PATHS) == {
        "provider.stt",
        "provider.peer_stt",
        "languages.source_language",
        "languages.target_language",
        "languages.peer_source_language",
        "languages.peer_target_language",
        "languages.recent_source_languages",
        "languages.recent_target_languages",
        "audio.internal_sample_rate_hz",
        "audio.internal_channels",
        "audio.ring_buffer_ms",
        "audio.input_host_api",
        "audio.input_device",
        "desktop_audio.output_device",
        "desktop_audio.vad_speech_threshold",
        "desktop_audio.vad_hangover_ms",
        "desktop_audio.vad_pre_roll_ms",
        "stt.drain_timeout_s",
        "stt.vad_speech_threshold",
        "stt.low_latency_mode",
        "stt.low_latency_vad_hangover_ms",
        "stt.low_latency_merge_gap_ms",
        "stt.low_latency_spec_retry_max",
        "stt.custom_vocabulary_enabled",
        "stt.custom_terms",
        "deepgram_stt.model",
        "qwen_asr_stt.model",
        "soniox_stt.model",
        "soniox_stt.endpoint",
        "soniox_stt.keepalive_interval_s",
        "soniox_stt.trailing_silence_ms",
    }


def test_order23_overlay_osc_output_patch_records_initial_covered_surface_list() -> None:
    settings_mutation = _service_module()

    assert set(settings_mutation.ORDER23_OVERLAY_OSC_OUTPUT_SETTINGS_PATHS) == {
        "overlay.target",
        "overlay.show_translation",
        "overlay.show_peer_original",
        "overlay.calibration.anchor",
        "overlay.calibration.offset_x",
        "overlay.calibration.offset_y",
        "overlay.calibration.distance",
        "overlay.calibration.text_scale",
        "overlay.calibration.background_alpha",
        "overlay.desktop_flet.size_preset",
        "overlay.desktop_flet.position.x",
        "overlay.desktop_flet.position.y",
        "overlay.desktop_flet.visual.background_alpha",
        "osc.host",
        "osc.port",
        "osc.chatbox_address",
        "osc.chatbox_send",
        "osc.chatbox_clear",
        "osc.chatbox_max_chars",
        "osc.vrc_mic_intercept",
        "osc.chatbox_include_source",
    }


def test_order24_ui_prompt_clipboard_state_patch_records_initial_covered_surface_list() -> None:
    settings_mutation = _service_module()

    assert set(settings_mutation.ORDER24_UI_PROMPT_CLIPBOARD_STATE_SETTINGS_PATHS) == {
        "secrets.backend",
        "secrets.encrypted_file_path",
        "ui.locale",
        "ui.peer_translation_eula_accepted",
        "ui.integrated_context_enabled",
        "ui.integrated_context_bootstrapped",
        "ui.clipboard_auto_translate_enabled",
        "ui.github_star_prompt_clicked",
        "ui.github_star_prompt_last_shown_at",
        "ui.github_star_prompt_show_count",
        "ui.github_star_prompt_translation_success_observed",
        "ui.github_star_prompt_eligible_launch_count",
        "system_prompt",
    }


def test_nondurable_order22_compatibility_fields_are_not_covered() -> None:
    settings_mutation = _service_module()

    assert {
        "qwen_asr_stt.endpoint",
        "peer_qwen_asr_stt.model",
        "peer_qwen_asr_stt.region",
        "peer_soniox_stt.model",
        "peer_soniox_stt.endpoint",
        "peer_soniox_stt.keepalive_interval_s",
        "peer_soniox_stt.trailing_silence_ms",
    }.isdisjoint(settings_mutation.ORDER22_STT_LANGUAGE_AUDIO_SETTINGS_PATHS)


def test_runtime_only_and_nondurable_order23_fields_are_not_covered() -> None:
    settings_mutation = _service_module()

    assert {
        "ui.overlay_enabled",
        "ui.peer_translation_enabled",
        "active_chatbox_channel",
        "overlay.desktop_flet.locked",
        "overlay.desktop_flet.bounds",
        "overlay.desktop_flet.visual.text_scale",
        "overlay.desktop_flet.visual.outline_width",
        "desktop_audio.output_device",
    }.isdisjoint(settings_mutation.ORDER23_OVERLAY_OSC_OUTPUT_SETTINGS_PATHS)


def test_runtime_only_secret_and_legacy_order24_fields_are_not_covered() -> None:
    settings_mutation = _service_module()

    assert {
        "ui.overlay_enabled",
        "ui.peer_translation_enabled",
        "system_prompts",
        "api_key_verified.openrouter",
        "managed_identity.installation_id",
        "secrets.openrouter_api_key",
        "secrets.deepgram_api_key",
    }.isdisjoint(settings_mutation.ORDER24_UI_PROMPT_CLIPBOARD_STATE_SETTINGS_PATHS)


def test_settings_path_patch_builds_typed_mutation_request_for_order21_surface() -> None:
    settings_mutation = _service_module()

    patch = settings_mutation.SettingsPathPatch(
        values_by_path={
            "translation.model": "gemma4",
            "openrouter.selection_alias": "gemma4_byok",
        },
        surface=settings_mutation.SETTINGS_MUTATION_SURFACE_TRANSLATION_PROVIDER,
    )

    request = patch.to_mutation_request(
        expected_revision="settings-r1",
        correlation_id="corr-order21",
    )

    assert request == settings_mutation.SettingsMutationRequest(
        values={
            "translation.model": "gemma4",
            "openrouter.selection_alias": "gemma4_byok",
        },
        expected_revision="settings-r1",
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_TRANSLATION_PROVIDER,
        correlation_id="corr-order21",
    )


def test_settings_path_patch_builds_typed_mutation_request_for_order22_surface() -> None:
    settings_mutation = _service_module()

    patch = settings_mutation.SettingsPathPatch(
        values_by_path={
            "languages.source_language": "ja",
            "stt.low_latency_mode": False,
            "audio.input_device": "Headset Mic",
        },
        surface=settings_mutation.SETTINGS_MUTATION_SURFACE_STT_LANGUAGE_AUDIO,
    )

    request = patch.to_mutation_request(
        expected_revision="settings-r2",
        correlation_id="corr-order22",
    )

    assert request == settings_mutation.SettingsMutationRequest(
        values={
            "languages.source_language": "ja",
            "stt.low_latency_mode": False,
            "audio.input_device": "Headset Mic",
        },
        expected_revision="settings-r2",
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_STT_LANGUAGE_AUDIO,
        correlation_id="corr-order22",
    )


def test_settings_path_patch_builds_typed_mutation_request_for_order23_surface() -> None:
    settings_mutation = _service_module()

    patch = settings_mutation.SettingsPathPatch(
        values_by_path={
            "overlay.show_translation": False,
            "overlay.desktop_flet.size_preset": "large",
            "osc.chatbox_max_chars": 120,
        },
        surface=settings_mutation.SETTINGS_MUTATION_SURFACE_OVERLAY_OSC_OUTPUT,
    )

    request = patch.to_mutation_request(
        expected_revision="settings-r3",
        correlation_id="corr-order23",
    )

    assert request == settings_mutation.SettingsMutationRequest(
        values={
            "overlay.show_translation": False,
            "overlay.desktop_flet.size_preset": "large",
            "osc.chatbox_max_chars": 120,
        },
        expected_revision="settings-r3",
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_OVERLAY_OSC_OUTPUT,
        correlation_id="corr-order23",
    )


def test_settings_path_patch_builds_typed_mutation_request_for_order24_surface() -> None:
    settings_mutation = _service_module()

    patch = settings_mutation.SettingsPathPatch(
        values_by_path={
            "ui.locale": "ja",
            "ui.clipboard_auto_translate_enabled": True,
            "system_prompt": "custom translation style",
        },
        surface=settings_mutation.SETTINGS_MUTATION_SURFACE_UI_PROMPT_CLIPBOARD_STATE,
    )

    request = patch.to_mutation_request(
        expected_revision="settings-r4",
        correlation_id="corr-order24",
    )

    assert request == settings_mutation.SettingsMutationRequest(
        values={
            "ui.locale": "ja",
            "ui.clipboard_auto_translate_enabled": True,
            "system_prompt": "custom translation style",
        },
        expected_revision="settings-r4",
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_UI_PROMPT_CLIPBOARD_STATE,
        correlation_id="corr-order24",
    )


@pytest.mark.asyncio
async def test_order21_path_validator_accepts_only_translation_provider_paths() -> None:
    settings_mutation = _service_module()
    validator = settings_mutation.SettingsPathMutationValidator(
        allowed_paths=settings_mutation.ORDER21_TRANSLATION_PROVIDER_SETTINGS_PATHS,
        component="settings_mutation",
        operation="validate_translation_provider_patch",
    )
    request = settings_mutation.SettingsMutationRequest(
        values={
            "translation.connection": "openrouter",
            "openrouter.fallback_selection_alias": "qwen35_flash",
            "local_llm.base_url": "http://127.0.0.1:11434/v1",
            "llm.concurrency_limit": 3,
        },
        expected_revision=None,
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_TRANSLATION_PROVIDER,
        correlation_id="corr-valid-paths",
    )

    result = await validator.validate(request)

    assert result == settings_mutation.SettingsMutationValidationResult(
        succeeded=True,
        message=None,
        diagnostics=None,
    )


@pytest.mark.asyncio
async def test_order21_path_validator_rejects_out_of_scope_paths_without_secret_values() -> None:
    settings_mutation = _service_module()
    validator = settings_mutation.SettingsPathMutationValidator(
        allowed_paths=settings_mutation.ORDER21_TRANSLATION_PROVIDER_SETTINGS_PATHS,
        component="settings_mutation",
        operation="validate_translation_provider_patch",
    )
    request = settings_mutation.SettingsMutationRequest(
        values={
            "stt.low_latency_mode": False,
            "audio.input_device": "default microphone",
            "overlay.target": "desktop",
            "secrets.openrouter_api_key": "secret-value-must-not-leak",
        },
        expected_revision=None,
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_TRANSLATION_PROVIDER,
        correlation_id="corr-invalid-paths",
    )

    result = await validator.validate(request)

    assert result.succeeded is False
    assert result.message is None
    assert result.diagnostics == messages.ErrorDiagnostics(
        component="settings_mutation",
        operation="validate_translation_provider_patch",
        code="settings_path_not_covered",
        category=messages.DIAGNOSTIC_CATEGORY_TRANSACTION,
        visibility=messages.DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=messages.CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=None,
        fields={"path": "audio.input_device"},
    )
    assert "secret-value-must-not-leak" not in repr(result)


@pytest.mark.asyncio
async def test_order22_path_validator_accepts_only_stt_language_audio_paths() -> None:
    settings_mutation = _service_module()
    validator = settings_mutation.SettingsPathMutationValidator(
        allowed_paths=settings_mutation.ORDER22_STT_LANGUAGE_AUDIO_SETTINGS_PATHS,
        component="settings_mutation",
        operation="validate_stt_language_audio_patch",
    )
    request = settings_mutation.SettingsMutationRequest(
        values={
            "provider.stt": "soniox",
            "provider.peer_stt": "local_qwen",
            "languages.source_language": "ja",
            "audio.input_device": "Headset Mic",
            "desktop_audio.vad_hangover_ms": 900,
            "stt.low_latency_mode": False,
            "soniox_stt.trailing_silence_ms": 150,
        },
        expected_revision=None,
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_STT_LANGUAGE_AUDIO,
        correlation_id="corr-valid-order22-paths",
    )

    result = await validator.validate(request)

    assert result == settings_mutation.SettingsMutationValidationResult(
        succeeded=True,
        message=None,
        diagnostics=None,
    )


@pytest.mark.asyncio
async def test_order22_path_validator_rejects_order21_overlay_and_secret_paths_without_values() -> (
    None
):
    settings_mutation = _service_module()
    validator = settings_mutation.SettingsPathMutationValidator(
        allowed_paths=settings_mutation.ORDER22_STT_LANGUAGE_AUDIO_SETTINGS_PATHS,
        component="settings_mutation",
        operation="validate_stt_language_audio_patch",
    )
    request = settings_mutation.SettingsMutationRequest(
        values={
            "translation.model": "gemma4-secret-ish",
            "openrouter.selection_alias": "managed-secret-ish",
            "overlay.target": "desktop-secret-ish",
            "secrets.deepgram_api_key": "secret-value-must-not-leak",
        },
        expected_revision=None,
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_STT_LANGUAGE_AUDIO,
        correlation_id="corr-invalid-order22-paths",
    )

    result = await validator.validate(request)

    assert result.succeeded is False
    assert result.message is None
    assert result.diagnostics == messages.ErrorDiagnostics(
        component="settings_mutation",
        operation="validate_stt_language_audio_patch",
        code="settings_path_not_covered",
        category=messages.DIAGNOSTIC_CATEGORY_TRANSACTION,
        visibility=messages.DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=messages.CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=None,
        fields={"path": "openrouter.selection_alias"},
    )
    assert "secret-value-must-not-leak" not in repr(result)
    assert "gemma4-secret-ish" not in repr(result)


@pytest.mark.asyncio
async def test_order23_path_validator_accepts_only_overlay_osc_output_paths() -> None:
    settings_mutation = _service_module()
    validator = settings_mutation.SettingsPathMutationValidator(
        allowed_paths=settings_mutation.ORDER23_OVERLAY_OSC_OUTPUT_SETTINGS_PATHS,
        component="settings_mutation",
        operation="validate_overlay_osc_output_patch",
    )
    request = settings_mutation.SettingsMutationRequest(
        values={
            "overlay.target": "desktop",
            "overlay.calibration.distance": 1.4,
            "overlay.desktop_flet.position.x": 24,
            "overlay.desktop_flet.visual.background_alpha": 0.45,
            "osc.host": "127.0.0.1",
            "osc.port": 9001,
            "osc.chatbox_max_chars": 120,
            "osc.chatbox_include_source": True,
        },
        expected_revision=None,
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_OVERLAY_OSC_OUTPUT,
        correlation_id="corr-valid-order23-paths",
    )

    result = await validator.validate(request)

    assert result == settings_mutation.SettingsMutationValidationResult(
        succeeded=True,
        message=None,
        diagnostics=None,
    )


@pytest.mark.asyncio
async def test_order23_path_validator_rejects_runtime_only_peer_and_secret_paths_without_values() -> (
    None
):
    settings_mutation = _service_module()
    validator = settings_mutation.SettingsPathMutationValidator(
        allowed_paths=settings_mutation.ORDER23_OVERLAY_OSC_OUTPUT_SETTINGS_PATHS,
        component="settings_mutation",
        operation="validate_overlay_osc_output_patch",
    )
    request = settings_mutation.SettingsMutationRequest(
        values={
            "active_chatbox_channel": "peer-secret-ish",
            "ui.overlay_enabled": True,
            "ui.peer_translation_enabled": True,
            "secrets.openrouter_api_key": "secret-value-must-not-leak",
        },
        expected_revision=None,
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_OVERLAY_OSC_OUTPUT,
        correlation_id="corr-invalid-order23-paths",
    )

    result = await validator.validate(request)

    assert result.succeeded is False
    assert result.message is None
    assert result.diagnostics == messages.ErrorDiagnostics(
        component="settings_mutation",
        operation="validate_overlay_osc_output_patch",
        code="settings_path_not_covered",
        category=messages.DIAGNOSTIC_CATEGORY_TRANSACTION,
        visibility=messages.DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=messages.CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=None,
        fields={"path": "active_chatbox_channel"},
    )
    assert "secret-value-must-not-leak" not in repr(result)
    assert "peer-secret-ish" not in repr(result)


@pytest.mark.asyncio
async def test_order24_path_validator_accepts_only_ui_prompt_clipboard_state_paths() -> None:
    settings_mutation = _service_module()
    validator = settings_mutation.SettingsPathMutationValidator(
        allowed_paths=settings_mutation.ORDER24_UI_PROMPT_CLIPBOARD_STATE_SETTINGS_PATHS,
        component="settings_mutation",
        operation="validate_ui_prompt_clipboard_state_patch",
    )
    request = settings_mutation.SettingsMutationRequest(
        values={
            "secrets.backend": "encrypted_file",
            "secrets.encrypted_file_path": "secure-secrets.json",
            "ui.locale": "ja",
            "ui.peer_translation_eula_accepted": True,
            "ui.integrated_context_enabled": False,
            "ui.integrated_context_bootstrapped": True,
            "ui.clipboard_auto_translate_enabled": True,
            "ui.github_star_prompt_clicked": False,
            "ui.github_star_prompt_last_shown_at": "2026-06-08T00:00:00Z",
            "ui.github_star_prompt_show_count": 2,
            "ui.github_star_prompt_translation_success_observed": True,
            "ui.github_star_prompt_eligible_launch_count": 3,
            "system_prompt": "custom translation style",
        },
        expected_revision=None,
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_UI_PROMPT_CLIPBOARD_STATE,
        correlation_id="corr-valid-order24-paths",
    )

    result = await validator.validate(request)

    assert result == settings_mutation.SettingsMutationValidationResult(
        succeeded=True,
        message=None,
        diagnostics=None,
    )


@pytest.mark.asyncio
async def test_order24_path_validator_rejects_runtime_secret_and_legacy_paths_without_values() -> (
    None
):
    settings_mutation = _service_module()
    validator = settings_mutation.SettingsPathMutationValidator(
        allowed_paths=settings_mutation.ORDER24_UI_PROMPT_CLIPBOARD_STATE_SETTINGS_PATHS,
        component="settings_mutation",
        operation="validate_ui_prompt_clipboard_state_patch",
    )
    request = settings_mutation.SettingsMutationRequest(
        values={
            "api_key_verified.openrouter": True,
            "managed_identity.installation_id": "device-secret-ish",
            "system_prompts": {"openrouter": "prompt-secret-ish"},
            "ui.overlay_enabled": True,
            "ui.peer_translation_enabled": True,
            "secrets.openrouter_api_key": "secret-value-must-not-leak",
        },
        expected_revision=None,
        reason=settings_mutation.SETTINGS_MUTATION_SURFACE_UI_PROMPT_CLIPBOARD_STATE,
        correlation_id="corr-invalid-order24-paths",
    )

    result = await validator.validate(request)

    assert result.succeeded is False
    assert result.message is None
    assert result.diagnostics == messages.ErrorDiagnostics(
        component="settings_mutation",
        operation="validate_ui_prompt_clipboard_state_patch",
        code="settings_path_not_covered",
        category=messages.DIAGNOSTIC_CATEGORY_TRANSACTION,
        visibility=messages.DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=messages.CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=None,
        fields={"path": "api_key_verified.openrouter"},
    )
    assert "secret-value-must-not-leak" not in repr(result)
    assert "device-secret-ish" not in repr(result)
    assert "prompt-secret-ish" not in repr(result)


def test_settings_mutation_service_module_avoids_concrete_ui_provider_and_i18n_imports() -> None:
    module = _service_module()
    tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not {
        imported
        for imported in imported_modules
        for forbidden in FORBIDDEN_SERVICE_IMPORT_PREFIXES
        if imported == forbidden or imported.startswith(f"{forbidden}.")
    }
