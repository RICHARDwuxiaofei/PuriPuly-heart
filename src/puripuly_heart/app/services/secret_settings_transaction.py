from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol

from puripuly_heart.app.ports._settings_values import freeze_settings_values
from puripuly_heart.app.ports.secret_store import SecretSnapshot, SecretStorePort
from puripuly_heart.app.ports.settings_repository import (
    SettingsCommitRequest,
    SettingsRepositoryPort,
)
from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_TRANSACTION,
    DIAGNOSTIC_VISIBILITY_BASIC,
    TRANSACTION_STATUS_SECRET_WRITE_FAILED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED_SECRET_RESTORE_FAILED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED_SECRET_RESTORED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    ErrorDiagnostics,
    TransactionResult,
    UserMessageRef,
)


@dataclass(frozen=True, slots=True)
class DashboardNeedsKeySnapshot:
    translation_needs_key: bool | None
    stt_needs_key: bool | None
    settings_revision: str | None = None


@dataclass(frozen=True, slots=True)
class SecretSetRequest:
    secret_key: str
    secret_value: str = field(repr=False)
    settings_values: Mapping[str, object]
    expected_settings_revision: str | None
    reason: str | None
    correlation_id: str | None
    dashboard_needs_key: DashboardNeedsKeySnapshot | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings_values", freeze_settings_values(self.settings_values))


@dataclass(frozen=True, slots=True)
class SecretClearRequest:
    secret_key: str
    settings_values: Mapping[str, object]
    expected_settings_revision: str | None
    reason: str | None
    correlation_id: str | None
    dashboard_needs_key: DashboardNeedsKeySnapshot | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings_values", freeze_settings_values(self.settings_values))


