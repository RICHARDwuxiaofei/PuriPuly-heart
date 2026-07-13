from __future__ import annotations

import asyncio
from dataclasses import replace

from puripuly_heart.app.ports.application_settings import (
    ApplicationSettingsSnapshot,
    GithubStarOpenedCommand,
    LocalExtraBodyValue,
    OperationalCommandResult,
    OperationalStateCommand,
    OperationalStateSnapshot,
    OverlayOscOutputSettingsCommand,
    SettingChange,
    SettingsCommand,
    SettingsCommandResult,
    SettingsSurface,
    SettingValue,
    StringListMapValue,
    StringMapValue,
    SttLanguageAudioSettingsCommand,
    TranslationProviderSettingsCommand,
    UiPromptClipboardSettingsCommand,
)
from puripuly_heart.app.ports.canonical_state_repository import (
    AsyncCanonicalStateRepositoryPort,
    CanonicalEnvelopeSnapshot,
    CanonicalRepositoryCancelled,
    CanonicalRepositoryConflict,
    CanonicalRepositoryError,
)
from puripuly_heart.app.ports.owned_async import OwnedFailure, settle_owned
from puripuly_heart.app.ports.runtime_apply import (
    RuntimeApplyExecution,
    RuntimeApplyPort,
    RuntimeApplyRequest,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.application_settings_codecs import (
    FIELD_CODECS,
    OPERATIONAL_CODECS,
    CodecKind,
)


def settings_presentation_codecs():
    return tuple(FIELD_CODECS.values())


from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
    PersistedOperationalState,
    UserIntentSettings,
)
from puripuly_heart.core.messages import ErrorDiagnostics


def _diagnostics(operation: str, code: str) -> ErrorDiagnostics:
    return ErrorDiagnostics(
        "canonical_application_settings",
        operation,
        code,
        "transaction",
        "diagnostic_only",
        "metadata_only",
        None,
        None,
        {},
    )


def _get(value: object, path: tuple[str, ...]) -> object:
    for part in path:
        value = getattr(value, part)
    return value


def _set(value: object, path: tuple[str, ...], leaf: object) -> object:
    if len(path) == 1:
        return replace(value, **{path[0]: leaf})
    child = _set(getattr(value, path[0]), path[1:], leaf)
    return replace(value, **{path[0]: child})


def _codec_value(kind: CodecKind, value: object) -> SettingValue:
    if kind == CodecKind.STRING_LIST:
        return tuple(value)  # type: ignore[arg-type,return-value]
    if kind == CodecKind.STRING_MAP:
        return StringMapValue(tuple(sorted(value.items())))  # type: ignore[union-attr]
    if kind == CodecKind.STRING_LIST_MAP:
        return StringListMapValue(
            tuple(sorted((key, tuple(items)) for key, items in value.items()))  # type: ignore[union-attr]
        )
    if kind == CodecKind.LOCAL_EXTRA_BODY:
        from puripuly_heart.app.ports.application_settings import JsonScalarEntry

        return LocalExtraBodyValue(
            tuple(JsonScalarEntry(key, item) for key, item in sorted(value.items()))  # type: ignore[union-attr]
        )
    return value  # type: ignore[return-value]


def _canonical_value(kind: CodecKind, value: SettingValue) -> object:
    if kind == CodecKind.STRING_LIST:
        return list(value)  # type: ignore[arg-type]
    if kind == CodecKind.STRING_MAP:
        return dict(value.entries)  # type: ignore[union-attr]
    if kind == CodecKind.STRING_LIST_MAP:
        return {key: list(items) for key, items in value.entries}  # type: ignore[union-attr]
    if kind == CodecKind.LOCAL_EXTRA_BODY:
        return {entry.key: entry.value for entry in value.entries}  # type: ignore[union-attr]
    return value


