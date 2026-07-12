from __future__ import annotations

import asyncio
from dataclasses import replace

from puripuly_heart.app.ports.application_settings import (
    ApplicationSettingsSnapshot,
    LocalExtraBodyValue,
    OperationalCommandResult,
    OperationalStateCommand,
    OperationalStateSnapshot,
    SettingsCommand,
    SettingsCommandResult,
    SettingValue,
    StringListMapValue,
    StringMapValue,
)
from puripuly_heart.app.ports.canonical_state_repository import (
    AsyncCanonicalStateRepositoryPort,
    CanonicalEnvelopeSnapshot,
    CanonicalRepositoryCancelled,
    CanonicalRepositoryConflict,
    CanonicalRepositoryError,
)
from puripuly_heart.app.ports.owned_async import OwnedFailure, settle_owned
from puripuly_heart.app.ports.runtime_apply import RuntimeApplyPort, RuntimeApplyRequest
from puripuly_heart.app.services.application_settings_codecs import (
    FIELD_CODECS,
    OPERATIONAL_CODECS,
    CodecKind,
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
            try:
                outcome = await settle_owned(
                    self._runtime.apply_runtime(RuntimeApplyRequest(receipt))
                )
                applied = outcome.value
                cancellation_count += outcome.cancellation_count
                status = "applied" if applied.status == "applied" else "degraded"
                diagnostics = applied.diagnostics
            except OwnedFailure as exc:
                status = "degraded"
                diagnostics = _diagnostics("runtime_apply", "runtime_exception")
                return SettingsCommandResult(
                    "cancelled_degraded",
                    committed_snapshot,
                    diagnostics=diagnostics,
                    cancellation_count=cancellation_count + exc.cancellation_count,
                    committed_revision=receipt.revision,
                )
            except Exception:
                status = "degraded"
                diagnostics = _diagnostics("runtime_apply", "runtime_exception")
            result = SettingsCommandResult(status, committed_snapshot, diagnostics=diagnostics)
            if cancellation_count:
                return SettingsCommandResult(
                    "cancelled_degraded" if status == "degraded" else "cancelled_committed",
                    committed_snapshot,
                    diagnostics=diagnostics,
                    cancellation_count=cancellation_count,
                    committed_revision=receipt.revision,
                )
            return result


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
                )
            if envelope.revision != command.expected_revision:
                return OperationalCommandResult(
                    "conflict",
                    _operational_snapshot(envelope),
                    _diagnostics("commit", "revision_conflict"),
                )
            try:
                codec = next(
                    codec
                    for codec in OPERATIONAL_CODECS.values()
                    if isinstance(command, codec.command_type)
                )
                path, value = codec.encode(command)
                state = _set(envelope.operational_state, path, value)
                receipt = await self._repository.commit_operational_state(
                    state,  # type: ignore[arg-type]
                    expected_revision=command.expected_revision,
                    reason=type(command).__name__,
                    correlation_id=None,
                )
            except (StopIteration, TypeError, ValueError):
                return OperationalCommandResult(
                    "invalid",
                    _operational_snapshot(envelope),
                    _diagnostics("validate", "invalid_command"),
                )
            except CanonicalRepositoryCancelled as exc:
                if exc.committed is None:
                    raise
                raise
            except CanonicalRepositoryConflict as exc:
                snapshot = _operational_snapshot(exc.authoritative)
                return OperationalCommandResult(
                    "conflict", snapshot, _diagnostics("rebase", "revision_conflict")
                )
            except CanonicalRepositoryError as exc:
                return OperationalCommandResult(
                    exc.status,
                    _operational_snapshot(envelope),
                    _diagnostics("commit", exc.status),
                )
            committed = CanonicalEnvelopeSnapshot(
                receipt.envelope.intent, receipt.envelope.state, receipt.revision
            )
            return OperationalCommandResult("committed", _operational_snapshot(committed))


__all__ = ["CanonicalApplicationSettingsService", "CanonicalOperationalStateService"]
