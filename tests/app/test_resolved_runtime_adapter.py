from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

import pytest

from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.runtime_resources import (
    InstalledRuntimeState,
    ResourceRef,
    RuntimeInstallFailure,
    RuntimeInstallSuccess,
    RuntimeResourceBuildError,
    RuntimeResourceInstallError,
    RuntimeResourceReplacementPlan,
    StagedRuntimeResources,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.app.services.resolved_runtime_adapter import ResolvedRuntimeResourceAdapter
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


class Resource:
    def __init__(self, fail: bool = False, cancel: bool = False, fail_once: bool = False) -> None:
        self.close_calls = 0
        self.fail = fail
        self.cancel = cancel
        self.fail_once = fail_once

    async def close(self) -> None:
        self.close_calls += 1
        if self.cancel:
            raise asyncio.CancelledError
        if self.fail or (self.fail_once and self.close_calls == 1):
            raise RuntimeError("close failed")


def _config(value: int):
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            translation=replace(settings.intent.translation, concurrency_limit=value),
        ),
    )
    return CanonicalRuntimeConfigResolver().resolve(
        SettingsCommitReceipt(settings, str(value), "r", "c")
    )


def _request(value: int):
    return ResolvedRuntimeActivationRequest(_config(value), str(value), "r", "c")


@dataclass
class Factory:
    staged: StagedRuntimeResources
    fail: bool = False

    async def build_resources(self, config, plan):  # noqa: ANN001
        _ = config
        staged = StagedRuntimeResources(
            plan,
            {
                slot: ref
                for slot, ref in self.staged.candidates.items()
                if getattr(plan, slot) == "replace"
            },
        )
        if self.fail:
            raise RuntimeResourceBuildError(staged)
        return staged


@dataclass
class Host:
    result: object

    async def install_runtime_resources(self, staged):  # noqa: ANN001
        _ = staged
        return self.result

    async def current_runtime_state(self):
        return self.result.active if self.result is not None else InstalledRuntimeState({})


def _staged(ref: ResourceRef, plan=None):  # noqa: ANN001
    plan = plan or RuntimeResourceReplacementPlan("replace", "replace", "replace")
    return StagedRuntimeResources(
        plan,
        {slot: ref for slot in ("llm", "self_stt", "peer_stt") if getattr(plan, slot) == "replace"},
    )


class ReplaceAllPlanner:
    def plan(self, current, target):  # noqa: ANN001
        _ = (current, target)
        return RuntimeResourceReplacementPlan("replace", "replace", "replace")


@pytest.mark.parametrize(
    "values",
    [
        {
            "restored": False,
            "cause_code": "runtime_install_validation_failed",
        },
        {
            "restored": False,
            "cause_code": "runtime_install_precommit_quiesce_failed",
        },
        {
            "restored": False,
            "cause_code": "runtime_install_rollback_state_failed",
        },
        {
            "restored": True,
            "cause_code": "runtime_install_rollback_task_start_failed",
            "origin_cause_code": "runtime_install_postcommit_task_start_failed",
        },
        {
            "restored": True,
            "cause_code": "runtime_install_postcommit_binding_failed",
            "origin_cause_code": "runtime_install_postcommit_binding_failed",
        },
    ],
)
def test_runtime_install_failure_rejects_invalid_restoration_contract(
    values,
) -> None:  # noqa: ANN001
    with pytest.raises(ValueError):
        RuntimeInstallFailure(
            active=InstalledRuntimeState({}),
            returned_candidates=(),
            **values,
        )


def test_resource_identity_validation_and_shared_slots() -> None:
    resource = Resource()
    shared = _staged(ResourceRef("shared", resource))
    assert len(shared.candidates) == 3
    assert not hasattr(InstalledRuntimeState({"llm": ResourceRef("shared", resource)}), "close")
    with pytest.raises(ValueError):
        StagedRuntimeResources(
            RuntimeResourceReplacementPlan("replace", "replace", "retain"),
            {"llm": ResourceRef("same", Resource()), "self_stt": ResourceRef("same", Resource())},
        )