class DashboardNeedsKeySnapshotPublisher(Protocol):
    async def publish_dashboard_needs_key_snapshot(
        self,
        snapshot: DashboardNeedsKeySnapshot,
        *,
        correlation_id: str | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SecretSettingsTransaction:
    secret_store: SecretStorePort
    settings_repository: SettingsRepositoryPort
    dashboard_needs_key_publisher: DashboardNeedsKeySnapshotPublisher | None = None

    async def set_provider_secret(self, request: SecretSetRequest) -> TransactionResult:
        try:
            snapshot = await self.secret_store.snapshot_secret(request.secret_key)
        except Exception:
            return _secret_write_failed_result(
                secret_key=request.secret_key,
                action="set",
                operation="snapshot_secret",
            )

        try:
            write_result = await self.secret_store.set_secret(
                request.secret_key,
                request.secret_value,
            )
        except Exception:
            return _secret_write_failed_result(
                secret_key=request.secret_key,
                action="set",
                operation="set_secret",
            )

        if not write_result.succeeded:
            return _secret_write_failed_result(
                secret_key=request.secret_key,
                action="set",
                operation="set_secret",
            )

        return await self._commit_settings_or_restore_secret(
            request=request,
            snapshot=snapshot,
            action="set",
        )

    async def clear_provider_secret(self, request: SecretClearRequest) -> TransactionResult:
        try:
            snapshot = await self.secret_store.snapshot_secret(request.secret_key)
        except Exception:
            return _secret_write_failed_result(
                secret_key=request.secret_key,
                action="clear",
                operation="snapshot_secret",
            )

        try:
            write_result = await self.secret_store.clear_secret(request.secret_key)
        except Exception:
            return _secret_write_failed_result(
                secret_key=request.secret_key,
                action="clear",
                operation="clear_secret",
            )

        if not write_result.succeeded:
            return _secret_write_failed_result(
                secret_key=request.secret_key,
                action="clear",
                operation="clear_secret",
            )

        return await self._commit_settings_or_restore_secret(
            request=request,
            snapshot=snapshot,
            action="clear",
        )

    async def _commit_settings_or_restore_secret(
        self,
        *,
        request: SecretSetRequest | SecretClearRequest,
        snapshot: SecretSnapshot,
        action: str,
    ) -> TransactionResult:
        try:
            commit_result = await self.settings_repository.save(
                SettingsCommitRequest(
                    values=request.settings_values,
                    expected_revision=request.expected_settings_revision,
                    reason=request.reason,
                )
            )
        except Exception:
            return await self._restore_after_settings_commit_failure(
                snapshot=snapshot,
                action=action,
                commit_message=None,
            )

        if commit_result.succeeded and commit_result.snapshot is not None:
            await self._publish_dashboard_needs_key_snapshot(
                request.dashboard_needs_key,
                settings_revision=commit_result.snapshot.revision,
                correlation_id=request.correlation_id,
            )
            return TransactionResult(
                status=TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
                message=commit_result.message,
                diagnostics=commit_result.diagnostics,
            )

        return await self._restore_after_settings_commit_failure(
            snapshot=snapshot,
            action=action,
            commit_message=commit_result.message,
        )

    async def _restore_after_settings_commit_failure(
        self,
        *,
        snapshot: SecretSnapshot,
        action: str,
        commit_message: UserMessageRef | None,
    ) -> TransactionResult:
        try:
            restore_result = await self.secret_store.restore_secret(snapshot)
            restore_succeeded = restore_result.succeeded
        except Exception:
            restore_succeeded = False

        if restore_succeeded:
            return TransactionResult(
                status=TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED_SECRET_RESTORED,
                message=commit_message,
                diagnostics=_compensation_diagnostics(
                    code="settings_commit_failed_secret_restored",
                    secret_key=snapshot.key,
                    action=action,
                    previous_secret_existed=snapshot.existed,
                    secret_restore_succeeded=True,
                ),
            )

        return TransactionResult(
            status=TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED_SECRET_RESTORE_FAILED,
            message=commit_message,
            diagnostics=_compensation_diagnostics(
                code="settings_commit_failed_secret_restore_failed",
                secret_key=snapshot.key,
                action=action,
                previous_secret_existed=snapshot.existed,
                secret_restore_succeeded=False,
            ),
        )

    async def _publish_dashboard_needs_key_snapshot(
        self,
        snapshot: DashboardNeedsKeySnapshot | None,
        *,
        settings_revision: str | None,
        correlation_id: str | None,
    ) -> None:
        if snapshot is None or self.dashboard_needs_key_publisher is None:
            return
        try:
            await self.dashboard_needs_key_publisher.publish_dashboard_needs_key_snapshot(
                replace(snapshot, settings_revision=settings_revision),
                correlation_id=correlation_id,
            )
        except Exception:
            pass


def _secret_write_failed_result(
    *,
    secret_key: str,
    action: str,
    operation: str,
) -> TransactionResult:
    return TransactionResult(
        status=TRANSACTION_STATUS_SECRET_WRITE_FAILED,
        message=None,
        diagnostics=ErrorDiagnostics(
            component="secret_settings_transaction",
            operation=operation,
            code="secret_write_failed",
            category=DIAGNOSTIC_CATEGORY_TRANSACTION,
            visibility=DIAGNOSTIC_VISIBILITY_BASIC,
            content_policy=CONTENT_POLICY_METADATA_ONLY,
            status_code=None,
            retry_after_ms=None,
            fields={
                "secret_key": secret_key,
                "action": action,
                "secret_write_succeeded": False,
            },
        ),
    )


def _compensation_diagnostics(
    *,
    code: str,
    secret_key: str,
    action: str,
    previous_secret_existed: bool,
    secret_restore_succeeded: bool,
) -> ErrorDiagnostics:
    return ErrorDiagnostics(
        component="secret_settings_transaction",
        operation="restore_secret",
        code=code,
        category=DIAGNOSTIC_CATEGORY_TRANSACTION,
        visibility=DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=None,
        fields={
            "secret_key": secret_key,
            "action": action,
            "previous_secret_existed": previous_secret_existed,
            "settings_commit_succeeded": False,
            "secret_restore_succeeded": secret_restore_succeeded,
        },
    )


__all__ = [
    "DashboardNeedsKeySnapshot",
    "DashboardNeedsKeySnapshotPublisher",
    "SecretClearRequest",
    "SecretSetRequest",
    "SecretSettingsTransaction",
]
