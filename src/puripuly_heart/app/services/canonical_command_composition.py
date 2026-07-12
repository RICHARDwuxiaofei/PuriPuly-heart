from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from puripuly_heart.app.ports.application_settings import (
    OperationalCommandResult,
    OperationalStateCommand,
    OverlayOscOutputSettingsCommand,
    SettingChange,
    SettingsCommand,
    SettingsCommandResult,
    SettingsSurface,
    SttLanguageAudioSettingsCommand,
    TranslationProviderSettingsCommand,
    UiPromptClipboardSettingsCommand,
)
from puripuly_heart.app.ports.runtime_apply import (
    RuntimeApplyExecution,
    RuntimeApplyExecutionPort,
    RuntimeApplyPort,
    RuntimeApplyRequest,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.application_settings_codecs import (
    FIELD_CODECS,
    OPERATIONAL_CODECS,
    CodecKind,
)
from puripuly_heart.app.services.canonical_application_settings import (
    CanonicalApplicationSettingsService,
    CanonicalOperationalStateService,
    surface_for_settings_command,
)
from puripuly_heart.app.services.canonical_secret_commands import (
    CanonicalSecretCommandService,
    SyncSecretStorePortAdapter,
)
from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
    PersistedOperationalState,
    UserIntentSettings,
)
from puripuly_heart.core.messages import (
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_FAILED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
    RuntimeApplyResult,
    TransactionResult,
)

PRODUCTION_SETTINGS_SURFACES: frozenset[str] = frozenset(
    {
        "translation_provider",
        "stt_language_audio",
        "overlay_osc_output",
        "ui_prompt_clipboard_state",
    }
)

_SURFACE_ORDER: tuple[str, ...] = (
    "translation_provider",
    "stt_language_audio",
    "overlay_osc_output",
    "ui_prompt_clipboard_state",
)

_SURFACE_TO_ENUM: Mapping[str, SettingsSurface] = {
    "translation_provider": SettingsSurface.TRANSLATION_PROVIDER,
    "stt_language_audio": SettingsSurface.STT_LANGUAGE_AUDIO,
    "overlay_osc_output": SettingsSurface.OVERLAY_OSC_OUTPUT,
    "ui_prompt_clipboard_state": SettingsSurface.UI_PROMPT_CLIPBOARD,
}

_SURFACE_TO_COMMAND: Mapping[str, type[SettingsCommand]] = {
    "translation_provider": TranslationProviderSettingsCommand,
    "stt_language_audio": SttLanguageAudioSettingsCommand,
    "overlay_osc_output": OverlayOscOutputSettingsCommand,
    "ui_prompt_clipboard_state": UiPromptClipboardSettingsCommand,
}

_OPERATIONAL_COMMAND_TYPES: Mapping[object, type[OperationalStateCommand]] = {
    codec.canonical_path: codec.command_type for codec in OPERATIONAL_CODECS.values()
}


def _get(value: object, path: tuple[str, ...]) -> object:
    for part in path:
        value = getattr(value, part)
    return value


def _codec_value(kind: CodecKind, value: object):
    from puripuly_heart.app.ports.application_settings import (
        JsonScalarEntry,
        LocalExtraBodyValue,
        StringListMapValue,
        StringMapValue,
    )

    if kind == CodecKind.STRING_LIST:
        return tuple(value)  # type: ignore[arg-type]
    if kind == CodecKind.STRING_MAP:
        return StringMapValue(tuple(sorted(value.items())))  # type: ignore[union-attr]
    if kind == CodecKind.STRING_LIST_MAP:
        return StringListMapValue(
            tuple(sorted((key, tuple(items)) for key, items in value.items()))  # type: ignore[union-attr]
        )
    if kind == CodecKind.LOCAL_EXTRA_BODY:
        return LocalExtraBodyValue(
            tuple(JsonScalarEntry(key, item) for key, item in sorted(value.items()))  # type: ignore[union-attr]
        )
    return value