@pytest.mark.asyncio
async def test_success_closes_displaced_and_unadopted_once_but_never_active_overlap() -> None:
    candidate, displaced = Resource(), Resource()
    cref = ResourceRef("candidate", candidate)
    old_ref = ResourceRef("old", displaced)
    initial = RuntimeInstallSuccess(
        InstalledRuntimeState({slot: old_ref for slot in ("llm", "self_stt", "peer_stt")}),
        frozenset({"old"}),
    )
    adapter = ResolvedRuntimeResourceAdapter(
        Factory(_staged(old_ref)), Host(initial), ReplaceAllPlanner()
    )
    await adapter.replace_runtime(_request(1))
    result = RuntimeInstallSuccess(
        InstalledRuntimeState({slot: cref for slot in ("llm", "self_stt", "peer_stt")}),
        frozenset({"candidate"}),
        displaced=(old_ref, old_ref),
    )
    adapter.factory = Factory(_staged(cref))
    adapter.host = Host(result)
    await adapter.replace_runtime(_request(2))
    assert displaced.close_calls == 1
    assert candidate.close_calls == 0


@pytest.mark.asyncio
async def test_restored_failure_closes_only_returned_candidates_and_preserves_cache() -> None:
    first = ResourceRef("first", Resource())
    active = InstalledRuntimeState({slot: first for slot in ("llm", "self_stt", "peer_stt")})
    success = RuntimeInstallSuccess(active, frozenset({"first"}))
    adapter = ResolvedRuntimeResourceAdapter(Factory(_staged(first)), Host(success))
    await adapter.replace_runtime(_request(2))
    returned = ResourceRef("returned", Resource())
    adapter.factory = Factory(_staged(returned))
    adapter.host = Host(
        RuntimeInstallFailure(active, (returned,), True, "runtime_install_precommit_quiesce_failed")
    )
    with pytest.raises(RuntimeResourceInstallError):
        await adapter.replace_runtime(_request(3))
    assert returned.resource.close_calls == 1
    assert first.resource.close_calls == 0
    assert adapter._active_config == _config(2)


@pytest.mark.asyncio
async def test_incomplete_rollback_invalidates_cache_and_cleanup_failure_keeps_primary() -> None:
    returned = ResourceRef("returned", Resource(fail=True))
    failure = RuntimeInstallFailure(
        InstalledRuntimeState({}),
        (returned,),
        False,
        "runtime_install_rollback_state_failed",
        origin_cause_code="runtime_install_postcommit_state_failed",
    )
    adapter = ResolvedRuntimeResourceAdapter(Factory(_staged(returned)), Host(failure))
    with pytest.raises(RuntimeResourceInstallError) as caught:
        await adapter.replace_runtime(_request(2))
    assert caught.value.cause_code == "runtime_install_rollback_state_failed"
    assert adapter._active_config is None
    assert adapter.cleanup_diagnostics[0].failed_resources == 1


@pytest.mark.asyncio
async def test_build_failure_cleanup_primary_priority() -> None:
    ref = ResourceRef("candidate", Resource(fail=True))
    adapter = ResolvedRuntimeResourceAdapter(Factory(_staged(ref), fail=True), Host(None))
    with pytest.raises(RuntimeResourceBuildError):
        await adapter.replace_runtime(_request(2))
    assert ref.resource.close_calls == 1


@pytest.mark.asyncio
async def test_retained_plan_and_same_config_noop_do_not_build_or_close() -> None:
    ref = ResourceRef("candidate", Resource())
    success = RuntimeInstallSuccess(
        InstalledRuntimeState({slot: ref for slot in ("llm", "self_stt", "peer_stt")}),
        frozenset({"candidate"}),
    )
    factory = Factory(_staged(ref))
    adapter = ResolvedRuntimeResourceAdapter(factory, Host(success))
    await adapter.replace_runtime(_request(2))
    await adapter.replace_runtime(_request(2))
    assert ref.resource.close_calls == 0


