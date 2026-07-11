from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import tempfile
import time
import wave
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import numpy as np

from puripuly_heart.config.process_capture_platform import get_process_capture_platform_availability
from puripuly_heart.release_evidence.windows_process_isolation import (
    IsolationMeasurements,
    _start_emitter,
    _worker_environment,
    isolation_passes,
    load_thresholds,
    measure_isolation,
    validate_direct_child_topology,
)

AB_EVIDENCE_SCHEMA = "puripuly-heart/windows-application-loopback-ab/v1"
MICROSOFT_SOURCE_URL = "https://github.com/microsoft/Windows-classic-samples.git"


def read_pcm_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as recording:
        channels = recording.getnchannels()
        sample_width = recording.getsampwidth()
        sample_rate_hz = recording.getframerate()
        frames = recording.readframes(recording.getnframes())
    if channels not in (1, 2) or sample_width not in (2, 4) or sample_rate_hz <= 0:
        raise ValueError("unsupported official sample WAV format")
    dtype = "<i2" if sample_width == 2 else "<i4"
    scale = float(1 << (sample_width * 8 - 1))
    samples = np.frombuffer(frames, dtype=dtype).astype(np.float32) / scale
    samples = samples.reshape((-1, channels))
    if channels == 1:
        samples = np.repeat(samples, 2, axis=1)
    return samples, sample_rate_hz


def analyze_official_wav(path: Path) -> tuple[IsolationMeasurements, int]:
    samples, sample_rate_hz = read_pcm_wav(path)
    return measure_isolation(samples, sample_rate_hz=sample_rate_hz), sample_rate_hz


