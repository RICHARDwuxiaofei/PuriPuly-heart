from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

import pytest

from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.runtime_resources import (
    RuntimeProviderResources,
    RuntimeResourceBuildError,
    RuntimeResourceInstallResult,
    RuntimeResourceReplacementPlan,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.app.services.resolved_runtime_adapter import (
    ResolvedRuntimeResourceAdapter,
    RuntimeResourceCleanupDiagnostic,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


class Resource:
    def __init__(
        self,
        *,
        fail_close: bool = False,
        cancel_close: bool = False,
        falsey: bool = False,
    ) -> None:
        self.close_calls = 0
        self.fail_close = fail_close
        self.cancel_close = cancel_close
        self.falsey = falsey

    def __bool__(self) -> bool:
        return not self.falsey

    async def close(self) -> None:
        self.close_calls += 1
        if self.cancel_close:
            raise asyncio.CancelledError
        if self.fail_close:
            raise RuntimeError("close failed")


def _config(concurrency: int, *, overlay_translation: bool = True):
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            translation=replace(settings.intent.translation, concurrency_limit=concurrency),
            overlay=replace(
                settings.intent.overlay,
                show_translation=overlay_translation,
            ),
        ),
    )
    return CanonicalRuntimeConfigResolver().resolve(
        SettingsCommitReceipt(settings, f"r{concurrency}", "test", "corr")
    )


@dataclass
class Factory:
    fail_build: bool = False
    cleanup_failure: bool = False

    def __post_init__(self) -> None:
        self.plans: list[RuntimeResourceReplacementPlan] = []
        self.built: list[RuntimeProviderResources] = []

    async def build_resources(self, config, plan):  # noqa: ANN001
        self.plans.append(plan)
        shared = Resource(fail_close=self.cleanup_failure)
        resources = RuntimeProviderResources(
            llm=shared if plan.llm == "replace" else None,
            self_stt=(
                Resource(fail_close=self.cleanup_failure) if plan.self_stt == "replace" else None
            ),
            peer_stt=shared if plan.peer_stt == "replace" else None,
        )
        self.built.append(resources)
        if self.fail_build:
            raise RuntimeResourceBuildError(resources)
        return resources


@dataclass
class Host:
    fail_install: bool = False
    cancel_install: bool = False
    active: RuntimeProviderResources = field(default_factory=RuntimeProviderResources)

    async def install_runtime_resources(self, plan, staged):  # noqa: ANN001
        if self.cancel_install:
            raise asyncio.CancelledError
        if self.fail_install:
            raise RuntimeError("install failed")
        previous = self.active

        def selected(action, staged_value, active_value):  # noqa: ANN001
            if action == "retain":
                return active_value
            if action == "clear":
                return None
            return staged_value

        self.active = RuntimeProviderResources(
            selected(plan.llm, staged.llm, previous.llm),
            selected(plan.self_stt, staged.self_stt, previous.self_stt),
            selected(plan.peer_stt, staged.peer_stt, previous.peer_stt),
        )
        return RuntimeResourceInstallResult(self.active, previous)


def _request(config):  # noqa: ANN001
    return ResolvedRuntimeActivationRequest(config, "revision", "test", "corr")


@pytest.mark.asyncio
async def test_irrelevant_config_change_is_noop_and_partial_replacement_retains_resources() -> None:
    factory = Factory()
    host = Host()
    adapter = ResolvedRuntimeResourceAdapter(factory, host)
    first = _config(2)
    await adapter.replace_runtime(_request(first))
    first_active = host.active

    await adapter.replace_runtime(_request(_config(2, overlay_translation=False)))
    assert len(factory.built) == 1
    assert host.active == first_active

    await adapter.replace_runtime(_request(_config(3, overlay_translation=False)))
    assert factory.plans[-1] == RuntimeResourceReplacementPlan("replace", "retain", "retain")
    assert host.active.self_stt is first_active.self_stt
    assert host.active.peer_stt is first_active.peer_stt
    assert first_active.llm.close_calls == 0
    assert first_active.self_stt.close_calls == 0


@pytest.mark.asyncio
async def test_duplicate_shared_displaced_handle_closes_exactly_once() -> None:
    shared = Resource()
    host = Host(active=RuntimeProviderResources(shared, shared, shared))
    adapter = ResolvedRuntimeResourceAdapter(Factory(), host)

    await adapter.replace_runtime(_request(_config(2)))

    assert shared.close_calls == 1