@pytest.mark.asyncio
async def test_host_exception_queries_authority_and_closes_only_inactive_candidates() -> None:
    active_resource = Resource()
    active_ref = ResourceRef("active", active_resource)
    candidate = ResourceRef("candidate", Resource())

    class RaisingHost:
        async def install_runtime_resources(self, staged):  # noqa: ANN001
            _ = staged
            raise RuntimeError("host failed")

        async def current_runtime_state(self):
            return InstalledRuntimeState({"llm": active_ref})

    adapter = ResolvedRuntimeResourceAdapter(Factory(_staged(candidate)), RaisingHost())
    with pytest.raises(RuntimeError, match="host failed"):
        await adapter.replace_runtime(_request(2))
    assert candidate.resource.close_calls == 1
    assert active_resource.close_calls == 0
    assert adapter._active_config is None


@pytest.mark.asyncio
async def test_install_cancellation_waits_for_partial_adoption_settlement() -> None:
    refs = {
        "llm": ResourceRef("llm", Resource()),
        "self_stt": ResourceRef("self", Resource(fail=True)),
        "peer_stt": ResourceRef("peer", Resource(fail=True)),
    }
    staged = StagedRuntimeResources(
        RuntimeResourceReplacementPlan("replace", "replace", "replace"), refs
    )
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedHost:
        async def install_runtime_resources(self, value):  # noqa: ANN001
            _ = value
            entered.set()
            await release.wait()
            return RuntimeInstallSuccess(
                InstalledRuntimeState({"llm": refs["llm"]}),
                frozenset({"llm"}),
                unadopted=(refs["self_stt"], refs["peer_stt"]),
            )

        async def current_runtime_state(self):
            return InstalledRuntimeState({"llm": refs["llm"]})

    adapter = ResolvedRuntimeResourceAdapter(Factory(staged), DelayedHost(), ReplaceAllPlanner())
    task = asyncio.create_task(adapter.replace_runtime(_request(2)))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert refs["llm"].resource.close_calls == 0
    assert refs["self_stt"].resource.close_calls == 1
    assert refs["peer_stt"].resource.close_calls == 1
    assert adapter.cleanup_diagnostics[-1].operation == "invalid_install_result"


@pytest.mark.asyncio
async def test_pending_cancellation_keeps_displaced_cleanup_failure_secondary() -> None:
    prior = ResourceRef("prior", Resource())
    initial = RuntimeInstallSuccess(
        InstalledRuntimeState({slot: prior for slot in ("llm", "self_stt", "peer_stt")}),
        frozenset({"prior"}),
    )
    adapter = ResolvedRuntimeResourceAdapter(
        Factory(_staged(prior)), Host(initial), ReplaceAllPlanner()
    )
    await adapter.replace_runtime(_request(1))
    prior.resource.fail = True
    candidate = ResourceRef("candidate", Resource())
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedSuccessHost:
        async def install_runtime_resources(self, staged):  # noqa: ANN001
            _ = staged
            entered.set()
            await release.wait()
            return RuntimeInstallSuccess(
                InstalledRuntimeState(
                    {slot: candidate for slot in ("llm", "self_stt", "peer_stt")}
                ),
                frozenset({"candidate"}),
                displaced=(prior,),
            )

        async def current_runtime_state(self):
            return InstalledRuntimeState(
                {slot: candidate for slot in ("llm", "self_stt", "peer_stt")}
            )

    adapter.factory = Factory(_staged(candidate))
    adapter.host = DelayedSuccessHost()
    task = asyncio.create_task(adapter.replace_runtime(_request(2)))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert prior.resource.close_calls == 1
    assert candidate.resource.close_calls == 0
    assert adapter.cleanup_diagnostics[-1].operation == "ownership_return"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["retained", "object_mismatch", "unadopted_active"])