def surface_changes_from_intents(
    before: UserIntentSettings,
    after: UserIntentSettings,
    surface: str,
) -> tuple[SettingChange, ...]:
    owner = _SURFACE_TO_ENUM[surface]
    changes: list[SettingChange] = []
    for field, codec in FIELD_CODECS.items():
        if codec.owner != owner:
            continue
        before_values = tuple(
            _codec_value(codec.kind, _get(before, path)) for path in codec.canonical_paths
        )
        after_values = tuple(
            _codec_value(codec.kind, _get(after, path)) for path in codec.canonical_paths
        )
        before_decoded = codec.decode(before_values)
        after_decoded = codec.decode(after_values)
        if before_decoded != after_decoded:
            changes.append(SettingChange(field, after_decoded))
    return tuple(changes)


def operational_commands_from_states(
    before: PersistedOperationalState,
    after: PersistedOperationalState,
    *,
    expected_revision: str,
) -> tuple[OperationalStateCommand, ...]:
    commands: list[OperationalStateCommand] = []
    for codec in OPERATIONAL_CODECS.values():
        before_value = _get(before, codec.canonical_path)
        after_value = _get(after, codec.canonical_path)
        if before_value == after_value:
            continue
        command_type = codec.command_type
        commands.append(command_type(after_value, expected_revision))  # type: ignore[call-arg,misc]
    return tuple(commands)


def settings_command_for_surface(
    surface: str,
    changes: tuple[SettingChange, ...],
    expected_revision: str,
    correlation_id: str | None = None,
) -> SettingsCommand:
    command_type = _SURFACE_TO_COMMAND[surface]
    return command_type(changes, expected_revision, correlation_id)


def command_result_as_transaction(result: SettingsCommandResult) -> TransactionResult:
    if result.status in {"applied", "cancelled_committed", "no_change"}:
        status = TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED
    elif result.status in {
        "degraded",
        "cancelled_degraded",
        "partial_commit_degraded",
    }:
        status = TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED
    else:
        from puripuly_heart.core.messages import TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED

        status = TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED
    return TransactionResult(status, result.message, result.diagnostics)


def operational_result_as_transaction(result: OperationalCommandResult) -> TransactionResult:
    if result.status in {"committed", "cancelled_committed", "no_change"}:
        status = TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED
    else:
        from puripuly_heart.core.messages import TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED

        status = TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED
    return TransactionResult(status, None, result.diagnostics)


def dispatch_result_as_transaction(
    dispatch: "ProductionSettingsDispatchResult",
) -> TransactionResult:
    from puripuly_heart.core.messages import (
        CONTENT_POLICY_METADATA_ONLY,
        DIAGNOSTIC_CATEGORY_TRANSACTION,
        DIAGNOSTIC_VISIBILITY_BASIC,
        TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED,
        ErrorDiagnostics,
    )

    if dispatch.status in {"applied", "no_change"} and not dispatch.reconciliation_required:
        status = TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED
    elif (
        dispatch.status
        in {
            "degraded",
            "partial_commit_degraded",
            "cancelled_degraded",
            "cancelled_committed",
        }
        or dispatch.reconciliation_required
    ):
        status = TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED
    else:
        status = TRANSACTION_STATUS_SETTINGS_COMMIT_FAILED
    surface_summary = ",".join(
        f"{surface}:{result.status}" for surface, result in dispatch.surface_results
    )
    operational_summary = ",".join(result.status for result in dispatch.operational_results)
    surface_receipts = ",".join(
        result.receipt.revision
        for _, result in dispatch.surface_results
        if result.receipt is not None
    )
    operational_receipts = ",".join(
        result.receipt.revision
        for result in dispatch.operational_results
        if result.receipt is not None
    )
    diagnostics = ErrorDiagnostics(
        component="canonical_command_composition",
        operation="execute_production_settings_delta",
        code=dispatch.status,
        category=DIAGNOSTIC_CATEGORY_TRANSACTION,
        visibility=DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=None,
        fields={
            "aggregate_status": dispatch.status,
            "partial_commit": dispatch.partial_commit,
            "reconciliation_required": dispatch.reconciliation_required,
            "final_revision": dispatch.final_receipt.revision,
            "surface_results": surface_summary or None,
            "operational_results": operational_summary or None,
            "surface_receipts": surface_receipts or None,
            "operational_receipts": operational_receipts or None,
            "secrets_rebound": dispatch.secrets_rebound,
        },
    )
    return TransactionResult(status, None, diagnostics)


