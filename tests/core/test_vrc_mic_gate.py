from __future__ import annotations

import numpy as np
import pytest

from puripuly_heart.core.audio.gate import VrcMicAudioGate
from puripuly_heart.core.osc import receiver as receiver_module
from puripuly_heart.core.osc.receiver import VrcMicState, VrcOscReceiver


def _samples(value: float) -> np.ndarray:
    return np.full((8,), value, dtype=np.float32)


def test_vrc_mic_audio_gate_passes_audio_when_unmuted() -> None:
    state = VrcMicState(muted=False)
    gate = VrcMicAudioGate(state=state, enabled=True)
    gate.set_receiver_active(True)

    out = gate.process_chunk(_samples(1.0))

    assert np.array_equal(out, _samples(1.0))


def test_vrc_mic_audio_gate_mutes_audio_when_muted() -> None:
    state = VrcMicState(muted=True)
    gate = VrcMicAudioGate(state=state, enabled=True)
    gate.set_receiver_active(True)

    out = gate.process_chunk(_samples(1.0))

    assert np.array_equal(out, np.zeros((8,), dtype=np.float32))


def test_vrc_mic_audio_gate_holds_closed_during_initial_sync_grace() -> None:
    now = 100.0

    def monotonic() -> float:
        return now

    state = VrcMicState()
    gate = VrcMicAudioGate(
        state=state,
        enabled=True,
        initial_sync_grace_s=1.0,
        monotonic=monotonic,
    )
    gate.set_receiver_active(True)

    assert np.array_equal(gate.process_chunk(_samples(1.0)), np.zeros((8,), dtype=np.float32))

    now = 101.5
    assert np.array_equal(gate.process_chunk(_samples(1.0)), _samples(1.0))


def test_vrc_mic_audio_gate_resumes_immediately_after_unmute() -> None:
    state = VrcMicState(muted=True)
    gate = VrcMicAudioGate(state=state, enabled=True)
    gate.set_receiver_active(True)

    assert np.array_equal(gate.process_chunk(_samples(1.0)), np.zeros((8,), dtype=np.float32))

    state.update(False)
    assert np.array_equal(gate.process_chunk(_samples(1.0)), _samples(1.0))


def test_vrc_mic_audio_gate_enters_sync_grace_after_receiver_restart() -> None:
    now = 100.0

    def monotonic() -> float:
        return now

    state = VrcMicState(muted=True)
    gate = VrcMicAudioGate(
        state=state,
        enabled=True,
        initial_sync_grace_s=1.0,
        monotonic=monotonic,
    )

    gate.set_receiver_active(True)
    assert np.array_equal(gate.process_chunk(_samples(1.0)), np.zeros((8,), dtype=np.float32))

    gate.set_receiver_active(False)
    state.reset()
    assert np.array_equal(gate.process_chunk(_samples(1.0)), _samples(1.0))

    gate.set_receiver_active(True)
    assert np.array_equal(gate.process_chunk(_samples(1.0)), np.zeros((8,), dtype=np.float32))

    now = 101.5
    assert np.array_equal(gate.process_chunk(_samples(1.0)), _samples(1.0))


def test_vrc_osc_receiver_stop_preserves_last_known_state() -> None:
    state = VrcMicState(muted=True)
    receiver = VrcOscReceiver(state=state)

    receiver.stop()

    assert state.muted is True


@pytest.mark.asyncio
async def test_vrc_osc_receiver_start_resets_last_known_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = VrcMicState(muted=True)
    transport_closed: list[bool] = []

    class FakeTransport:
        def close(self) -> None:
            transport_closed.append(True)

    class FakeServer:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def create_serve_endpoint(self):
            return FakeTransport(), object()

    monkeypatch.setattr(receiver_module, "AsyncIOOSCUDPServer", FakeServer)

    receiver = VrcOscReceiver(state=state)
    await receiver.start()

    assert state.muted is None

    receiver.stop()
    assert transport_closed == [True]
