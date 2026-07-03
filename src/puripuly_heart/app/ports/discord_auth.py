from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from puripuly_heart.core.messages import (
    DiagnosticFieldValue,
    ErrorDiagnostics,
    UserMessageRef,
)


def _freeze_fields(
    values: Mapping[str, DiagnosticFieldValue],
) -> Mapping[str, DiagnosticFieldValue]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class DiscordAuthRequest:
    correlation_id: str | None
    metadata: Mapping[str, DiagnosticFieldValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_fields(self.metadata))


@dataclass(frozen=True, slots=True)
class DiscordAuthResult:
    succeeded: bool
    discord_user_id: str | None
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None
    authorization_code: str | None = field(default=None, repr=False)
    oauth_state: str | None = field(default=None, repr=False)
    redirect_uri: str | None = None
    issue_nonce: str | None = field(default=None, repr=False)
    hardware_hash: str | None = field(default=None, repr=False)
    hardware_hash_salt_version: int | None = None


class DiscordAuthPort(Protocol):
    async def start_discord_auth(self, request: DiscordAuthRequest) -> DiscordAuthResult: ...


__all__ = [
    "DiscordAuthPort",
    "DiscordAuthRequest",
    "DiscordAuthResult",
]