@dataclass(slots=True)
class ProductionCanonicalRuntimeApply(RuntimeApplyPort, RuntimeApplyExecutionPort):
    host: object

    async def apply_runtime(self, request: RuntimeApplyRequest) -> RuntimeApplyResult:
        execution = await self.apply_runtime_execution(request)
        return execution.result

    async def apply_runtime_execution(self, request: RuntimeApplyRequest) -> RuntimeApplyExecution:
        surface = request.surface or surface_for_settings_command_reason(request.receipt.reason)
        operational = request.operational
        if operational is None:
            snapshot = getattr(self.host, "runtime_operational_snapshot", None)
            if not callable(snapshot):
                raise RuntimeError("application host must own operational snapshot")
            operational = snapshot()
        execution = await self.host.apply_committed_runtime(
            before=request.before,
            after=request.receipt,
            surface=surface,
            operational=operational,
        )
        completed = [str(item) for item in execution.completed]
        skipped = [str(item) for item in getattr(execution, "skipped", ())]
        failed = None if execution.failed is None else str(execution.failed)
        reconciliation = bool(execution.reconciliation_required)
        status = (
            RUNTIME_APPLY_STATUS_APPLIED
            if execution.transaction.status
            == TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED
            else RUNTIME_APPLY_STATUS_FAILED
        )
        message = execution.transaction.message
        diagnostics = execution.transaction.diagnostics
        if (
            status == RUNTIME_APPLY_STATUS_APPLIED
            and request.before is not None
            and request.receipt is not None
            and request.before.envelope.intent.secrets != request.receipt.envelope.intent.secrets
        ):
            composition = getattr(self.host, "canonical_commands", None)
            rebind = getattr(composition, "try_rebind_secrets_from_intent", None)
            if callable(rebind):
                rebind_error = await rebind(request.receipt)
                if rebind_error is None:
                    completed.append("secrets_backend_rebind")
                else:
                    status = RUNTIME_APPLY_STATUS_FAILED
                    failed = "secrets_backend_rebind"
                    reconciliation = True
                    from puripuly_heart.core.messages import (
                        CONTENT_POLICY_METADATA_ONLY,
                        DIAGNOSTIC_CATEGORY_TRANSACTION,
                        DIAGNOSTIC_VISIBILITY_BASIC,
                        ErrorDiagnostics,
                    )

                    diagnostics = ErrorDiagnostics(
                        component="canonical_command_composition",
                        operation="secrets_backend_rebind",
                        code=rebind_error,
                        category=DIAGNOSTIC_CATEGORY_TRANSACTION,
                        visibility=DIAGNOSTIC_VISIBILITY_BASIC,
                        content_policy=CONTENT_POLICY_METADATA_ONLY,
                        status_code=None,
                        retry_after_ms=None,
                        fields={"surface": surface, "failed": "secrets_backend_rebind"},
                    )
        return RuntimeApplyExecution(
            result=RuntimeApplyResult(status, message, diagnostics),
            completed=tuple(completed),
            failed=failed,
            skipped=tuple(skipped),
            reconciliation_required=reconciliation,
        )


def surface_for_settings_command_reason(reason: str | None) -> str:
    if reason is None:
        raise ValueError("settings command reason is required for runtime apply")
    mapping = {
        TranslationProviderSettingsCommand.__name__: "translation_provider",
        SttLanguageAudioSettingsCommand.__name__: "stt_language_audio",
        OverlayOscOutputSettingsCommand.__name__: "overlay_osc_output",
        UiPromptClipboardSettingsCommand.__name__: "ui_prompt_clipboard_state",
    }
    try:
        return mapping[reason]
    except KeyError as exc:
        raise ValueError(f"unknown settings command reason: {reason}") from exc