def surface_changes_from_intents(
    before: UserIntentSettings,
    after: UserIntentSettings,
    surface: str,
) -> tuple[SettingChange, ...]:
    owner = {
        "translation_provider": SettingsSurface.TRANSLATION_PROVIDER,
        "stt_language_audio": SettingsSurface.STT_LANGUAGE_AUDIO,
        "overlay_osc_output": SettingsSurface.OVERLAY_OSC_OUTPUT,
        "ui_prompt_clipboard_state": SettingsSurface.UI_PROMPT_CLIPBOARD,
    }[surface]
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
        commands.append(codec.command_type(after_value, expected_revision))  # type: ignore[call-arg,misc]
    return tuple(commands)


def _intent_snapshot(envelope: CanonicalEnvelopeSnapshot) -> ApplicationSettingsSnapshot:
    leaves = []
    for field, codec in FIELD_CODECS.items():
        values = tuple(
            _codec_value(codec.kind, _get(envelope.intent, path)) for path in codec.canonical_paths
        )
        leaves.append(((field.value,), codec.decode(values)))
    return ApplicationSettingsSnapshot(tuple(leaves), envelope.revision)


def _operational_snapshot(envelope: CanonicalEnvelopeSnapshot) -> OperationalStateSnapshot:
    leaves = tuple(
        (codec.canonical_path, _get(envelope.operational_state, codec.canonical_path))
        for codec in OPERATIONAL_CODECS.values()
    )
    return OperationalStateSnapshot(leaves, envelope.revision)


def _empty_intent_snapshot(revision: str = "unavailable") -> ApplicationSettingsSnapshot:
    return ApplicationSettingsSnapshot((), revision)


def _empty_operational_snapshot(revision: str = "unavailable") -> OperationalStateSnapshot:
    return OperationalStateSnapshot((), revision)


def _envelope_receipt(
    envelope: CanonicalEnvelopeSnapshot,
    *,
    reason: str | None,
    correlation_id: str | None,
) -> SettingsCommitReceipt:
    return SettingsCommitReceipt(
        AppSettingsVNext(intent=envelope.intent, state=envelope.operational_state),
        envelope.revision,
        reason,
        correlation_id,
    )


def surface_for_settings_command(command: SettingsCommand) -> str:
    return {
        TranslationProviderSettingsCommand: "translation_provider",
        SttLanguageAudioSettingsCommand: "stt_language_audio",
        OverlayOscOutputSettingsCommand: "overlay_osc_output",
        UiPromptClipboardSettingsCommand: "ui_prompt_clipboard_state",
    }[type(command)]


async def _execute_runtime(
    runtime: RuntimeApplyPort,
    request: RuntimeApplyRequest,
) -> RuntimeApplyExecution:
    apply_execution = getattr(runtime, "apply_runtime_execution", None)
    if callable(apply_execution):
        return await apply_execution(request)
    result = await runtime.apply_runtime(request)
    return RuntimeApplyExecution(result=result)


