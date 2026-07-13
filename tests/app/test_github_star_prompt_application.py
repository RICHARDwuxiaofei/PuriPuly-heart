from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from puripuly_heart.app.ports.application_settings import (
    GithubStarOpenedCommand,
    OperationalCommandResult,
    OperationalStateSnapshot,
)
from puripuly_heart.app.ports.github_star_prompt_application import GithubStarPromptStatus
from puripuly_heart.app.services.github_star_prompt_application import GithubStarPromptApplication
from puripuly_heart.core.runtime.github_star_prompt import GithubStarPromptRuntime


class OperationalState:
    def __init__(self) -> None:
        self.snapshot = OperationalStateSnapshot(
            (
                (("github_star_prompt", "clicked"), False),
                (("github_star_prompt", "show_count"), 0),
                (("github_star_prompt", "eligible_launch_count"), 3),
                (("github_star_prompt", "last_shown_at"), None),
                (("github_star_prompt", "translation_success_observed"), False),
            ),
            "r1",
        )
        self.commands: list[object] = []

    async def operational_snapshot(self) -> OperationalStateSnapshot:
        return self.snapshot

    async def execute_operational(self, command) -> OperationalCommandResult:  # noqa: ANN001
        self.commands.append(command)
        if isinstance(command, GithubStarOpenedCommand):
            values = dict(self.snapshot.leaves)
            values[("github_star_prompt", "last_shown_at")] = command.last_shown_at
            values[("github_star_prompt", "show_count")] = command.show_count
            self.snapshot = OperationalStateSnapshot(tuple(values.items()), "r2")
        return OperationalCommandResult("applied", self.snapshot)


@pytest.mark.asyncio
async def test_opened_is_one_atomic_operational_command() -> None:
    state = OperationalState()
    owner = GithubStarPromptApplication(
        commands=state,
        queries=state,
        eligibility=lambda: asyncio.sleep(0, result=True),
        runtime=GithubStarPromptRuntime(),
    )

    result = await owner.record_opened()

    assert result.status == GithubStarPromptStatus.APPLIED
    assert len(state.commands) == 1
    assert isinstance(state.commands[0], GithubStarOpenedCommand)
    assert result.presentation.show_count == 1
    await owner.close()


@pytest.mark.asyncio
async def test_delayed_launch_cancels_and_shutdown_settles_owned_task() -> None:
    state = OperationalState()
    state.snapshot = replace(state.snapshot, revision="r3")
    runtime = GithubStarPromptRuntime()
    owner = GithubStarPromptApplication(
        commands=state,
        queries=state,
        eligibility=lambda: asyncio.sleep(0, result=True),
        runtime=runtime,
    )

    launch = asyncio.create_task(owner.run_delayed_launch(60))
    await asyncio.sleep(0)
    await owner.cancel_launch()
    result = await launch
    await owner.close()

    assert result.status == GithubStarPromptStatus.CANCELLED
    assert runtime.launch_prompt_task is None
    assert runtime.is_closed
