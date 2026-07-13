from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from puripuly_heart.app.ports.application_settings import (
    GithubStarClickedCommand,
    GithubStarEligibleLaunchCountCommand,
    GithubStarOpenedCommand,
    GithubStarTranslationSuccessObservedCommand,
    OperationalStateCommandPort,
    OperationalStateQueryPort,
    OperationalStateSnapshot,
)
from puripuly_heart.app.ports.github_star_prompt_application import (
    GithubStarPromptPresentation,
    GithubStarPromptResult,
    GithubStarPromptStatus,
)
from puripuly_heart.core.runtime.github_star_prompt import GithubStarPromptRuntime


class GithubStarPromptApplication:
    def __init__(
        self,
        *,
        commands: OperationalStateCommandPort,
        queries: OperationalStateQueryPort,
        eligibility: Callable[[], Awaitable[bool]],
        runtime: GithubStarPromptRuntime,
        eligible_launch_threshold: int = 3,
        recency_days: int = 14,
    ) -> None:
        self._commands = commands
        self._queries = queries
        self._eligibility = eligibility
        self._runtime = runtime
        self._eligible_launch_threshold = eligible_launch_threshold
        self._recency = timedelta(days=recency_days)

    async def presentation(self) -> GithubStarPromptPresentation:
        snapshot = await self._queries.operational_snapshot()
        return await self._presentation(snapshot)

    async def record_eligible_launch(self) -> GithubStarPromptResult:
        current = await self.presentation()
        if not current.eligible:
            return GithubStarPromptResult(GithubStarPromptStatus.INELIGIBLE, current)
        if current.clicked or current.show_count > 0:
            return GithubStarPromptResult(GithubStarPromptStatus.SUPPRESSED, current)
        count = min(current.eligible_launch_count + 1, self._eligible_launch_threshold)
        return await self._execute(
            GithubStarEligibleLaunchCountCommand(count, current.operational_revision)
        )

    async def run_delayed_launch(self, delay_s: float) -> GithubStarPromptResult:
        async def run(_generation: int) -> bool:
            result = await self.record_eligible_launch()
            if not result.presentation.should_show:
                return False
            await asyncio.sleep(max(0.0, delay_s))
            return (await self.presentation()).should_show

        try:
            task = self._runtime.start_launch_prompt(run)
        except RuntimeError:
            return GithubStarPromptResult(
                GithubStarPromptStatus.CANCELLED,
                await self.presentation(),
                "launch_already_active",
            )
        try:
            shown = await task
        except asyncio.CancelledError:
            return GithubStarPromptResult(
                GithubStarPromptStatus.CANCELLED,
                await self.presentation(),
                "launch_cancelled",
            )
        presentation = await self.presentation()
        return GithubStarPromptResult(
            GithubStarPromptStatus.APPLIED if shown else GithubStarPromptStatus.SUPPRESSED,
            presentation,
        )

    async def cancel_launch(self) -> None:
        task = self._runtime.launch_prompt_task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def record_opened(self) -> GithubStarPromptResult:
        current = await self.presentation()
        if not current.should_show:
            return GithubStarPromptResult(GithubStarPromptStatus.SUPPRESSED, current)
        opened = await self._commands.execute_operational(
            GithubStarOpenedCommand(
                self._timestamp(), current.show_count + 1, current.operational_revision
            )
        )
        return await self._from_command(opened.status, opened.snapshot)

    async def record_clicked(self) -> GithubStarPromptResult:
        current = await self.presentation()
        return await self._execute(GithubStarClickedCommand(True, current.operational_revision))

    def observe_translation_success(self) -> bool:
        existing = self._runtime.translation_success_task
        if existing is not None and not existing.done():
            return False
        self._runtime.start_translation_success_observation(self._record_translation_success())
        return True

    async def _record_translation_success(self) -> bool:
        current = await self.presentation()
        if current.translation_success_observed or not current.eligible:
            return False
        result = await self._execute(
            GithubStarTranslationSuccessObservedCommand(True, current.operational_revision)
        )
        return result.status == GithubStarPromptStatus.APPLIED

    async def _execute(self, command) -> GithubStarPromptResult:  # noqa: ANN001
        result = await self._commands.execute_operational(command)
        return await self._from_command(result.status, result.snapshot)

    async def _from_command(
        self, status: str, snapshot: OperationalStateSnapshot
    ) -> GithubStarPromptResult:
        mapped = {
            "applied": GithubStarPromptStatus.APPLIED,
            "no_change": GithubStarPromptStatus.APPLIED,
            "conflict": GithubStarPromptStatus.CONFLICT,
            "cancelled": GithubStarPromptStatus.CANCELLED,
        }.get(status, GithubStarPromptStatus.FAILED)
        return GithubStarPromptResult(mapped, await self._presentation(snapshot))

    async def _presentation(
        self, snapshot: OperationalStateSnapshot
    ) -> GithubStarPromptPresentation:
        values = dict(snapshot.leaves)

        def get(name: str, default=None):  # noqa: ANN001, ANN202
            return values.get(tuple(name.split(".")), default)

        eligible = await self._eligibility()
        clicked = bool(get("github_star_prompt.clicked", False))
        show_count = max(0, int(get("github_star_prompt.show_count", 0) or 0))
        eligible_launch_count = max(0, int(get("github_star_prompt.eligible_launch_count", 0) or 0))
        last_shown_at = get("github_star_prompt.last_shown_at")
        gate = show_count > 0 or eligible_launch_count >= self._eligible_launch_threshold
        should_show = eligible and not clicked and gate and self._recency_elapsed(last_shown_at)
        return GithubStarPromptPresentation(
            eligible,
            should_show,
            clicked,
            show_count,
            eligible_launch_count,
            last_shown_at if isinstance(last_shown_at, str) else None,
            bool(get("github_star_prompt.translation_success_observed", False)),
            snapshot.revision,
        )

    def _recency_elapsed(self, value: object) -> bool:
        if not isinstance(value, str) or not value:
            return True
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - parsed.astimezone(timezone.utc) >= self._recency

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def close(self) -> None:
        await self._runtime.close()


__all__ = ["GithubStarPromptApplication"]