class CanonicalApplicationSettingsService:
    def __init__(
        self,
        repository: AsyncCanonicalStateRepositoryPort,
        runtime: RuntimeApplyPort,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._lock = asyncio.Lock()

    async def snapshot(self) -> ApplicationSettingsSnapshot:
        return _intent_snapshot(await self._repository.load())

    async def execute(self, command: SettingsCommand) -> SettingsCommandResult:
        async with self._lock:
            try:
                envelope = await self._repository.load()
            except asyncio.CancelledError:
                raise
            except Exception:
                return SettingsCommandResult(
                    "read_failed",
                    _empty_intent_snapshot(),
                    diagnostics=_diagnostics("read", "read_failed"),
                )
            if envelope.revision != command.expected_revision:
                return SettingsCommandResult(
                    "conflict",
                    _intent_snapshot(envelope),
                    diagnostics=_diagnostics("commit", "revision_conflict"),
                )
            try:
                intent: object = envelope.intent
                for change in command.changes:
                    codec = FIELD_CODECS[change.field]
                    for path, value in codec.encode(change.value):
                        intent = _set(intent, path, _canonical_value(codec.kind, value))
                _intent_snapshot(
                    CanonicalEnvelopeSnapshot(
                        intent, envelope.operational_state, "pending"  # type: ignore[arg-type]
                    )
                )
            except (KeyError, TypeError, ValueError):
                return SettingsCommandResult(
                    "invalid",
                    _intent_snapshot(envelope),
                    diagnostics=_diagnostics("validate", "invalid_command"),
                )
            before_receipt = _envelope_receipt(
                envelope,
                reason=None,
                correlation_id=command.correlation_id,
            )
            cancellation_count = 0
            try:
                receipt = await self._repository.commit_intent(
                    intent,  # type: ignore[arg-type]
                    expected_revision=command.expected_revision,
                    reason=type(command).__name__,
                    correlation_id=command.correlation_id,
                )
            except CanonicalRepositoryCancelled as exc:
                if exc.committed is None:
                    raise
                receipt = exc.committed
                cancellation_count = exc.cancellation_count
            except CanonicalRepositoryConflict as exc:
                snapshot = _intent_snapshot(exc.authoritative)
                return SettingsCommandResult(
                    "conflict", snapshot, diagnostics=_diagnostics("rebase", "revision_conflict")
                )
            except CanonicalRepositoryError as exc:
                return SettingsCommandResult(
                    exc.status,
                    _intent_snapshot(envelope),
                    diagnostics=_diagnostics("commit", exc.status),
                )
            committed = CanonicalEnvelopeSnapshot(
                receipt.envelope.intent, receipt.envelope.state, receipt.revision
            )
            committed_snapshot = _intent_snapshot(committed)
            surface = surface_for_settings_command(command)
            request = RuntimeApplyRequest(
                receipt=receipt,
                before=before_receipt,
                surface=surface,
            )
            try:
                outcome = await settle_owned(_execute_runtime(self._runtime, request))
                execution = outcome.value
                cancellation_count += outcome.cancellation_count
                applied = execution.result
                status = "applied" if applied.status == "applied" else "degraded"
                diagnostics = applied.diagnostics
                runtime_status = applied.status
                completed = execution.completed
                failed = execution.failed
                skipped = execution.skipped
                reconciliation = execution.reconciliation_required
            except OwnedFailure as exc:
                return SettingsCommandResult(
                    "cancelled_degraded",
                    committed_snapshot,
                    diagnostics=_diagnostics("runtime_apply", "runtime_exception"),
                    cancellation_count=cancellation_count + exc.cancellation_count,
                    committed_revision=receipt.revision,
                    receipt=receipt,
                    runtime_status="failed",
                    reconciliation_required=True,
                )
            except Exception:
                return SettingsCommandResult(
                    "degraded",
                    committed_snapshot,
                    diagnostics=_diagnostics("runtime_apply", "runtime_exception"),
                    committed_revision=receipt.revision,
                    receipt=receipt,
                    runtime_status="failed",
                    reconciliation_required=True,
                )
            if cancellation_count:
                return SettingsCommandResult(
                    "cancelled_degraded" if status == "degraded" else "cancelled_committed",
                    committed_snapshot,
                    diagnostics=diagnostics,
                    cancellation_count=cancellation_count,
                    committed_revision=receipt.revision,
                    receipt=receipt,
                    runtime_status=runtime_status,
                    runtime_completed=completed,
                    runtime_failed=failed,
                    runtime_skipped=skipped,
                    reconciliation_required=reconciliation or status == "degraded",
                )
            return SettingsCommandResult(
                status,
                committed_snapshot,
                diagnostics=diagnostics,
                committed_revision=receipt.revision,
                receipt=receipt,
                runtime_status=runtime_status,
                runtime_completed=completed,
                runtime_failed=failed,
                runtime_skipped=skipped,
                reconciliation_required=reconciliation or status == "degraded",
            )


class CanonicalOperationalStateService:
    def __init__(self, repository: AsyncCanonicalStateRepositoryPort) -> None:
        self._repository = repository
        self._lock = asyncio.Lock()

    async def operational_snapshot(self) -> OperationalStateSnapshot:
        return _operational_snapshot(await self._repository.load())

    async def execute_operational(
        self, command: OperationalStateCommand
    ) -> OperationalCommandResult:
        async with self._lock:
            try:
                envelope = await self._repository.load()
            except asyncio.CancelledError:
                raise
            except Exception:
                return OperationalCommandResult(
                    "read_failed",
                    _empty_operational_snapshot(),
                    _diagnostics("read", "read_failed"),
                    runtime_outcome="no_runtime_change",
                )
            if envelope.revision != command.expected_revision:
                return OperationalCommandResult(
                    "conflict",
                    _operational_snapshot(envelope),
                    _diagnostics("commit", "revision_conflict"),
                    runtime_outcome="no_runtime_change",
                )
            try:
                if isinstance(command, GithubStarOpenedCommand):
                    state = _set(
                        envelope.operational_state,
                        ("github_star_prompt", "last_shown_at"),
                        command.last_shown_at,
                    )
                    state = _set(
                        state,
                        ("github_star_prompt", "show_count"),
                        command.show_count,
                    )
                    receipt = await self._repository.commit_operational_state(
                        state,  # type: ignore[arg-type]
                        expected_revision=command.expected_revision,
                        reason=type(command).__name__,
                        correlation_id=None,
                    )
                    cancellation_count = 0
                else:
                    codec = next(
                        codec
                        for codec in OPERATIONAL_CODECS.values()
                        if isinstance(command, codec.command_type)
                    )
                    path, value = codec.encode(command)
                    current_value = _get(envelope.operational_state, path)
                    current_receipt = _envelope_receipt(
                        envelope,
                        reason=type(command).__name__,
                        correlation_id=None,
                    )
                    if current_value == value:
                        return OperationalCommandResult(
                            "no_change",
                            _operational_snapshot(envelope),
                            receipt=current_receipt,
                            runtime_outcome="no_runtime_change",
                            committed_revision=envelope.revision,
                            no_op=True,
                        )
                    state = _set(envelope.operational_state, path, value)
                    receipt = await self._repository.commit_operational_state(
                        state,  # type: ignore[arg-type]
                        expected_revision=command.expected_revision,
                        reason=type(command).__name__,
                        correlation_id=None,
                    )
                    cancellation_count = 0
            except (StopIteration, TypeError, ValueError):
                return OperationalCommandResult(
                    "invalid",
                    _operational_snapshot(envelope),
                    _diagnostics("validate", "invalid_command"),
                    runtime_outcome="no_runtime_change",
                )
            except CanonicalRepositoryCancelled as exc:
                if exc.committed is None:
                    raise
                receipt = exc.committed
                cancellation_count = exc.cancellation_count
            except CanonicalRepositoryConflict as exc:
                snapshot = _operational_snapshot(exc.authoritative)
                return OperationalCommandResult(
                    "conflict",
                    snapshot,
                    _diagnostics("rebase", "revision_conflict"),
                    runtime_outcome="no_runtime_change",
                )
            except CanonicalRepositoryError as exc:
                return OperationalCommandResult(
                    exc.status,
                    _operational_snapshot(envelope),
                    _diagnostics("commit", exc.status),
                    runtime_outcome="no_runtime_change",
                )
            committed = CanonicalEnvelopeSnapshot(
                receipt.envelope.intent, receipt.envelope.state, receipt.revision
            )
            status = "cancelled_committed" if cancellation_count else "committed"
            return OperationalCommandResult(
                status,
                _operational_snapshot(committed),
                receipt=receipt,
                runtime_outcome="no_runtime_change",
                committed_revision=receipt.revision,
                cancellation_count=cancellation_count,
                reconciliation_required=bool(cancellation_count),
            )


__all__ = [
    "CanonicalApplicationSettingsService",
    "CanonicalOperationalStateService",
    "surface_for_settings_command",
]