@dataclass(frozen=True, slots=True)
class ProductionSettingsDispatchResult:
    status: str
    surface_results: tuple[tuple[str, SettingsCommandResult], ...]
    operational_results: tuple[OperationalCommandResult, ...]
    final_receipt: SettingsCommitReceipt
    secrets_rebound: bool = False
    reconciliation_required: bool = False
    partial_commit: bool = False

    @property
    def last_settings_result(self) -> SettingsCommandResult | None:
        if not self.surface_results:
            return None
        return self.surface_results[-1][1]


def _aggregate_dispatch_status(
    surface_results: list[tuple[str, SettingsCommandResult]],
    operational_results: list[OperationalCommandResult],
    *,
    stopped_early: bool,
) -> tuple[str, bool, bool]:
    status = "applied"
    reconciliation = False
    partial = False
    committed = 0
    for _, result in surface_results:
        if (
            result.receipt is not None
            and not result.no_op
            and result.status
            in {
                "applied",
                "degraded",
                "cancelled_committed",
                "cancelled_degraded",
            }
        ):
            committed += 1
        if result.reconciliation_required:
            reconciliation = True
        if result.status in {"degraded", "cancelled_degraded"}:
            status = "degraded"
            reconciliation = True
        elif result.status not in {"applied", "cancelled_committed", "no_change"}:
            status = result.status
            reconciliation = committed > 0
    for result in operational_results:
        if (
            result.receipt is not None
            and not result.no_op
            and result.status
            in {
                "committed",
                "cancelled_committed",
            }
        ):
            committed += 1
        if result.reconciliation_required:
            reconciliation = True
        if result.status not in {"committed", "cancelled_committed", "no_change"}:
            status = result.status
            reconciliation = committed > 0
    if stopped_early and committed:
        partial = True
        reconciliation = True
        if status == "applied":
            status = "partial_commit_degraded"
        elif status not in {"partial_commit_degraded", "degraded"}:
            status = (
                "partial_commit_degraded"
                if status in {"conflict", "invalid", "read_failed"}
                else status
            )
    if status == "degraded" and partial:
        status = "partial_commit_degraded"
    return status, reconciliation, partial


