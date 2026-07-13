from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from puripuly_heart.app.ports._settings_values import freeze_settings_values
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.messages import ErrorDiagnostics, UserMessageRef


class SettingsNotInitializedError(RuntimeError):
    pass


class SettingsRevisionConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    values: Mapping[str, object]
    revision: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", freeze_settings_values(self.values))


@dataclass(frozen=True, slots=True)
class SettingsCommitRequest:
    values: Mapping[str, object]
    expected_revision: str
    reason: str | None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", freeze_settings_values(self.values))


@dataclass(frozen=True, slots=True)
class SettingsCommitResult:
    succeeded: bool
    snapshot: SettingsSnapshot | None
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None
    receipt: SettingsCommitReceipt | None = None


@dataclass(frozen=True, slots=True, init=False)
class SettingsCommitReceipt:
    _envelope: AppSettingsVNext = field(repr=False)
    revision: str
    reason: str | None
    correlation_id: str | None

    def __init__(
        self,
        envelope: AppSettingsVNext,
        revision: str,
        reason: str | None,
        correlation_id: str | None,
    ) -> None:
        object.__setattr__(self, "_envelope", copy.deepcopy(envelope))
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "correlation_id", correlation_id)

    @property
    def envelope(self) -> AppSettingsVNext:
        return copy.deepcopy(self._envelope)


@dataclass(frozen=True, slots=True)
class SettingsInitializeRequest:
    envelope: AppSettingsVNext
    reason: str | None
    correlation_id: str | None


class SettingsRepositoryPort(Protocol):
    async def load(self) -> SettingsSnapshot: ...

    async def load_receipt(self) -> SettingsCommitReceipt: ...

    async def initialize(self, request: SettingsInitializeRequest) -> SettingsCommitReceipt: ...

    async def save(self, request: SettingsCommitRequest) -> SettingsCommitResult: ...


__all__ = [
    "SettingsCommitRequest",
    "SettingsCommitReceipt",
    "SettingsCommitResult",
    "SettingsInitializeRequest",
    "SettingsRepositoryPort",
    "SettingsSnapshot",
    "SettingsNotInitializedError",
    "SettingsRevisionConflict",
]
