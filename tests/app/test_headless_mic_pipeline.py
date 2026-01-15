from __future__ import annotations

import asyncio

import numpy as np

from puripuly_heart.app.headless_mic import run_audio_vad_loop
from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.osc.smart_queue import SmartOscQueue
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.vad.gating import VadGating
from tests.helpers.audio import FakeAudioSource, make_frames
from tests.helpers.fakes import FakeSender, SpeechAwareFakeBackend
from tests.helpers.vad import SequenceVadEngine


async def test_headless_mic_pipeline_smoke():
    clock = FakeClock()
    sender = FakeSender()
    osc = SmartOscQueue(sender=sender, clock=clock, ttl_s=100.0)

    stt = ManagedSTTProvider(backend=SpeechAwareFakeBackend(), sample_rate_hz=16000, clock=clock)
    hub = ClientHub(stt=stt, llm=None, osc=osc, clock=clock, fallback_transcript_only=True)
    await hub.start(auto_flush_osc=False)

    probs = [0.0, 0.0, 0.9, 0.9, 0.0, 0.0, 0.0]
    vad = VadGating(
        SequenceVadEngine(probs=probs), sample_rate_hz=16000, ring_buffer_ms=64, hangover_ms=64
    )

    chunks = [
        np.zeros((512,), dtype=np.float32),
        np.zeros((512,), dtype=np.float32),
        np.ones((512,), dtype=np.float32),
        np.ones((512,), dtype=np.float32),
        np.zeros((512,), dtype=np.float32),
        np.zeros((512,), dtype=np.float32),
        np.zeros((512,), dtype=np.float32),
    ]
    audio = np.concatenate(chunks, axis=0)

    # Deliberately split into uneven frames to exercise chunking.
    splits = [1000, 1000, 1000, audio.size - 3000]
    frames = make_frames(audio, sample_rate_hz=16000, splits=splits)
    source = FakeAudioSource(frames)
    await run_audio_vad_loop(source=source, vad=vad, hub=hub, target_sample_rate_hz=16000)

    for _ in range(50):
        if sender.sent:
            break
        await asyncio.sleep(0.01)

    assert sender.sent == ["FINAL"]
    await hub.stop()