@pytest.mark.asyncio
async def test_falsey_retained_shared_identity_is_never_closed() -> None:
    shared = Resource(falsey=True)
    host = Host(active=RuntimeProviderResources(shared, shared, shared))

    class ReplaceLlmPlanner:
        def plan(self, current, target):  # noqa: ANN001
            _ = (current, target)
            return RuntimeResourceReplacementPlan("replace", "retain", "retain")

    adapter = ResolvedRuntimeResourceAdapter(Factory(), host, ReplaceLlmPlanner())
    await adapter.replace_runtime(_request(_config(2)))

    assert host.active.self_stt is shared
    assert host.active.peer_stt is shared
    assert shared.close_calls == 0


@pytest.mark.asyncio
async def test_explicit_clear_plan_closes_only_cleared_resource() -> None:
    class ClearPlanner:
        def plan(self, current, target):  # noqa: ANN001
            _ = (current, target)
            return RuntimeResourceReplacementPlan("retain", "clear", "retain")

    llm, self_stt, peer = Resource(), Resource(), Resource()
    host = Host(active=RuntimeProviderResources(llm, self_stt, peer))
    adapter = ResolvedRuntimeResourceAdapter(Factory(), host, ClearPlanner())

    await adapter.replace_runtime(_request(_config(2)))

    assert host.active == RuntimeProviderResources(llm, None, peer)
    assert self_stt.close_calls == 1
    assert llm.close_calls == peer.close_calls == 0


@pytest.mark.asyncio
async def test_build_and_install_primary_failures_survive_cleanup_failure() -> None:
    build_factory = Factory(fail_build=True, cleanup_failure=True)
    build_adapter = ResolvedRuntimeResourceAdapter(build_factory, Host())
    with pytest.raises(RuntimeResourceBuildError):
        await build_adapter.replace_runtime(_request(_config(2)))
    assert build_adapter.cleanup_diagnostics[0].operation == "build_failure"

    install_factory = Factory(cleanup_failure=True)
    install_adapter = ResolvedRuntimeResourceAdapter(install_factory, Host(fail_install=True))
    with pytest.raises(RuntimeError, match="install failed"):
        await install_adapter.replace_runtime(_request(_config(2)))
    assert install_adapter.cleanup_diagnostics[0].operation == "install_failure"


@pytest.mark.asyncio
async def test_displaced_close_failure_is_reported_after_successful_install() -> None:
    failing = Resource(fail_close=True)
    host = Host(active=RuntimeProviderResources(llm=failing))
    adapter = ResolvedRuntimeResourceAdapter(Factory(), host)

    with pytest.raises(RuntimeError, match="close failed"):
        await adapter.replace_runtime(_request(_config(2)))

    assert host.active.llm is not failing
    assert failing.close_calls == 1
    assert adapter.cleanup_diagnostics[0].operation == "displaced_close"


@pytest.mark.asyncio
async def test_install_cancellation_finishes_staged_cleanup_exactly_once() -> None:
    factory = Factory()
    host = Host(cancel_install=True)
    adapter = ResolvedRuntimeResourceAdapter(factory, host)

    with pytest.raises(asyncio.CancelledError):
        await adapter.replace_runtime(_request(_config(2)))

    staged = factory.built[-1]
    assert staged.llm.close_calls == 1
    assert staged.self_stt.close_calls == 1
    assert staged.peer_stt is staged.llm
    assert host.active == RuntimeProviderResources()


@pytest.mark.asyncio
async def test_displaced_cleanup_cancellation_closes_remaining_resources_once() -> None:
    cancelled = Resource(cancel_close=True)
    remaining = Resource()
    duplicate = remaining
    host = Host(active=RuntimeProviderResources(remaining, duplicate, cancelled))
    adapter = ResolvedRuntimeResourceAdapter(Factory(), host)

    with pytest.raises(asyncio.CancelledError):
        await adapter.replace_runtime(_request(_config(2)))

    assert cancelled.close_calls == 1
    assert remaining.close_calls == 1
    assert adapter.cleanup_diagnostics[0] == RuntimeResourceCleanupDiagnostic("displaced_close", 1)
