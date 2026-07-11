from __future__ import annotations

from dataclasses import FrozenInstanceError
from inspect import getsource

import pytest

from puripuly_heart.core.runtime.provider_handle import ProviderRuntimeHandle
from puripuly_heart.core.runtime.provider_state import ProviderStateCell, ResourceRef


def test_initial_snapshot_is_immutable_and_matches_all_slots() -> None:
    llm = object()
    self_stt = object()
    peer_stt = object()
    cell = ProviderStateCell(llm=llm, self_stt=self_stt, peer_stt=peer_stt)
    snapshot = cell.snapshot()

    assert snapshot.epoch == 0
    assert snapshot.llm.provider is llm
    assert snapshot.self_stt.provider is self_stt
    assert snapshot.peer_stt.provider is peer_stt
    assert snapshot.llm.generation == snapshot.self_stt.generation == 0
    with pytest.raises(FrozenInstanceError):
        snapshot.epoch = 1  # type: ignore[misc]


def test_provider_handle_has_no_duplicate_provider_or_generation_authority() -> None:
    source = getsource(ProviderRuntimeHandle)

    assert "self._provider" not in source
    assert "self._generation" not in source


def test_shared_cell_handle_requires_exact_selected_provider() -> None:
    selected = object()
    cell = ProviderStateCell(llm=selected)

    with pytest.raises(ValueError, match="exactly match"):
        ProviderRuntimeHandle(
            name="llm",
            provider=object(),
            state_cell=cell,
            slot="llm",
        )

    handle = ProviderRuntimeHandle(
        name="llm",
        provider=selected,
        state_cell=cell,
        slot="llm",
    )
    assert handle.provider is selected


def test_changed_generations_and_epoch_are_monotonic_while_retained_slots_preserve() -> None:
    original = object()
    replacement = object()
    cell = ProviderStateCell(llm=original)

    cell.replace("llm", replacement)
    after_replace = cell.snapshot()
    cell.replace("llm", original)
    after_rollback = cell.snapshot()

    assert after_replace.epoch == 1
    assert after_replace.llm.generation == 1
    assert after_replace.self_stt.generation == 0
    assert after_rollback.epoch == 2
    assert after_rollback.llm.generation == 2
    assert after_rollback.llm.provider is original


def test_resource_refs_are_stable_authority_and_re_adoption_keeps_exact_ref() -> None:
    provider = object()
    cell = ProviderStateCell(llm=provider)
    original = cell.snapshot().llm.ref
    assert original is not None
    assert isinstance(original.identity, str)
    assert original.resource is provider

    replacement = ResourceRef("configured-provider", object())
    cell.transition({"llm": replacement})
    cell.transition({"llm": original})

    assert cell.snapshot().llm.ref is original
    assert cell.snapshot().llm.identity == original.identity
    assert cell.snapshot().llm.generation == 2


def test_atomic_transition_preserves_retained_slot_identity_and_generation() -> None:
    cell = ProviderStateCell(llm=object(), self_stt=object(), peer_stt=object())
    before = cell.snapshot()
    llm = ResourceRef("new-llm", object())
    peer = ResourceRef("new-peer", object())

    after = cell.transition({"llm": llm, "peer_stt": peer})

    assert after.epoch == before.epoch + 1
    assert after.llm.ref is llm
    assert after.peer_stt.ref is peer
    assert after.self_stt is before.self_stt


@pytest.mark.asyncio
async def test_all_handles_share_cell_staleness_and_task_lifecycle_does_not_change_epoch() -> None:
    providers = {slot: object() for slot in ("llm", "self_stt", "peer_stt")}
    cell = ProviderStateCell(**providers)
    handles = {
        slot: ProviderRuntimeHandle(
            name=slot,
            provider=provider,
            state_cell=cell,
            slot=slot,  # type: ignore[arg-type]
        )
        for slot, provider in providers.items()
    }
    captures = {slot: handle.current_provider_generation() for slot, handle in handles.items()}

    await handles["self_stt"].start()
    assert cell.snapshot().epoch == 0
    await handles["self_stt"].stop_ingress()
    assert cell.snapshot().epoch == 0

    for slot, handle in handles.items():
        old_provider, old_generation = captures[slot]
        await handle.replace_provider(object(), start=False)
        assert old_provider is not None
        assert not handle.is_current_provider_generation(
            provider=old_provider,
            generation=old_generation,
        )

    assert cell.snapshot().epoch == 3
