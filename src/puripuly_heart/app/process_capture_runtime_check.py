from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import time
from pathlib import Path

from puripuly_heart.config.process_capture_platform import get_process_capture_platform_availability
from puripuly_heart.core.audio.process_source import (
    ProcTapProcessAudioCaptureFactory,
    verify_proctap_1_0_3_process_specific,
)

PROCESS_CAPTURE_RUNTIME_REPORT_ENV = "PURIPULY_HEART_PROCESS_CAPTURE_RUNTIME_REPORT_PATH"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_process_capture_runtime_check() -> int:
    report_value = os.environ.get(PROCESS_CAPTURE_RUNTIME_REPORT_ENV)
    if not report_value:
        return 2
    availability = get_process_capture_platform_availability()
    if not availability.available:
        return 2
    report_path = Path(report_value).resolve()
    capture = None
    started = False
    try:
        proctap = importlib.import_module("proctap")
        native = importlib.import_module("proctap._native")
        native_path = Path(native.__file__).resolve()
        capture = ProcTapProcessAudioCaptureFactory().create(
            pid=os.getpid(),
            on_data=lambda _data, _frames: None,
        )
        native_process_specific = verify_proctap_1_0_3_process_specific(capture)
        capture.start()
        started = True
        time.sleep(0.1)
        report = {
            "schema": "puripuly-heart/process-capture-runtime-check/v1",
            "status": "passed",
            "proctap_version": importlib.metadata.version("proc-tap"),
            "proctap_module": str(Path(proctap.__file__).resolve()),
            "native_module": str(native_path),
            "native_sha256": _sha256(native_path),
            "native_process_specific": native_process_specific,
            "capture_started": started,
            "device_fallback_used": False,
            "credentials_used": False,
            "network_used": False,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    except Exception:
        return 1
    finally:
        if capture is not None:
            capture.close()
