from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.core.messages import ErrorDiagnostics, UserMessageRef


@dataclass(frozen=True, slots=True)
class SecretReadResult:
    key: str
    value: str | None
    revision: str | None
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None


@dataclass(frozen=True, slots=True)
class SecretWriteResult:
    succeeded: bool
    key: str
    revision: str | None
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None


@dataclass(frozen=True, slots=True)
class SecretSnapshot:
    key: str
    value: str | None
    revision: str | None
    existed: bool


class SecretStorePort(Protocol):
    async def get_secret(self, key: str) -> SecretReadResult: ...

    async def set_secret(self, key: str, value: str) -> SecretWriteResult: ...

    async def clear_secret(self, key: str) -> SecretWriteResult: ...

    async def snapshot_secret(self, key: str) -> SecretSnapshot: ...

    async def restore_secret(self, snapshot: SecretSnapshot) -> SecretWriteResult: ...


__all__ = [
    "SecretReadResult",
    "SecretSnapshot",
    "SecretStorePort",
    "SecretWriteResult",
]
