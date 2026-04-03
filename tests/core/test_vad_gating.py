from __future__ import annotations

import numpy as np
import pytest

from puripuly_heart.core.vad.gating import (
    PEER_VAD_SPEECH_THRESHOLD,
    PEER_VAD_START_COMMIT_CHUNKS,
    PEER_VAD_START_DEBOUNCE_CHUNKS,
    SpeechChunk,
    SpeechEnd,
    SpeechStart,
    VadGating,
    create_peer_vad_gating,
)
from tests.helpers.vad import SequenceVadEngine, chunk_samples


def test_vad_gating_emits_start_and_end_with_hangover():
    # 32ms chunks @16k => 512 samples
    probs = [0.0, 0.0, 0.9, 0.9, 0.0, 0.0, 0.0]
    engine = SequenceVadEngine(probs=probs)
    gating = VadGating(engine, sample_rate_hz=16000, ring_buffer_ms=64, hangover_ms=64)

    events = []
    for i in range(len(probs)):
        events.extend(gating.process_chunk(chunk_samples(float(i), n=gating.chunk_samples)))

    start = next(e for e in events if isinstance(e, SpeechStart))
    end = next(e for e in events if isinstance(e, SpeechEnd))

    assert start.utterance_id == end.utterance_id
    assert start.pre_roll.shape[0] == 1024  # 64ms @ 16k
    assert end.trailing_silence_ms == 64


def test_vad_gating_pre_roll_contains_previous_audio():
    probs = [0.0, 0.0, 0.9]
    engine = SequenceVadEngine(probs=probs)
    gating = VadGating(engine, sample_rate_hz=16000, ring_buffer_ms=64, hangover_ms=0)

    # append two silent chunks (values 0,1) then speech chunk (value 2)
    gating.process_chunk(chunk_samples(0.0, n=gating.chunk_samples))
    gating.process_chunk(chunk_samples(1.0, n=gating.chunk_samples))
    events = gating.process_chunk(chunk_samples(2.0, n=gating.chunk_samples))

    start = next(e for e in events if isinstance(e, SpeechStart))
    assert start.pre_roll.shape[0] == 1024
    assert np.allclose(start.pre_roll[:512], 0.0)
    assert np.allclose(start.pre_roll[512:], 1.0)


def test_vad_gating_starts_on_first_positive_chunk_by_default():
    engine = SequenceVadEngine(probs=[0.0, 0.9])
    gating = VadGating(engine, sample_rate_hz=16000, ring_buffer_ms=64, hangover_ms=0)

    assert gating.process_chunk(chunk_samples(0.0, n=gating.chunk_samples)) == []

    events = gating.process_chunk(chunk_samples(1.0, n=gating.chunk_samples))

    assert len(events) == 1
    assert isinstance(events[0], SpeechStart)
    assert np.allclose(events[0].chunk, 1.0)


def test_vad_gating_buffers_candidate_until_commit_threshold():
    probs = [0.0, 0.0, 0.9, 0.9, 0.9]
    gating = VadGating(
        SequenceVadEngine(probs=probs),
        sample_rate_hz=16000,
        ring_buffer_ms=64,
        speech_threshold=0.6,
        hangover_ms=64,
        start_debounce_chunks=3,
        start_commit_chunks=3,
    )

    per_chunk_events = [
        gating.process_chunk(chunk_samples(float(i), n=gating.chunk_samples))
        for i in range(len(probs))
    ]

    assert all(not events for events in per_chunk_events[:4])

    events = per_chunk_events[4]
    start = events[0]
    chunks = [start.chunk] + [event.chunk for event in events[1:] if isinstance(event, SpeechChunk)]

    assert isinstance(start, SpeechStart)
    assert start.pre_roll.shape[0] == 1024
    assert np.allclose(start.pre_roll[:512], 0.0)
    assert np.allclose(start.pre_roll[512:], 1.0)
    assert len(events) == 3
    assert [type(event) for event in events] == [SpeechStart, SpeechChunk, SpeechChunk]
    assert [float(chunk[0]) for chunk in chunks] == [2.0, 3.0, 4.0]


def test_vad_gating_drops_short_candidate_before_commit():
    probs = [0.0, 0.9, 0.9, 0.0]
    gating = VadGating(
        SequenceVadEngine(probs=probs),
        sample_rate_hz=16000,
        ring_buffer_ms=64,
        speech_threshold=0.6,
        hangover_ms=64,
        start_debounce_chunks=3,
        start_commit_chunks=3,
    )

    events: list[object] = []
    for i in range(len(probs)):
        events.extend(gating.process_chunk(chunk_samples(float(i), n=gating.chunk_samples)))

    assert events == []
    assert gating.in_speech is False
    assert gating.utterance_id is None


def test_vad_gating_rejects_commit_threshold_lower_than_debounce_threshold():
    engine = SequenceVadEngine(probs=[0.0])

    with pytest.raises(ValueError, match="start_commit_chunks"):
        VadGating(
            engine,
            sample_rate_hz=16000,
            start_debounce_chunks=3,
            start_commit_chunks=2,
        )


def test_create_peer_vad_gating_uses_helper_defaults():
    gating = create_peer_vad_gating(
        SequenceVadEngine(probs=[0.0]),
        sample_rate_hz=16000,
        ring_buffer_ms=64,
        hangover_ms=64,
    )

    assert gating.speech_threshold == PEER_VAD_SPEECH_THRESHOLD
    assert gating.start_debounce_chunks == PEER_VAD_START_DEBOUNCE_CHUNKS
    assert gating.start_commit_chunks == PEER_VAD_START_COMMIT_CHUNKS
    assert gating.candidate_log_label == "Peer"
