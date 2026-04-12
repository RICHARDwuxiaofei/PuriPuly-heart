from __future__ import annotations

import importlib
import sys

from puripuly_heart.core.soxr_runtime import (
    SoxrRuntimeAvailabilityError,
    ensure_soxr_runtime_available_for_startup,
    resolve_soxr_runtime_paths,
    validate_soxr_runtime_paths,
)

SOXR_SMOKE_INPUT_RATE_HZ = 48000
SOXR_SMOKE_OUTPUT_RATE_HZ = 16000
SOXR_SMOKE_FRAME_COUNT = 480


def _print_error(message: str) -> None:
    print(f"Error: {message}", flush=True)


def run_soxr_runtime_check() -> int:
    if sys.platform != "win32":
        _print_error("soxr-runtime-check is only supported on Windows")
        return 2

    try:
        runtime_paths = ensure_soxr_runtime_available_for_startup()
        if runtime_paths is None:
            runtime_paths = validate_soxr_runtime_paths(resolve_soxr_runtime_paths())
    except SoxrRuntimeAvailabilityError as exc:
        _print_error(f"failed to verify packaged soxr runtime: {exc}")
        return 2

    try:
        import numpy as np

        soxr = importlib.import_module("soxr")
        stream = soxr.ResampleStream(
            SOXR_SMOKE_INPUT_RATE_HZ,
            SOXR_SMOKE_OUTPUT_RATE_HZ,
            1,
            dtype="float32",
        )
        output = stream.resample_chunk(
            np.zeros(SOXR_SMOKE_FRAME_COUNT, dtype=np.float32),
            last=True,
        )
        if len(output) == 0:
            raise RuntimeError("smoke resample returned no output")
    except Exception as exc:
        _print_error(f"failed to import or smoke-test soxr: {exc}")
        return 2

    print(f"soxr_extension_path={runtime_paths.extension_path}", flush=True)
    print(f"soxr_runtime_dir={runtime_paths.runtime_dir}", flush=True)
    print(f"soxr_sibling_dll={runtime_paths.sibling_dll_path}", flush=True)
    return 0