async def test_invalid_replace_outcomes_are_rejected_and_candidates_settled(case: str) -> None:
    staged_resource = Resource()
    staged_ref = ResourceRef("candidate", staged_resource)
    if case == "retained":
        active_ref = ResourceRef("prior", Resource())
        adopted = frozenset()
        unadopted = (staged_ref,)
    elif case == "object_mismatch":
        active_ref = ResourceRef("candidate", Resource())
        adopted = frozenset({"candidate"})
        unadopted = ()
    else:
        active_ref = staged_ref
        adopted = frozenset()
        unadopted = (staged_ref,)
    result = RuntimeInstallSuccess(
        InstalledRuntimeState({"llm": active_ref, "self_stt": active_ref, "peer_stt": active_ref}),
        adopted,
        unadopted=unadopted,
    )
    adapter = ResolvedRuntimeResourceAdapter(
        Factory(_staged(staged_ref)), Host(result), ReplaceAllPlanner()
    )
    adapter._ownership_state_known = True
    with pytest.raises(RuntimeResourceInstallError) as caught:
        await adapter.replace_runtime(_request(2))
    assert caught.value.cause_code == "invalid_install_result"
    if active_ref.resource is not staged_resource:
        assert staged_resource.close_calls == 1


@pytest.mark.asyncio
async def test_invalid_result_settlement_closes_omitted_displaced_prior_resource() -> None:
    prior = ResourceRef("prior", Resource())
    initial = RuntimeInstallSuccess(
        InstalledRuntimeState({slot: prior for slot in ("llm", "self_stt", "peer_stt")}),
        frozenset({"prior"}),
    )
    adapter = ResolvedRuntimeResourceAdapter(
        Factory(_staged(prior)), Host(initial), ReplaceAllPlanner()
    )
    await adapter.replace_runtime(_request(1))
    candidate = ResourceRef("candidate", Resource())
    invalid = RuntimeInstallSuccess(
        InstalledRuntimeState({slot: candidate for slot in ("llm", "self_stt", "peer_stt")}),
        frozenset({"candidate"}),
        displaced=(),
    )
    adapter.factory = Factory(_staged(candidate))
    adapter.host = Host(invalid)
    with pytest.raises(RuntimeResourceInstallError):
        await adapter.replace_runtime(_request(2))
    assert prior.resource.close_calls == 1
    assert candidate.resource.close_calls == 0


@pytest.mark.asyncio
async def test_host_exception_query_failure_preserves_primary_and_closes_nothing_unsafe() -> None:
    candidate = ResourceRef("candidate", Resource())

    class QueryFailingHost:
        async def install_runtime_resources(self, staged):  # noqa: ANN001
            _ = staged
            raise RuntimeError("primary host failure")

        async def current_runtime_state(self):
            raise RuntimeError("secondary query failure")

    adapter = ResolvedRuntimeResourceAdapter(Factory(_staged(candidate)), QueryFailingHost())
    adapter._ownership_state_known = True
    with pytest.raises(RuntimeError, match="primary host failure"):
        await adapter.replace_runtime(_request(2))
    assert candidate.resource.close_calls == 0
    assert adapter._active_config is None
    assert adapter.cleanup_diagnostics[-1].operation == "host_state_query"
    assert len(adapter._pending_settlement) == 1

    with pytest.raises(RuntimeResourceInstallError) as repeated:
        await adapter.replace_runtime(_request(2))
    assert repeated.value.cause_code == "host_state_query_failed"
    assert adapter._active_config is None
    assert len(adapter._pending_settlement) == 1

    adopted = ResourceRef("candidate", candidate.resource)
    success = RuntimeInstallSuccess(
        InstalledRuntimeState({slot: adopted for slot in ("llm", "self_stt", "peer_stt")}),
        frozenset({"candidate"}),
    )
    adapter.host = Host(success)
    await adapter.replace_runtime(_request(2))
    assert adapter._active_config == _config(2)
    assert adapter._pending_settlement == {}


