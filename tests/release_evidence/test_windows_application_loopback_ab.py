from __future__ import annotations

import wave
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from puripuly_heart.release_evidence.windows_application_loopback_ab import (
    AB_EVIDENCE_SCHEMA,
    analyze_official_wav,
    validate_ab_artifact,
)
from puripuly_heart.release_evidence.windows_process_isolation import (
    CONTROL_FREQUENCY_HZ,
    TARGET_FREQUENCY_HZ,
    IsolationThresholds,
    isolation_passes,
)


def _write_wav(path: Path, *, target_amplitude: float, control_amplitude: float) -> None:
    sample_rate_hz = 44100
    indexes = np.arange(sample_rate_hz * 2, dtype=np.float64)
    mono = target_amplitude * np.sin(
        2 * np.pi * TARGET_FREQUENCY_HZ * indexes / sample_rate_hz
    ) + control_amplitude * np.sin(2 * np.pi * CONTROL_FREQUENCY_HZ * indexes / sample_rate_hz)
    pcm = np.clip(mono * 32767, -32768, 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as recording:
        recording.setnchannels(2)
        recording.setsampwidth(2)
        recording.setframerate(sample_rate_hz)
        recording.writeframes(stereo.tobytes())


def test_official_wav_parser_uses_shared_threshold_analysis(tmp_path: Path) -> None:
    recording = tmp_path / "official.wav"
    _write_wav(recording, target_amplitude=0.18, control_amplitude=0.001)

    measurements, sample_rate_hz = analyze_official_wav(recording)

    assert sample_rate_hz == 44100
    assert measurements.target_amplitude == pytest.approx(0.18, abs=1e-4)
    assert measurements.control_amplitude == pytest.approx(0.001, abs=1e-4)
    assert isolation_passes(
        measurements,
        IsolationThresholds(
            target_present_amplitude_min=0.05,
            control_excluded_amplitude_max=0.005,
            control_to_target_ratio_max=0.1,
        ),
    )


def test_ab_artifact_schema_distinguishes_official_and_proctap_results() -> None:
    thresholds = IsolationThresholds(0.05, 0.005, 0.1)
    artifact = {
        "schema": AB_EVIDENCE_SCHEMA,
        "status": "decision_needed",
        "classification": "official_success_proctap_failure",
        "provenance": {"source_commit": "a" * 40},
        "topology": {"target_child_descendant_verified": True},
        "timing": {"official_root": {"duration_s": 10.0}},
        "thresholds": asdict(thresholds),
        "official_sample": {
            "root_tree": {"passed": True},
            "direct_child": {"target_present": True},
        },
        "proctap": {"status": "failed"},
        "contextual_multilevel_evidence": {"classification": "prior"},
        "credential_free": True,
        "network_scope": "public_microsoft_source_and_nuget_restore_only",
    }

    validate_ab_artifact(artifact)

    invalid = {**artifact, "official_sample": {"root_tree": {"passed": True}}}
    with pytest.raises(ValueError, match="official sample"):
        validate_ab_artifact(invalid)