def validate_ab_artifact(artifact: dict[str, object]) -> None:
    required = {
        "schema",
        "status",
        "classification",
        "provenance",
        "topology",
        "timing",
        "thresholds",
        "official_sample",
        "proctap",
        "contextual_multilevel_evidence",
        "credential_free",
        "network_scope",
    }
    if set(artifact) != required or artifact.get("schema") != AB_EVIDENCE_SCHEMA:
        raise ValueError("invalid A/B evidence schema")
    if artifact.get("status") not in {"passed", "decision_needed", "failed", "blocked"}:
        raise ValueError("invalid A/B evidence status")
    official = artifact.get("official_sample")
    if not isinstance(official, dict) or set(official) != {"root_tree", "direct_child"}:
        raise ValueError("invalid official sample evidence")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_official_capture(
    binary: Path,
    *,
    pid: int,
    output_path: Path,
    runtime_dir: Path,
) -> tuple[IsolationMeasurements, int, dict[str, float]]:
    started = time.monotonic()
    completed = subprocess.run(
        [str(binary), str(pid), "includetree", str(output_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=runtime_dir,
        env=_worker_environment(runtime_dir),
        timeout=20,
        check=False,
    )
    finished = time.monotonic()
    if completed.returncode != 0 or not output_path.is_file():
        raise RuntimeError("official_sample_capture_failed")
    measurements, sample_rate_hz = analyze_official_wav(output_path)
    return (
        measurements,
        sample_rate_hz,
        {
            "activation_monotonic_s": started,
            "completion_monotonic_s": finished,
            "duration_s": finished - started,
        },
    )


async def run_ab(
    *,
    binary: Path,
    source_commit: str,
    proctap_evidence_path: Path,
    thresholds_path: Path,
    evidence_path: Path,
    build_metadata: dict[str, str],
) -> int:
    availability = get_process_capture_platform_availability()
    thresholds = load_thresholds(thresholds_path)
    contextual_multilevel_evidence = None
    if evidence_path.is_file():
        previous = json.loads(evidence_path.read_text(encoding="utf-8"))
        if previous.get("contextual_multilevel_evidence") is not None:
            contextual_multilevel_evidence = previous.get("contextual_multilevel_evidence")
        elif (
            previous.get("classification") == "both_multilevel_root_failed_immediate_parent_passed"
        ):
            contextual_multilevel_evidence = {
                "classification": previous.get("classification"),
                "official_sample": previous.get("official_sample"),
                "proctap": previous.get("proctap"),
                "topology": previous.get("topology"),
            }
    if not availability.available or not binary.is_file():
        artifact = {
            "schema": AB_EVIDENCE_SCHEMA,
            "status": "blocked",
            "classification": availability.reason or "official_binary_unavailable",
            "provenance": {},
            "topology": {},
            "timing": {},
            "thresholds": asdict(thresholds),
            "official_sample": {"root_tree": None, "direct_child": None},
            "proctap": None,
            "contextual_multilevel_evidence": contextual_multilevel_evidence,
            "credential_free": True,
            "network_scope": "public_microsoft_source_and_nuget_restore_only",
        }
        validate_ab_artifact(artifact)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
        return 2

    with tempfile.TemporaryDirectory(prefix="puripuly-official-ab-") as directory:
        runtime_dir = Path(directory)
        root = None
        control = None
        try:
            root = _start_emitter("target_root", runtime_dir)
            control = _start_emitter("control", runtime_dir)
            import psutil

            root_process = psutil.Process(root.ready.pid)
            child_pid = root.ready.child_pid or 0
            child_process = psutil.Process(child_pid)
            descendants = {process.pid for process in root_process.children(recursive=True)}
            topology = {
                "target_root_pid": root.ready.pid,
                "target_child_pid": child_pid,
                "target_child_os_ppid": child_process.ppid(),
                "target_child_direct_ppid_verified": child_process.ppid() == root.ready.pid,
                "target_child_descendant_verified": child_pid in descendants,
                "control_pid": control.ready.pid,
                "control_non_descendant_verified": control.ready.pid not in descendants,
                "child_ready_role": root.ready.child_role,
            }
            if not validate_direct_child_topology(
                root_pid=root.ready.pid,
                child_pid=child_pid,
                child_ppid=child_process.ppid(),
                descendant_pids=descendants,
                control_pid=control.ready.pid,
            ):
                raise RuntimeError("direct_child_topology_invalid")
            root_measurements, root_rate, root_timing = await asyncio.to_thread(
                _run_official_capture,
                binary,
                pid=root.ready.pid,
                output_path=runtime_dir / "official-root.wav",
                runtime_dir=runtime_dir,
            )
            child_measurements, child_rate, child_timing = await asyncio.to_thread(
                _run_official_capture,
                binary,
                pid=child_pid,
                output_path=runtime_dir / "official-child.wav",
                runtime_dir=runtime_dir,
            )
            root_passed = isolation_passes(root_measurements, thresholds)
            child_present = (
                child_measurements.target_amplitude >= thresholds.target_present_amplitude_min
            )
            proctap = json.loads(proctap_evidence_path.read_text(encoding="utf-8"))
            proctap_passed = proctap.get("status") == "passed"
            if root_passed and proctap_passed:
                status = "passed"
                classification = "direct_child_both_passed_multilevel_risk"
                exit_code = 0
            elif root_passed and not proctap_passed:
                status = "decision_needed"
                classification = "official_success_proctap_failure"
                exit_code = 3
            else:
                status = "failed"
                classification = (
                    "both_direct_child_root_failed"
                    if not root_passed and not proctap_passed
                    else "ab_mismatch"
                )
                exit_code = 1
            artifact = {
                "schema": AB_EVIDENCE_SCHEMA,
                "status": status,
                "classification": classification,
                "provenance": {
                    "source_url": MICROSOFT_SOURCE_URL,
                    "source_commit": source_commit,
                    "sample_path": "Samples/ApplicationLoopback/cpp",
                    "source_modified": False,
                    "binary_sha256": _sha256(binary),
                    "build": build_metadata,
                },
                "topology": topology,
                "timing": {
                    "target_child_ready_monotonic_s": root.ready_monotonic_s,
                    "control_ready_monotonic_s": control.ready_monotonic_s,
                    "official_root": root_timing,
                    "official_direct_child": child_timing,
                },
                "thresholds": asdict(thresholds),
                "official_sample": {
                    "root_tree": {
                        "passed": root_passed,
                        "sample_rate_hz": root_rate,
                        "measurements": asdict(root_measurements),
                    },
                    "direct_child": {
                        "target_present": child_present,
                        "sample_rate_hz": child_rate,
                        "measurements": asdict(child_measurements),
                    },
                },
                "proctap": {
                    "artifact": (f"docs/release-evidence/{proctap_evidence_path.name}"),
                    "status": proctap.get("status"),
                    "classification": proctap.get("classification"),
                    "measurements": proctap.get("measurements"),
                    "native_process_specific_observations": (
                        proctap.get("capture_construction", {}).get(
                            "native_process_specific_observations"
                        )
                    ),
                },
                "contextual_multilevel_evidence": contextual_multilevel_evidence,
                "credential_free": True,
                "network_scope": "public_microsoft_source_and_nuget_restore_only",
            }
            validate_ab_artifact(artifact)
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
            return exit_code
        except Exception:
            artifact = {
                "schema": AB_EVIDENCE_SCHEMA,
                "status": "failed",
                "classification": "official_sample_diagnostic_failed",
                "provenance": {
                    "source_url": MICROSOFT_SOURCE_URL,
                    "source_commit": source_commit,
                    "binary_sha256": _sha256(binary),
                    "build": build_metadata,
                },
                "topology": {},
                "timing": {},
                "thresholds": asdict(thresholds),
                "official_sample": {"root_tree": None, "direct_child": None},
                "proctap": None,
                "contextual_multilevel_evidence": contextual_multilevel_evidence,
                "credential_free": True,
                "network_scope": "public_microsoft_source_and_nuget_restore_only",
            }
            validate_ab_artifact(artifact)
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
            return 1
        finally:
            if root is not None:
                root.stop()
            if control is not None:
                control.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="windows-application-loopback-ab")
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--proctap-evidence", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--msbuild-version", required=True)
    parser.add_argument("--toolset", required=True)
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--architecture", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(
        run_ab(
            binary=args.binary.resolve(),
            source_commit=args.source_commit,
            proctap_evidence_path=args.proctap_evidence.resolve(),
            thresholds_path=args.thresholds.resolve(),
            evidence_path=args.evidence.resolve(),
            build_metadata={
                "msbuild_version": args.msbuild_version,
                "platform_toolset": args.toolset,
                "configuration": args.configuration,
                "architecture": args.architecture,
                "python": platform.python_version(),
                "cmake": "4.3.1",
                "nuget": "6.14.0",
                "msbuild_command": (
                    "MSBuild.exe ApplicationLoopback.sln /m /p:Configuration=Release "
                    "/p:Platform=x64 /p:PlatformToolset=v143"
                ),
            },
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
