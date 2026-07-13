from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TranslationCommandStatus = Literal["applied", "unavailable", "closed"]


@dataclass(frozen=True, slots=True)
class SetTranslationEnabled:
    enabled: bool


@dataclass(frozen=True, slots=True)
class TranslationRuntimeSnapshot:
    desired_enabled: bool
    effective_enabled: bool
    provider_available: bool
    provider_generation: int


@dataclass(frozen=True, slots=True)
class TranslationCommandResult:
    status: TranslationCommandStatus
    snapshot: TranslationRuntimeSnapshot


__all__ = [
    "SetTranslationEnabled",
    "TranslationCommandResult",
    "TranslationCommandStatus",
    "TranslationRuntimeSnapshot",
]
