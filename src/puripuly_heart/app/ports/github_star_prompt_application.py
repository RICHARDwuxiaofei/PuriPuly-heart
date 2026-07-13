from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class GithubStarPromptStatus(str, Enum):
    APPLIED = "applied"
    INELIGIBLE = "ineligible"
    SUPPRESSED = "suppressed"
    CONFLICT = "conflict"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GithubStarPromptPresentation:
    eligible: bool
    should_show: bool
    clicked: bool
    show_count: int
    eligible_launch_count: int
    last_shown_at: str | None
    translation_success_observed: bool
    operational_revision: str


@dataclass(frozen=True, slots=True)
class GithubStarPromptResult:
    status: GithubStarPromptStatus
    presentation: GithubStarPromptPresentation
    detail_code: str | None = None


class GithubStarPromptApplicationPort(Protocol):
    async def presentation(self) -> GithubStarPromptPresentation: ...
    async def record_eligible_launch(self) -> GithubStarPromptResult: ...
    async def run_delayed_launch(self, delay_s: float) -> GithubStarPromptResult: ...
    async def cancel_launch(self) -> None: ...
    async def record_opened(self) -> GithubStarPromptResult: ...
    async def record_clicked(self) -> GithubStarPromptResult: ...
    def observe_translation_success(self) -> bool: ...
    async def close(self) -> None: ...


__all__ = [
    "GithubStarPromptApplicationPort",
    "GithubStarPromptPresentation",
    "GithubStarPromptResult",
    "GithubStarPromptStatus",
]
