from __future__ import annotations

import pytest

from puripuly_heart.app.adapters.overlay_runtime_effects import ProductionVrcMicrophoneEffects
from puripuly_heart.app.services.overlay_osc_application_runtime import OverlayOscApplicationRuntime


class ReceiverRuntime:
    def __init__(self, *, fail_start: bool = False, fail_close: bool = False) -> None:
        self.receiver = None
        self.fail_start = fail_start
        self.fail_close = fail_close
        self.starts = 0
        self.stops = 0
        self.closes = 0

    async def start(self) -> object:
        self.starts += 1
        if self.fail_start:
            raise OSError("receiver unavailable")
        self.receiver = object()
        return self.receiver

    async def stop(self) -> None:
        self.stops += 1
        self.receiver = None

    async def close(self) -> None:
        self.closes += 1
        self.receiver = None
        if self.fail_close:
            raise RuntimeError("receiver close failed")


def effects(receiver: ReceiverRuntime | None = None) -> ProductionVrcMicrophoneEffects:
    return ProductionVrcMicrophoneEffects(receiver=receiver or ReceiverRuntime())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_init_pipeline_initializes_vrc_state_and_gate() -> None:
    owner = effects()
    assert owner.gate.state is owner.state
    assert owner.gate.enabled is False
    assert await owner.apply_vrc_microphone_intercept(True) is True
    assert owner.gate.enabled is True
    assert owner.gate.receiver_active is True


@pytest.mark.asyncio
async def test_init_pipeline_reuses_existing_gate_and_updates_state() -> None:
    owner = effects()
    gate = owner.gate
    await owner.apply_vrc_microphone_intercept(True)
    assert owner.gate is gate
    assert gate.state is owner.state


@pytest.mark.asyncio
async def test_init_pipeline_configures_receiver_after_pipeline_init() -> None:
    receiver = ReceiverRuntime()
    owner = effects(receiver)
    await owner.apply_vrc_microphone_intercept(True)
    assert receiver.starts == 1


@pytest.mark.asyncio
async def test_configure_vrc_mic_receiver_disabled_stops_receiver() -> None:
    receiver = ReceiverRuntime()
    owner = effects(receiver)
    await owner.apply_vrc_microphone_intercept(True)
    await owner.apply_vrc_microphone_intercept(False)
    assert receiver.stops == 1
    assert owner.gate.enabled is False


@pytest.mark.asyncio
async def test_configure_vrc_mic_receiver_no_state_or_existing_receiver_only_syncs_gate() -> None:
    receiver = ReceiverRuntime()
    owner = effects(receiver)
    await owner.apply_vrc_microphone_intercept(True)
    await owner.apply_vrc_microphone_intercept(True)
    assert receiver.starts == 1
    assert owner.gate.receiver_active is True


@pytest.mark.asyncio
async def test_configure_vrc_mic_receiver_start_failure_logs_and_clears_active() -> None:
    receiver = ReceiverRuntime(fail_start=True)
    owner = effects(receiver)
    assert await owner.apply_vrc_microphone_intercept(True) is False
    assert owner.gate.enabled is False
    assert owner.gate.receiver_active is False
    assert receiver.closes == 1


@pytest.mark.asyncio
async def test_configure_vrc_mic_receiver_start_success_stores_receiver_and_resets_gate() -> None:
    receiver = ReceiverRuntime()
    owner = effects(receiver)
    assert await owner.apply_vrc_microphone_intercept(True) is True
    assert receiver.receiver is not None
    assert owner.gate.receiver_active is True


@pytest.mark.asyncio
async def test_stop_vrc_mic_receiver_stops_receiver_and_marks_gate_inactive() -> None:
    receiver = ReceiverRuntime()
    owner = effects(receiver)
    await owner.apply_vrc_microphone_intercept(True)
    await owner.apply_vrc_microphone_intercept(False)
    assert receiver.receiver is None
    assert owner.gate.receiver_active is False


@pytest.mark.asyncio
async def test_configure_vrc_mic_receiver_disabled_stops_runtime_owner_and_marks_gate_inactive() -> (
    None
):
    receiver = ReceiverRuntime()
    owner = effects(receiver)
    await owner.apply_vrc_microphone_intercept(True)
    await owner.apply_vrc_microphone_intercept(False)
    assert receiver.stops == 1
    assert owner.gate.receiver_active is False


@pytest.mark.asyncio
async def test_stop_terminally_closes_vrc_receiver_runtime_before_hub_teardown() -> None:
    receiver = ReceiverRuntime()
    owner = effects(receiver)
    runtime = OverlayOscApplicationRuntime(vrc_microphone=owner)
    await owner.apply_vrc_microphone_intercept(True)
    await runtime.shutdown()
    assert receiver.closes == 1


@pytest.mark.asyncio
async def test_stop_aggregates_vrc_receiver_close_failure_and_still_stops_hub() -> None:
    receiver = ReceiverRuntime(fail_close=True)
    owner = effects(receiver)
    runtime = OverlayOscApplicationRuntime(vrc_microphone=owner)
    await runtime.shutdown()
    assert runtime.lifecycle_state == "failed"
    assert receiver.closes == 1


@pytest.mark.asyncio
async def test_controller_stop_closes_vrc_mic_receiver_before_hub_shutdown() -> None:
    receiver = ReceiverRuntime()
    owner = effects(receiver)
    runtime = OverlayOscApplicationRuntime(vrc_microphone=owner)
    await runtime.shutdown()
    await runtime.shutdown()
    assert receiver.closes == 1
