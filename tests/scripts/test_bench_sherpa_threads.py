from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_bench_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "bench_sherpa_threads.py"
    spec = importlib.util.spec_from_file_location("bench_sherpa_threads", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_thread_summary_aggregates_latency_cpu_and_memory() -> None:
    bench = _load_bench_module()
    rows = [
        bench.FileBenchmarkSummary(
            group="ko-en",
            relative_path="ko-en/a/sample-a.wav",
            duration_seconds=2.0,
            first_transcript="alpha",
            stable_across_runs=True,
            latency_ms=[120.0, 125.0, 130.0, 128.0, 122.0],
            cpu_ms=[110.0, 112.0, 114.0, 113.0, 111.0],
            rtfs=[0.060, 0.062, 0.065, 0.064, 0.061],
        ),
        bench.FileBenchmarkSummary(
            group="ko-jp",
            relative_path="ko-jp/a/sample-b.wav",
            duration_seconds=3.0,
            first_transcript="beta",
            stable_across_runs=False,
            latency_ms=[210.0, 215.0, 214.0, 212.0, 213.0],
            cpu_ms=[190.0, 192.0, 191.0, 193.0, 194.0],
            rtfs=[0.070, 0.072, 0.071, 0.071, 0.071],
        ),
    ]

    summary = bench.build_thread_summary(
        num_threads=4,
        files=rows,
        warm_resource_samples=[
            bench.ProcessMemorySample(working_set_bytes=200, private_bytes=150, commit_bytes=175),
            bench.ProcessMemorySample(working_set_bytes=260, private_bytes=180, commit_bytes=210),
            bench.ProcessMemorySample(working_set_bytes=240, private_bytes=170, commit_bytes=205),
        ],
        warm_wall_seconds=2.5,
        warm_cpu_seconds=3.0,
    )

    assert summary.num_threads == 4
    assert summary.total_file_count == 2
    assert summary.stable_file_count == 1
    assert summary.median_file_p50_ms == 169.0
    assert round(summary.p95_file_p50_ms, 1) == 208.6
    assert round(summary.mean_file_cpu_ms, 1) == 152.0
    assert round(summary.weighted_total_rtf, 4) == 0.0676
    assert summary.warm_resources.peak_working_set_bytes == 260
    assert summary.warm_resources.steady_working_set_bytes == 240
    assert summary.warm_resources.peak_private_bytes == 180
    assert summary.warm_resources.steady_private_bytes == 170
    assert summary.warm_resources.peak_commit_bytes == 210
    assert summary.warm_resources.steady_commit_bytes == 205
    assert summary.cpu_utilization_percent == 120.0


def test_compute_win_counts_prefers_lower_p50_then_rtf() -> None:
    bench = _load_bench_module()
    thread3_rows = [
        bench.FileBenchmarkSummary(
            group="ko-en",
            relative_path="ko-en/a/sample-a.wav",
            duration_seconds=2.0,
            first_transcript="alpha",
            stable_across_runs=True,
            latency_ms=[90.0, 90.0, 90.0, 90.0, 90.0],
            cpu_ms=[50.0, 50.0, 50.0, 50.0, 50.0],
            rtfs=[0.070, 0.070, 0.070, 0.070, 0.070],
        ),
        bench.FileBenchmarkSummary(
            group="ko-en",
            relative_path="ko-en/a/sample-b.wav",
            duration_seconds=2.0,
            first_transcript="beta",
            stable_across_runs=True,
            latency_ms=[100.0, 100.0, 100.0, 100.0, 100.0],
            cpu_ms=[50.0, 50.0, 50.0, 50.0, 50.0],
            rtfs=[0.070, 0.070, 0.070, 0.070, 0.070],
        ),
    ]
    thread5_rows = [
        bench.FileBenchmarkSummary(
            group="ko-en",
            relative_path="ko-en/a/sample-a.wav",
            duration_seconds=2.0,
            first_transcript="alpha",
            stable_across_runs=True,
            latency_ms=[95.0, 95.0, 95.0, 95.0, 95.0],
            cpu_ms=[50.0, 50.0, 50.0, 50.0, 50.0],
            rtfs=[0.050, 0.050, 0.050, 0.050, 0.050],
        ),
        bench.FileBenchmarkSummary(
            group="ko-en",
            relative_path="ko-en/a/sample-b.wav",
            duration_seconds=2.0,
            first_transcript="beta",
            stable_across_runs=True,
            latency_ms=[100.0, 100.0, 100.0, 100.0, 100.0],
            cpu_ms=[50.0, 50.0, 50.0, 50.0, 50.0],
            rtfs=[0.060, 0.060, 0.060, 0.060, 0.060],
        ),
    ]

    wins = bench.compute_win_counts({3: thread3_rows, 5: thread5_rows})

    assert wins == {3: 1, 5: 1}


def test_thread_result_round_trips_through_json_payload() -> None:
    bench = _load_bench_module()
    result = bench.ThreadBenchmarkResult(
        num_threads=4,
        cold_load=bench.ColdLoadSummary(
            wall_ms=100.0,
            cpu_ms=80.0,
            working_set_bytes=10,
            private_bytes=9,
            commit_bytes=8,
        ),
        summary=bench.ThreadBenchmarkSummary(
            num_threads=4,
            median_file_p50_ms=120.0,
            p95_file_p50_ms=130.0,
            mean_file_rtf=0.06,
            weighted_total_rtf=0.06,
            mean_file_cpu_ms=90.0,
            stable_file_count=1,
            total_file_count=1,
            win_count=1,
            warm_resources=bench.WarmResourceSummary(
                peak_working_set_bytes=20,
                steady_working_set_bytes=18,
                peak_private_bytes=16,
                steady_private_bytes=15,
                peak_commit_bytes=14,
                steady_commit_bytes=13,
            ),
            cpu_utilization_percent=95.0,
        ),
        group_summaries={
            "ko-en": bench.GroupBenchmarkSummary(
                group="ko-en",
                median_file_p50_ms=120.0,
                p95_file_p50_ms=130.0,
                mean_file_rtf=0.06,
                weighted_total_rtf=0.06,
                mean_file_cpu_ms=90.0,
                stable_file_count=1,
                total_file_count=1,
                win_count=1,
            )
        },
        files=[
            bench.FileBenchmarkSummary(
                group="ko-en",
                relative_path="ko-en/a/sample.wav",
                duration_seconds=2.0,
                first_transcript="alpha",
                stable_across_runs=True,
                latency_ms=[120.0] * 5,
                cpu_ms=[90.0] * 5,
                rtfs=[0.06] * 5,
            )
        ],
    )

    restored = bench.thread_result_from_dict(result.to_dict())

    assert restored.num_threads == 4
    assert restored.summary.warm_resources.peak_working_set_bytes == 20
    assert restored.group_summaries["ko-en"].win_count == 1
    assert restored.files[0].p50_latency_ms == 120.0
