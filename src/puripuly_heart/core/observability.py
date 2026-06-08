from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Protocol

from puripuly_heart.core.messages import (
    ContentPolicy,
    DiagnosticCategory,
    DiagnosticFieldValue,
    DiagnosticVisibility,
    ErrorDiagnostics,
    Severity,
    UserMessageRef,
)

ProviderObservationOutcome = Literal["success", "failure", "skipped", "degraded"]
PROVIDER_OBSERVATION_OUTCOME_SUCCESS: Final[ProviderObservationOutcome] = "success"
PROVIDER_OBSERVATION_OUTCOME_FAILURE: Final[ProviderObservationOutcome] = "failure"
PROVIDER_OBSERVATION_OUTCOME_SKIPPED: Final[ProviderObservationOutcome] = "skipped"
PROVIDER_OBSERVATION_OUTCOME_DEGRADED: Final[ProviderObservationOutcome] = "degraded"
PROVIDER_OBSERVATION_OUTCOMES: Final[tuple[ProviderObservationOutcome, ...]] = (
    PROVIDER_OBSERVATION_OUTCOME_SUCCESS,
    PROVIDER_OBSERVATION_OUTCOME_FAILURE,
    PROVIDER_OBSERVATION_OUTCOME_SKIPPED,
    PROVIDER_OBSERVATION_OUTCOME_DEGRADED,
)

ConversationRecordChannel = Literal["self"]


def _freeze_fields(
    values: Mapping[str, DiagnosticFieldValue],
) -> Mapping[str, DiagnosticFieldValue]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class RuntimeLogEvent:
    category: DiagnosticCategory
    severity: Severity
    visibility: DiagnosticVisibility
    content_policy: ContentPolicy
    correlation_id: str | None
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None
    fields: Mapping[str, DiagnosticFieldValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _freeze_fields(self.fields))


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    category: DiagnosticCategory
    severity: Severity
    visibility: DiagnosticVisibility
    content_policy: ContentPolicy
    correlation_id: str | None
    diagnostics: ErrorDiagnostics | None
    fields: Mapping[str, DiagnosticFieldValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _freeze_fields(self.fields))


@dataclass(frozen=True, slots=True)
class ProviderObservationEvent:
    provider: str
    operation: str
    outcome: ProviderObservationOutcome
    correlation_id: str | None
    diagnostics: ErrorDiagnostics | None
    fields: Mapping[str, DiagnosticFieldValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _freeze_fields(self.fields))


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    utterance_id: str
    speaker_channel: ConversationRecordChannel
    transcript_text: str | None
    translation_text: str | None
    source_language: str | None
    target_language: str | None
    metadata: Mapping[str, DiagnosticFieldValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_fields(self.metadata))


@dataclass(frozen=True, slots=True)
class PersistedDiagnosticRecord:
    diagnostic: DiagnosticEvent
    storage_key: str | None
    metadata: Mapping[str, DiagnosticFieldValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_fields(self.metadata))


class RuntimeLogSink(Protocol):
    async def emit_runtime_log(self, event: RuntimeLogEvent) -> None: ...


class DiagnosticsSink(Protocol):
    async def emit_diagnostic(self, event: DiagnosticEvent) -> None: ...


class ProviderObservationSink(Protocol):
    async def emit_provider_observation(
        self,
        event: ProviderObservationEvent,
    ) -> None: ...


class ConversationRecordSink(Protocol):
    async def record_conversation(self, record: ConversationRecord) -> None: ...


class PersistedDiagnosticStore(Protocol):
    async def persist_diagnostic(
        self,
        record: PersistedDiagnosticRecord,
    ) -> None: ...


__all__ = [
    "ConversationRecord",
    "ConversationRecordChannel",
    "ConversationRecordSink",
    "DiagnosticEvent",
    "DiagnosticsSink",
    "PROVIDER_OBSERVATION_OUTCOMES",
    "PROVIDER_OBSERVATION_OUTCOME_DEGRADED",
    "PROVIDER_OBSERVATION_OUTCOME_FAILURE",
    "PROVIDER_OBSERVATION_OUTCOME_SKIPPED",
    "PROVIDER_OBSERVATION_OUTCOME_SUCCESS",
    "PersistedDiagnosticRecord",
    "PersistedDiagnosticStore",
    "ProviderObservationEvent",
    "ProviderObservationOutcome",
    "ProviderObservationSink",
    "RuntimeLogEvent",
    "RuntimeLogSink",
]
