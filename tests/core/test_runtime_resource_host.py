from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from puripuly_heart.app.ports.runtime_resources import (
    ResourceRef,
    RuntimeInstallFailure,
    RuntimeInstallSuccess,
    RuntimeResourceReplacementPlan,
    StagedRuntimeResources,
)
from puripuly_heart.core.orchestrator.hub import ClientHub


@dataclass
class Osc:
    messages: list[object] = field(default_factory=list)

    def enqueue(self, message: object) -> None:
        self.messages.append(message)

    def send_typing(self, on: bool) -> None:
        _ = on

    def set_typing_reason(self, reason: str, active: bool) -> None:
        _ = (reason, active)

    def send_immediate(self, text: str) -> bool:
        _ = text
        return True

    def process_due(self) -> None:
        return None


class Provider:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_real_hub_mixed_retain_replace_clear_reports_only_adopted_candidate() -> None:
    retained, replaced, cleared, candidate = (Provider() for _ in range(4))
    hub = ClientHub(stt=replaced, peer_stt=cleared, llm=retained, osc=Osc())
    prior = await hub.current_runtime_state()
    candidate_ref = ResourceRef("candidate-self", candidate)
    staged = StagedRuntimeResources(
        RuntimeResourceReplacementPlan("retain", "replace", "clear"),
        {"self_stt": candidate_ref},
    )

    result = await hub.install_runtime_resources(staged)

    assert isinstance(result, RuntimeInstallSuccess)
    assert result.active.slots["llm"] is prior.slots["llm"]
    assert result.active.slots["self_stt"] is candidate_ref
    assert "peer_stt" not in result.active.slots
    assert result.adopted_ids == {"candidate-self"}
    assert {ref.identity for ref in result.displaced} == {
        prior.slots["self_stt"].identity,
        prior.slots["peer_stt"].identity,
    }
    assert retained.close_calls == replaced.close_calls == cleared.close_calls == 0
    assert candidate.close_calls == 0


def _single_replace(candidate: Provider) -> StagedRuntimeResources:
    return StagedRuntimeResources(
        RuntimeResourceReplacementPlan("retain", "replace", "retain"),
        {"self_stt": ResourceRef("candidate-self", candidate)},
    )


def _assert_failure_ownership(
    result: RuntimeInstallFailure, current, prior, candidate: Provider
) -> None:
    assert result.active == current
    assert result.active.slots == prior.slots
    assert tuple(ref.resource for ref in result.returned_candidates) == (candidate,)
    assert result.displaced_prior == ()
    assert candidate.close_calls == 0
    assert all(ref.resource.close_calls == 0 for ref in prior.slots.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "cause"),
    [
        ("state", "runtime_install_postcommit_state_failed"),
        ("binding", "runtime_install_postcommit_binding_failed"),
        ("task", "runtime_install_postcommit_task_start_failed"),
    ],
)
async def test_real_hub_postcommit_stage_failure_restores_prior(
    monkeypatch: pytest.MonkeyPatch, stage: str, cause: str
) -> None:
    prior_provider, candidate = Provider(), Provider()
    hub = ClientHub(stt=prior_provider, llm=None, osc=Osc())
    prior = await hub.current_runtime_state()
    target = {
        "state": (hub._provider_state, "transition"),
        "binding": (ClientHub, "_bind_transition_handles"),
        "task": (ClientHub, "_start_transition_handles"),
    }[stage]
    owner, attribute = target
    original = getattr(owner, attribute)
    calls = 0

    def fail_once(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(stage)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, attribute, fail_once)
    result = await hub.install_runtime_resources(_single_replace(candidate))

    assert isinstance(result, RuntimeInstallFailure)
    assert result.restored is True
    assert result.cause_code == cause
    assert result.origin_cause_code is None
    _assert_failure_ownership(result, await hub.current_runtime_state(), prior, candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rollback_stage", "cause"),
    [
        ("quiesce", "runtime_install_rollback_quiesce_failed"),
        ("state", "runtime_install_rollback_state_failed"),
        ("task", "runtime_install_rollback_task_start_failed"),
    ],
)
async def test_real_hub_failed_rollback_reports_origin_and_exact_final_state(
    monkeypatch: pytest.MonkeyPatch, rollback_stage: str, cause: str
) -> None:
    prior_provider, candidate = Provider(), Provider()
    hub = ClientHub(stt=prior_provider, llm=None, osc=Osc())
    prior = await hub.current_runtime_state()
    start_original = ClientHub._start_transition_handles
    start_calls = 0

    def fail_initial_start(self, slots) -> None:  # noqa: ANN001
        nonlocal start_calls
        start_calls += 1
        if start_calls == 1 or rollback_stage == "task":
            raise RuntimeError("task start")
        start_original(self, slots)

    monkeypatch.setattr(ClientHub, "_start_transition_handles", fail_initial_start)
    if rollback_stage == "quiesce":
        handle = hub.provider_runtime_handles["self_stt"]
        handle_type = type(handle)
        original_quiesce = handle_type._quiesce_for_transition
        calls = 0

        async def fail_rollback_quiesce(self) -> None:  # noqa: ANN001
            nonlocal calls
            if self is handle:
                calls += 1
            if self is handle and calls == 2:
                raise RuntimeError("rollback quiesce")
            await original_quiesce(self)

        monkeypatch.setattr(handle_type, "_quiesce_for_transition", fail_rollback_quiesce)
    elif rollback_stage == "state":
        original_transition = hub._provider_state.transition
        calls = 0

        def fail_rollback_state(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("rollback state")
            return original_transition(*args, **kwargs)

        monkeypatch.setattr(hub._provider_state, "transition", fail_rollback_state)

    result = await hub.install_runtime_resources(_single_replace(candidate))
    current = await hub.current_runtime_state()

    assert isinstance(result, RuntimeInstallFailure)
    assert result.active == current
    assert result.restored is False
    assert result.cause_code == cause
    assert result.origin_cause_code == "runtime_install_postcommit_task_start_failed"
    assert candidate.close_calls == prior_provider.close_calls == 0
    assert not any(
        ref in current.slots.values()
        for ref in (*result.returned_candidates, *result.displaced_prior)
    )
    if rollback_stage == "task":
        assert current.slots == prior.slots
    else:
        assert current.slots["self_stt"].resource is candidate