@dataclass(slots=True)
class CanonicalCommandComposition:
    settings_commands: CanonicalApplicationSettingsService
    operational_commands: CanonicalOperationalStateService
    secret_commands: CanonicalSecretCommandService
    runtime_host: object
    runtime_apply: ProductionCanonicalRuntimeApply
    activated_surfaces: frozenset[str]
    _secrets: object
    _state_path: Path
    _secret_port: SyncSecretStorePortAdapter

    @property
    def settings_queries(self) -> CanonicalApplicationSettingsService:
        return self.settings_commands

    @property
    def operational_queries(self) -> CanonicalOperationalStateService:
        return self.operational_commands

    @property
    def secret_queries(self) -> CanonicalSecretCommandService:
        return self.secret_commands

    async def current_receipt(self) -> SettingsCommitReceipt:
        repository = self.settings_commands._repository
        envelope = await repository.load()
        return SettingsCommitReceipt(
            AppSettingsVNext(intent=envelope.intent, state=envelope.operational_state),
            envelope.revision,
            "current_state",
            None,
        )

    async def resolve_secret_value(self, key: str) -> str | None:
        return await self.secret_commands.resolve_secret_value(key)

    async def execute_surface_intent_delta(
        self,
        *,
        surface: str,
        before_intent: UserIntentSettings,
        after_intent: UserIntentSettings,
        expected_revision: str | None = None,
        correlation_id: str | None = None,
    ) -> SettingsCommandResult:
        if surface not in PRODUCTION_SETTINGS_SURFACES:
            raise ValueError(f"unknown production settings surface: {surface}")
        changes = surface_changes_from_intents(before_intent, after_intent, surface)
        current = await self.current_receipt()
        if not changes:
            snapshot = await self.settings_commands.snapshot()
            return SettingsCommandResult(
                "no_change",
                snapshot,
                receipt=current,
                runtime_status="no_runtime_change",
                committed_revision=current.revision,
                no_op=True,
            )
        revision = expected_revision or current.revision
        command = settings_command_for_surface(surface, changes, revision, correlation_id)
        return await self.settings_commands.execute(command)

    async def execute_production_settings_delta(
        self,
        *,
        before: AppSettingsVNext,
        after: AppSettingsVNext,
        expected_revision: str | None = None,
        correlation_id: str | None = None,
    ) -> ProductionSettingsDispatchResult:
        current = await self.current_receipt()
        revision = expected_revision or current.revision
        surface_results: list[tuple[str, SettingsCommandResult]] = []
        operational_results: list[OperationalCommandResult] = []
        working_intent = before.intent
        secrets_rebound = False
        stopped_early = False
        for surface in _SURFACE_ORDER:
            changes = surface_changes_from_intents(working_intent, after.intent, surface)
            if not changes:
                continue
            result = await self.settings_commands.execute(
                settings_command_for_surface(surface, changes, revision, correlation_id)
            )
            surface_results.append((surface, result))
            if result.receipt is not None:
                revision = result.receipt.revision
                working_intent = result.receipt.envelope.intent
                current = result.receipt
                if "secrets_backend_rebind" in result.runtime_completed:
                    secrets_rebound = True
            if result.receipt is None or result.status not in {
                "applied",
                "degraded",
                "cancelled_committed",
                "cancelled_degraded",
                "no_change",
            }:
                stopped_early = True
                authoritative = await self.current_receipt()
                status, reconciliation, partial = _aggregate_dispatch_status(
                    surface_results,
                    operational_results,
                    stopped_early=True,
                )
                return ProductionSettingsDispatchResult(
                    status=status,
                    surface_results=tuple(surface_results),
                    operational_results=tuple(operational_results),
                    final_receipt=authoritative,
                    secrets_rebound=secrets_rebound,
                    reconciliation_required=reconciliation,
                    partial_commit=partial,
                )
            if result.status in {"degraded", "cancelled_degraded"}:
                # Keep overall degraded, but continue remaining surfaces for deterministic multi-surface outcome.
                continue

        for command in operational_commands_from_states(
            before.state,
            after.state,
            expected_revision=revision,
        ):
            command = replace(command, expected_revision=revision)
            op_result = await self.operational_commands.execute_operational(command)
            operational_results.append(op_result)
            if op_result.receipt is not None and not op_result.no_op:
                revision = op_result.receipt.revision
                current = op_result.receipt
            if op_result.receipt is None or op_result.status not in {
                "committed",
                "cancelled_committed",
                "no_change",
            }:
                stopped_early = True
                authoritative = await self.current_receipt()
                status, reconciliation, partial = _aggregate_dispatch_status(
                    surface_results,
                    operational_results,
                    stopped_early=True,
                )
                return ProductionSettingsDispatchResult(
                    status=status,
                    surface_results=tuple(surface_results),
                    operational_results=tuple(operational_results),
                    final_receipt=authoritative,
                    secrets_rebound=secrets_rebound,
                    reconciliation_required=reconciliation,
                    partial_commit=partial,
                )

        if not surface_results and not operational_results:
            snapshot = await self.settings_commands.snapshot()
            no_change = SettingsCommandResult(
                "no_change",
                snapshot,
                receipt=current,
                runtime_status="no_runtime_change",
                committed_revision=current.revision,
                no_op=True,
            )
            return ProductionSettingsDispatchResult(
                status="no_change",
                surface_results=(("none", no_change),),
                operational_results=(),
                final_receipt=current,
            )

        status, reconciliation, partial = _aggregate_dispatch_status(
            surface_results,
            operational_results,
            stopped_early=stopped_early,
        )
        authoritative = await self.current_receipt()
        return ProductionSettingsDispatchResult(
            status=status,
            surface_results=tuple(surface_results),
            operational_results=tuple(operational_results),
            final_receipt=authoritative,
            secrets_rebound=secrets_rebound,
            reconciliation_required=reconciliation,
            partial_commit=partial,
        )

    async def try_rebind_secrets_from_intent(self, receipt: SettingsCommitReceipt) -> str | None:
        from puripuly_heart.app.wiring import create_secret_store
        from puripuly_heart.config.settings import SecretsBackend, SecretsSettings

        try:
            backend = SecretsBackend(receipt.envelope.intent.secrets.backend)
        except Exception:
            backend = SecretsBackend.KEYRING
        settings = SecretsSettings(
            backend=backend,
            encrypted_file_path=receipt.envelope.intent.secrets.encrypted_file_path,
        )
        try:
            secrets = create_secret_store(settings, config_path=self._state_path)
        except ValueError as exc:
            message = str(exc)
            if "PURIPULY_HEART_SECRETS_PASSPHRASE" in message or "passphrase" in message.lower():
                return "secrets_passphrase_required"
            return "secrets_backend_rebind_failed"
        rebind = getattr(self.runtime_host, "rebind_secret_store", None)
        if callable(rebind):
            try:
                await rebind(secrets, receipt)
            except asyncio.CancelledError:
                authoritative = getattr(self.runtime_host, "secret_store_is_authoritative", None)
                if callable(authoritative) and authoritative(secrets):
                    self._publish_secret_store(secrets)
                raise
            except Exception:
                authoritative = getattr(self.runtime_host, "secret_store_is_authoritative", None)
                if callable(authoritative) and authoritative(secrets):
                    self._publish_secret_store(secrets)
                return "secrets_runtime_rebind_failed"
        self._publish_secret_store(secrets)
        return None

    def _publish_secret_store(self, secrets: object) -> None:
        self._secrets = secrets
        self._secret_port = SyncSecretStorePortAdapter(secrets)
        self.secret_commands = CanonicalSecretCommandService(self._secret_port, store_kind=secrets)