@pytest.mark.asyncio
async def test_failure_active_same_identity_different_object_must_return_staged_object() -> None:
    staged = ResourceRef("same", Resource())
    active = ResourceRef("same", Resource())
    failure = RuntimeInstallFailure(
        InstalledRuntimeState({"llm": active}),
        (),
        False,
        "runtime_install_rollback_state_failed",
        origin_cause_code="runtime_install_postcommit_state_failed",
    )
    adapter = ResolvedRuntimeResourceAdapter(Factory(_staged(staged)), Host(failure))
    adapter._ownership_state_known = True
    with pytest.raises(RuntimeResourceInstallError) as caught:
        await adapter.replace_runtime(_request(2))
    assert caught.value.cause_code == "invalid_install_result"
    assert staged.resource.close_calls == 1
    assert active.resource.close_calls == 0


@pytest.mark.asyncio
async def test_cross_transaction_same_identity_different_object_is_rejected_before_install() -> (
    None
):
    active = ResourceRef("same", Resource())
    staged = ResourceRef("same", Resource())

    class NeverHost:
        called = False

        async def install_runtime_resources(self, value):  # noqa: ANN001
            self.called = True
            raise AssertionError("install must not run")

        async def current_runtime_state(self):
            return InstalledRuntimeState({"llm": active})

    host = NeverHost()
    adapter = ResolvedRuntimeResourceAdapter(Factory(_staged(staged)), host)
    adapter._active_state = InstalledRuntimeState({"llm": active})
    with pytest.raises(RuntimeResourceInstallError) as caught:
        await adapter.replace_runtime(_request(2))
    assert caught.value.cause_code == "staged_identity_conflict"
    assert host.called is False
    assert staged.resource.close_calls == 1
    assert active.resource.close_calls == 0


@pytest.mark.asyncio
async def test_unknown_state_refresh_cancellation_retains_pending_ownership() -> None:
    candidate = ResourceRef("candidate", Resource())

    class CancelQueryHost:
        async def install_runtime_resources(self, staged):  # noqa: ANN001
            _ = staged
            raise AssertionError("install must not run")

        async def current_runtime_state(self):
            raise asyncio.CancelledError

    adapter = ResolvedRuntimeResourceAdapter(Factory(_staged(candidate)), CancelQueryHost())
    adapter._ownership_state_known = False
    adapter._pending_settlement[(candidate.identity, id(candidate.resource))] = candidate
    with pytest.raises(asyncio.CancelledError):
        await adapter.replace_runtime(_request(2))
    assert len(adapter._pending_settlement) == 1
    assert candidate.resource.close_calls == 0
    assert adapter._active_config is None


@pytest.mark.asyncio
async def test_pending_cleanup_failure_retries_on_later_known_state_activation() -> None:
    pending = ResourceRef("pending", Resource(fail_once=True))
    active_state = InstalledRuntimeState({})
    no_candidates = StagedRuntimeResources(
        RuntimeResourceReplacementPlan("retain", "retain", "retain"), {}
    )
    adapter = ResolvedRuntimeResourceAdapter(
        Factory(no_candidates), Host(RuntimeInstallSuccess(active_state, frozenset()))
    )
    adapter._active_config = _config(2)
    adapter._pending_settlement[(pending.identity, id(pending.resource))] = pending

    await adapter.replace_runtime(_request(2))
    assert pending.resource.close_calls == 1
    assert len(adapter._pending_settlement) == 1

    await adapter.replace_runtime(_request(2))
    assert pending.resource.close_calls == 2
    assert adapter._pending_settlement == {}


