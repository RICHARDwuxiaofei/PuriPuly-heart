from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class ManagedAuthenticationStatus(str, Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ManagedAuthenticationPrompt(str, Enum):
    DISCORD = "discord"
    QQ = "qq"


class ManagedAuthenticationBrowserPort(Protocol):
    async def reopen(self) -> bool: ...
    async def cancel(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ManagedAuthenticationPresentation:
    action: str
    prompt: ManagedAuthenticationPrompt
    connection_state: str
    browser_reopen_available: bool
    referral_bonus_applied: bool
    trial_remaining_percent: int | None = None
    referral_id: str | None = None
    pass_status: str | None = None
    generation: int = 0
    callback_received: bool = False


@dataclass(frozen=True, slots=True)
class StartDiscordManagedAuthentication:
    referral_id: str | None = None


@dataclass(frozen=True, slots=True)
class StartQqManagedAuthentication:
    qq_identity: str
    credential: EphemeralSecretLease


@dataclass(slots=True, repr=False)
class EphemeralSecretLease:
    _secret: bytearray = field(repr=False)
    _consumed: bool = field(default=False, init=False, repr=False)

    @classmethod
    def from_text(cls, value: str) -> EphemeralSecretLease:
        return cls(bytearray(value.encode("utf-8")))

    def consume(self) -> str:
        if self._consumed:
            raise RuntimeError("secret lease already consumed")
        self._consumed = True
        try:
            return bytes(self._secret).decode("utf-8")
        finally:
            self.clear()

    def clear(self) -> None:
        self._consumed = True
        for index in range(len(self._secret)):
            self._secret[index] = 0
        self._secret.clear()

    @property
    def consumed(self) -> bool:
        return self._consumed


@dataclass(frozen=True, slots=True)
class ManagedAuthenticationResult:
    status: ManagedAuthenticationStatus
    presentation: ManagedAuthenticationPresentation
    detail_code: str | None = None


class ManagedAuthenticationApplicationPort(Protocol):
    def subscribe_presentation(
        self, listener: Callable[[ManagedAuthenticationPresentation], None]
    ) -> Callable[[], None]: ...
    async def presentation(self) -> ManagedAuthenticationPresentation: ...
    async def start_discord(
        self, command: StartDiscordManagedAuthentication
    ) -> ManagedAuthenticationResult: ...
    async def start_qq(
        self, command: StartQqManagedAuthentication
    ) -> ManagedAuthenticationResult: ...
    async def reopen_discord_browser(self) -> ManagedAuthenticationResult: ...
    async def cancel(self) -> ManagedAuthenticationResult: ...
    def clear_pending(self) -> None: ...
    async def close(self) -> None: ...


__all__ = [
    "ManagedAuthenticationApplicationPort",
    "ManagedAuthenticationBrowserPort",
    "EphemeralSecretLease",
    "ManagedAuthenticationPresentation",
    "ManagedAuthenticationPrompt",
    "ManagedAuthenticationResult",
    "ManagedAuthenticationStatus",
    "StartDiscordManagedAuthentication",
    "StartQqManagedAuthentication",
]