def create_canonical_command_composition(
    *,
    state_path: Path,
    runtime_host: object,
    secrets: object,
) -> CanonicalCommandComposition:
    from puripuly_heart.app.adapters.canonical_state_repository import (
        AsyncCanonicalStateRepository,
        CanonicalStateUnitOfWork,
    )

    repository = AsyncCanonicalStateRepository(CanonicalStateUnitOfWork(state_path))
    secret_port = SyncSecretStorePortAdapter(secrets)
    secret_commands = CanonicalSecretCommandService(secret_port, store_kind=secrets)
    runtime_apply = ProductionCanonicalRuntimeApply(host=runtime_host)
    settings_commands = CanonicalApplicationSettingsService(repository, runtime_apply)
    operational_commands = CanonicalOperationalStateService(repository)
    return CanonicalCommandComposition(
        settings_commands=settings_commands,
        operational_commands=operational_commands,
        secret_commands=secret_commands,
        runtime_host=runtime_host,
        runtime_apply=runtime_apply,
        activated_surfaces=PRODUCTION_SETTINGS_SURFACES,
        _secrets=secrets,
        _state_path=state_path,
        _secret_port=secret_port,
    )


def receipt_runtime_trace(result: SettingsCommandResult, *, surface: str) -> dict[str, object]:
    receipt = result.receipt
    if not isinstance(receipt, SettingsCommitReceipt):
        raise TypeError("settings command result must carry authoritative SettingsCommitReceipt")
    return {
        "command_status": result.status,
        "surface": surface,
        "receipt_revision": receipt.revision,
        "receipt_reason": receipt.reason,
        "receipt_correlation_id": receipt.correlation_id,
        "runtime_status": result.runtime_status,
        "runtime_completed": result.runtime_completed,
        "runtime_failed": result.runtime_failed,
        "runtime_skipped": result.runtime_skipped,
        "reconciliation_required": result.reconciliation_required,
        "committed_revision": result.committed_revision or result.snapshot.revision,
        "no_op": result.no_op,
    }


__all__ = [
    "CanonicalCommandComposition",
    "PRODUCTION_SETTINGS_SURFACES",
    "ProductionCanonicalRuntimeApply",
    "ProductionSettingsDispatchResult",
    "command_result_as_transaction",
    "create_canonical_command_composition",
    "dispatch_result_as_transaction",
    "operational_commands_from_states",
    "operational_result_as_transaction",
    "receipt_runtime_trace",
    "settings_command_for_surface",
    "surface_changes_from_intents",
    "surface_for_settings_command",
    "surface_for_settings_command_reason",
]