@pytest.mark.asyncio
async def test_ownership_return_close_failure_is_pending_and_retried_on_noop() -> None:
    prior = ResourceRef("prior", Resource(fail_once=True))
    candidate = ResourceRef("candidate", Resource())
    initial = RuntimeInstallSuccess(
        InstalledRuntimeState({slot: prior for slot in ("llm", "self_stt", "peer_stt")}),
        frozenset({"prior"}),
    )
    adapter = ResolvedRuntimeResourceAdapter(
        Factory(_staged(prior)), Host(initial), ReplaceAllPlanner()
    )
    await adapter.replace_runtime(_request(1))
    success = RuntimeInstallSuccess(
        InstalledRuntimeState({slot: candidate for slot in ("llm", "self_stt", "peer_stt")}),
        frozenset({"candidate"}),
        displaced=(prior,),
    )
    adapter.factory = Factory(_staged(candidate))
    adapter.host = Host(success)

    with pytest.raises(RuntimeError, match="close failed"):
        await adapter.replace_runtime(_request(2))
    assert prior.resource.close_calls == 1
    assert len(adapter._pending_settlement) == 1

    adapter.planner = type(
        "NoopPlanner",
        (),
        {
            "plan": lambda self, current, target: RuntimeResourceReplacementPlan(
                "retain", "retain", "retain"
            )
        },
    )()
    await adapter.replace_runtime(_request(2))
    assert prior.resource.close_calls == 2
    assert adapter._pending_settlement == {}


@pytest.mark.asyncio
async def test_build_failure_never_closes_candidate_already_active_in_other_slot() -> None:
    resource = Resource()
    active_ref = ResourceRef("shared", resource)
    staged = _staged(active_ref)
    active = InstalledRuntimeState({"peer_stt": active_ref})
    adapter = ResolvedRuntimeResourceAdapter(
        Factory(staged, fail=True), Host(RuntimeInstallSuccess(active, frozenset()))
    )
    adapter._active_state = active

    with pytest.raises(RuntimeResourceBuildError):
        await adapter.replace_runtime(_request(2))

    assert resource.close_calls == 0


@pytest.mark.asyncio
async def test_host_exception_query_cancellation_leaves_no_query_task() -> None:
    candidate = ResourceRef("candidate", Resource())
    query_started = asyncio.Event()

    class BlockingQueryHost:
        async def install_runtime_resources(self, staged):  # noqa: ANN001
            _ = staged
            raise RuntimeError("install failed")

        async def current_runtime_state(self):
            query_started.set()
            await asyncio.Event().wait()

    adapter = ResolvedRuntimeResourceAdapter(Factory(_staged(candidate)), BlockingQueryHost())
    adapter._ownership_state_known = True
    task = asyncio.create_task(adapter.replace_runtime(_request(2)))
    await query_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert candidate.resource.close_calls == 0
    assert len(adapter._pending_settlement) == 1
    assert not any(
        other is not asyncio.current_task()
        and not other.done()
        and "current_runtime_state" in repr(other.get_coro())
        for other in asyncio.all_tasks()
    )


@pytest.mark.asyncio
async def test_build_failure_query_cancellation_leaves_no_query_task() -> None:
    candidate = ResourceRef("candidate", Resource())
    query_started = asyncio.Event()

    class BlockingQueryHost:
        async def install_runtime_resources(self, staged):  # noqa: ANN001
            raise AssertionError(staged)

        async def current_runtime_state(self):
            query_started.set()
            await asyncio.Event().wait()

    adapter = ResolvedRuntimeResourceAdapter(
        Factory(_staged(candidate), fail=True), BlockingQueryHost()
    )
    adapter._ownership_state_known = True
    task = asyncio.create_task(adapter.replace_runtime(_request(2)))
    await query_started.wait()
    task.cancel()
    with pytest.raises(RuntimeResourceBuildError):
        await task
    await asyncio.sleep(0)
    assert candidate.resource.close_calls == 0
    assert len(adapter._pending_settlement) == 1
    assert not any(
        other is not asyncio.current_task()
        and not other.done()
        and "current_runtime_state" in repr(other.get_coro())
        for other in asyncio.all_tasks()
    )
