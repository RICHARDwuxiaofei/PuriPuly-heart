from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from puripuly_heart.app.ports._settings_values import freeze_settings_values
from puripuly_heart.app.ports.runtime_apply import RuntimeApplyPort, RuntimeApplyRequest
from puripuly_heart.app.ports.settings_repository import (
    SettingsCommitRequest,
    SettingsCommitResult,
    SettingsRepositoryPort,
    SettingsSnapshot,
)
from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_LIFECYCLE,
    DIAGNOSTIC_CATEGORY_TRANSACTION,
    DIAGNOSTIC_VISIBILITY_BASIC,
    RUNTIME_APPLY_STATUS_APPLIED,
    SEVERITY_WARNING,
    TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
    ErrorDiagnostics,
    RuntimeApplyResult,
    TransactionResult,
    UserMessageRef,
)

SETTINGS_MUTATION_SURFACE_TRANSLATION_PROVIDER: Final = "settings.translation_provider"

ORDER21_TRANSLATION_PROVIDER_SETTINGS_PATHS: Final[tuple[str, ...]] = (
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
)


@dataclass(frozen=True, slots=True)
class SettingsMutationRequest:
    values: Mapping[str, object]
    expected_revision: str | None
    reason: str | None
    correlation_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", freeze_settings_values(self.values))


@dataclass(frozen=True, slots=True)
class SettingsMutationValidationResult:
    succeeded: bool
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None


@dataclass(frozen=True, slots=True)
class SettingsPathPatch:
    values_by_path: Mapping[str, object]
    surface: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "values_by_path",
            freeze_settings_values(self.values_by_path),
        )

    def to_mutation_request(
        self,
        *,
        expected_revision: str | None,
        correlation_id: str | None,
    ) -> SettingsMutationRequest:
        return SettingsMutationRequest(
            values=self.values_by_path,
            expected_revision=expected_revision,
            reason=self.surface,
            correlation_id=correlation_id,
        )


@dataclass(frozen=True, slots=True)
class SettingsPathMutationValidator:
    allowed_paths: tuple[str, ...]
    component: str
    operation: str | None

    def __init__(
        self,
        *,
        allowed_paths: tuple[str, ...],
        component: str,
        operation: str | None,
    ) -> None:
        object.__setattr__(self, "allowed_paths", tuple(allowed_paths))
        object.__setattr__(self, "component", component)
        object.__setattr__(self, "operation", operation)

    async def validate(
        self,
        request: SettingsMutationRequest,
    ) -> SettingsMutationValidationResult:
        allowed = frozenset(self.allowed_paths)
        disallowed_paths = sorted(
            str(path) for path in request.values if not isinstance(path, str) or path not in allowed
        )
        if disallowed_paths:
            return SettingsMutationValidationResult(
                succeeded=False,
                message=None,
                diagnostics=ErrorDiagnostics(
                    component=self.component,
                    operation=self.operation,
                    code="settings_path_not_covered",
                    category=DIAGNOSTIC_CATEGORY_TRANSACTION,
                    visibility=DIAGNOSTIC_VISIBILITY_BASIC,
                    content_policy=CONTENT_POLICY_METADATA_ONLY,
                    status_code=None,
                    retry_after_ms=None,
                    fields={"path": disallowed_paths[0]},
                ),
            )
        return SettingsMutationValidationResult(
            succeeded=True,
            message=None,
            diagnostics=None,
        )


class SettingsMutationValidator(Protocol):
    async def validate(
        self,
        request: SettingsMutationRequest,
    ) -> SettingsMutationValidationResult: ...


class SettingsSnapshotPublisher(Protocol):
    async def publish_settings_snapshot(
        self,
        snapshot: SettingsSnapshot,
        *,
        correlation_id: str | None,
    ) -> None: ...


class RuntimeApplyResultPublisher(Protocol):
    async def publish_runtime_apply_result(
        self,
        result: RuntimeApplyResult,
        *,
        correlation_id: str | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SettingsMutationService:
    settings_repository: SettingsRepositoryPort
    runtime_apply: RuntimeApplyPort
    validator: SettingsMutationValidator
    snapshot_publisher: SettingsSnapshotPublisher | None = None
    runtime_result_publisher: RuntimeApplyResultPublisher | None = None

    async def mutate(self, request: SettingsMutationRequest) -> TransactionResult:
        validation_result = await self.validator.validate(request)
        if not validation_result.succeeded:
            return TransactionResult(
                status=TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED,
                message=validation_result.message,
                diagnostics=validation_result.diagnostics,
            )

        commit_result = await self.settings_repository.save(
            SettingsCommitRequest(
                values=request.values,
                expected_revision=request.expected_revision,
                reason=request.reason,
            )
        )
        if not commit_result.succeeded or commit_result.snapshot is None:
            return TransactionResult(
                status=TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED,
                message=commit_result.message,
                diagnostics=commit_result.diagnostics,
            )

        snapshot = commit_result.snapshot
        if self.snapshot_publisher is not None:
            try:
                await self.snapshot_publisher.publish_settings_snapshot(
                    snapshot,
                    correlation_id=request.correlation_id,
                )
            except Exception:
                pass

        try:
            runtime_result = await self.runtime_apply.apply_runtime(
                RuntimeApplyRequest(
                    settings_values=snapshot.values,
                    reason=request.reason,
                    correlation_id=request.correlation_id,
                )
            )
        except Exception:
            return _runtime_apply_exception_transaction_result()

        transaction_result = _transaction_result_for_commit_and_runtime(
            commit_result=commit_result,
            runtime_result=runtime_result,
        )
        if self.runtime_result_publisher is not None:
            try:
                await self.runtime_result_publisher.publish_runtime_apply_result(
                    runtime_result,
                    correlation_id=request.correlation_id,
                )
            except Exception:
                pass

        return transaction_result


def _transaction_result_for_commit_and_runtime(
    *,
    commit_result: SettingsCommitResult,
    runtime_result: RuntimeApplyResult,
) -> TransactionResult:
    transaction_status = (
        TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED
        if runtime_result.status == RUNTIME_APPLY_STATUS_APPLIED
        else TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED
    )
    return TransactionResult(
        status=transaction_status,
        message=(
            runtime_result.message if runtime_result.message is not None else commit_result.message
        ),
        diagnostics=(
            runtime_result.diagnostics
            if runtime_result.diagnostics is not None
            else commit_result.diagnostics
        ),
    )


def _runtime_apply_exception_transaction_result() -> TransactionResult:
    return TransactionResult(
        status=TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
        message=UserMessageRef(
            key="settings.mutation.runtime_apply_failed",
            params={"phase": "runtime_apply"},
            severity=SEVERITY_WARNING,
        ),
        diagnostics=ErrorDiagnostics(
            component="settings_mutation",
            operation="runtime_apply",
            code="runtime_apply_exception",
            category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
            visibility=DIAGNOSTIC_VISIBILITY_BASIC,
            content_policy=CONTENT_POLICY_METADATA_ONLY,
            status_code=None,
            retry_after_ms=None,
            fields={"phase": "runtime_apply"},
        ),
    )


__all__ = [
    "ORDER21_TRANSLATION_PROVIDER_SETTINGS_PATHS",
    "RuntimeApplyResultPublisher",
    "SETTINGS_MUTATION_SURFACE_TRANSLATION_PROVIDER",
    "SettingsMutationRequest",
    "SettingsMutationService",
    "SettingsPathMutationValidator",
    "SettingsPathPatch",
    "SettingsMutationValidationResult",
    "SettingsMutationValidator",
    "SettingsSnapshotPublisher",
]
