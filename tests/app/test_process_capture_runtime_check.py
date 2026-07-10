from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from puripuly_heart.app import process_capture_runtime_check as runtime_check
from puripuly_heart.config.process_capture_platform import ProcessCapturePlatformAvailability


def test_runtime_check_imports_starts_and_reports_verified_native_mode(
    tmp_path: Path, monkeypatch
) -> None:
    package_file = tmp_path / "proctap" / "__init__.py"
    native_file = tmp_path / "proctap" / "_native.cp312-win_amd64.pyd"
    package_file.parent.mkdir()
    package_file.write_text("", encoding="utf-8")
    native_file.write_bytes(b"native")
    report_path = tmp_path / "report.json"
    capture = SimpleNamespace(
        _backend=SimpleNamespace(_native=SimpleNamespace(is_process_specific=lambda: True)),
        started=False,
        closed=False,
    )
    capture.start = lambda: setattr(capture, "started", True)
    capture.close = lambda: setattr(capture, "closed", True)

    monkeypatch.setenv(runtime_check.PROCESS_CAPTURE_RUNTIME_REPORT_ENV, str(report_path))
    monkeypatch.setattr(
        runtime_check,
        "get_process_capture_platform_availability",
        lambda: ProcessCapturePlatformAvailability(available=True),
    )
    monkeypatch.setattr(
        runtime_check.importlib,
        "import_module",
        lambda name: SimpleNamespace(__file__=package_file if name == "proctap" else native_file),
    )
    monkeypatch.setattr(runtime_check.importlib.metadata, "version", lambda _name: "1.0.3")
    monkeypatch.setattr(
        runtime_check,
        "ProcTapProcessAudioCaptureFactory",
        lambda: SimpleNamespace(create=lambda **_kwargs: capture),
    )
    monkeypatch.setattr(runtime_check.time, "sleep", lambda _seconds: None)

    assert runtime_check.run_process_capture_runtime_check() == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["native_process_specific"] is True
    assert report["capture_started"] is True
    assert report["device_fallback_used"] is False
    assert report["credentials_used"] is False
    assert report["network_used"] is False
    assert capture.closed is True


def test_runtime_check_fails_without_report_path(monkeypatch) -> None:
    monkeypatch.delenv(runtime_check.PROCESS_CAPTURE_RUNTIME_REPORT_ENV, raising=False)

    assert runtime_check.run_process_capture_runtime_check() == 2
