from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest

import puripuly_heart.main as main_module
from puripuly_heart import __version__


def test_main_version_prints(capsys) -> None:
    result = main_module.main(["--version"])
    assert result == 0
    assert capsys.readouterr().out.strip() == __version__


def test_main_osc_send_uses_sender(monkeypatch, tmp_path) -> None:
    sent: dict[str, object] = {}

    class FakeSender:
        def __init__(self, *args, **kwargs):
            sent["instance"] = self

        def send_chatbox(self, text: str) -> None:
            sent["text"] = text

        def close(self) -> None:
            sent["closed"] = True

    monkeypatch.setattr(main_module, "VrchatOscUdpSender", FakeSender)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "osc-send", "hello"])

    assert result == 0
    assert sent["text"] == "hello"
    assert sent["closed"] is True


def test_main_run_stdin_llm_error(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(main_module, "create_secret_store", lambda *a, **k: "secrets")

    def raise_llm(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "create_llm_provider", raise_llm)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-stdin", "--use-llm"])

    assert result == 2
    assert "failed to initialize LLM provider" in capsys.readouterr().out


def test_main_run_stdin_invokes_runner(monkeypatch, tmp_path) -> None:
    ran: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            return None

        async def run(self):
            ran["called"] = True
            return 0

    monkeypatch.setattr(main_module, "HeadlessStdinRunner", FakeRunner)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-stdin"])

    assert result == 0
    assert ran["called"] is True


def test_main_run_mic_invokes_runner(monkeypatch, tmp_path) -> None:
    ran: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            return None

        async def run(self):
            ran["called"] = True
            return 0

    monkeypatch.setattr(main_module, "HeadlessMicRunner", FakeRunner)

    config_path = tmp_path / "settings.json"
    vad_model = tmp_path / "vad.onnx"
    vad_model.write_text("dummy", encoding="utf-8")
    result = main_module.main(
        ["--config", str(config_path), "run-mic", "--vad-model", str(vad_model)]
    )

    assert result == 0
    assert ran["called"] is True


def test_main_run_gui_invokes_flet_app(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    fake_flet = ModuleType("flet")

    def fake_app(*, target, assets_dir):
        calls["target"] = target
        calls["assets_dir"] = assets_dir

    fake_flet.app = fake_app
    monkeypatch.setitem(sys.modules, "flet", fake_flet)

    fake_ui_app = ModuleType("puripuly_heart.ui.app")

    async def main_gui(page, config_path):
        _ = (page, config_path)

    fake_ui_app.main_gui = main_gui
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.app", fake_ui_app)

    fake_fonts = ModuleType("puripuly_heart.ui.fonts")
    fake_fonts.assets_dir = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.fonts", fake_fonts)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-gui"])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    assert callable(calls["target"])


def test_main_default_invokes_gui(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    fake_flet = ModuleType("flet")

    def fake_app(*, target, assets_dir):
        calls["target"] = target
        calls["assets_dir"] = assets_dir

    fake_flet.app = fake_app
    monkeypatch.setitem(sys.modules, "flet", fake_flet)

    fake_ui_app = ModuleType("puripuly_heart.ui.app")

    async def main_gui(page, config_path):
        _ = (page, config_path)

    fake_ui_app.main_gui = main_gui
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.app", fake_ui_app)

    fake_fonts = ModuleType("puripuly_heart.ui.fonts")
    fake_fonts.assets_dir = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.fonts", fake_fonts)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path)])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    assert callable(calls["target"])


def test_main_local_qwen_runtime_check_dispatches_runner(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def fake_run_local_qwen_runtime_check() -> int:
        calls["called"] = True
        return 0

    monkeypatch.setattr(
        main_module,
        "run_local_qwen_runtime_check",
        fake_run_local_qwen_runtime_check,
        raising=False,
    )

    config_path = tmp_path / "settings.json"
    try:
        result = main_module.main(["--config", str(config_path), "local-qwen-runtime-check"])
    except SystemExit as exc:  # pragma: no cover - red phase guard
        pytest.fail(f"unexpected SystemExit: {exc}")

    assert result == 0
    assert calls["called"] is True


def test_run_local_qwen_runtime_check_imports_sherpa_onnx_and_offline_recognizer_before_reporting_success(
    monkeypatch, capsys, tmp_path
) -> None:
    try:
        runtime_check_module = importlib.import_module(
            "puripuly_heart.app.local_qwen_runtime_check"
        )
    except ModuleNotFoundError:  # pragma: no cover - red phase guard
        pytest.fail("local_qwen_runtime_check module is missing")

    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module.local_qwen_runtime,
        "ensure_local_qwen_windows_runtime",
        lambda: tmp_path,
    )

    imported_modules: list[str] = []
    real_import_module = runtime_check_module.importlib.import_module

    def fake_import_module(name: str, *args, **kwargs):
        if name == "sherpa_onnx":
            imported_modules.append(name)
            return ModuleType("sherpa_onnx")
        if name == "sherpa_onnx.offline_recognizer":
            imported_modules.append(name)
            return ModuleType("sherpa_onnx.offline_recognizer")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(runtime_check_module.importlib, "import_module", fake_import_module)

    result = runtime_check_module.run_local_qwen_runtime_check()

    assert result == 0
    assert imported_modules == ["sherpa_onnx", "sherpa_onnx.offline_recognizer"]
    assert capsys.readouterr().out.strip() == f"local_qwen_runtime_dir={tmp_path}"


def test_run_local_qwen_runtime_check_rejects_non_windows(monkeypatch, capsys) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.local_qwen_runtime_check")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "linux", raising=False)

    result = runtime_check_module.run_local_qwen_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: local-qwen-runtime-check is only supported on Windows"
    )


def test_run_local_qwen_runtime_check_reports_bootstrap_failure(monkeypatch, capsys) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.local_qwen_runtime_check")
    runtime_error = importlib.import_module("puripuly_heart.core.local_qwen_runtime")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)

    def raise_bootstrap_error() -> None:
        raise runtime_error.LocalQwenRuntimeBootstrapError("missing runtime dlls")

    monkeypatch.setattr(
        runtime_check_module.local_qwen_runtime,
        "ensure_local_qwen_windows_runtime",
        raise_bootstrap_error,
    )

    result = runtime_check_module.run_local_qwen_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to verify Local Qwen Windows runtime DLL directory: missing runtime dlls"
    )


def test_run_local_qwen_runtime_check_reports_bootstrap_failure_after_runtime_module_reload(
    monkeypatch, capsys, tmp_path
) -> None:
    runtime_check_module = importlib.reload(
        importlib.import_module("puripuly_heart.app.local_qwen_runtime_check")
    )
    runtime_module = importlib.import_module("puripuly_heart.core.local_qwen_runtime")

    runtime_module = importlib.reload(runtime_module)

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(runtime_module.sys, "platform", "win32")

    missing_runtime_dir = tmp_path / "missing-runtime"
    monkeypatch.setattr(
        runtime_module, "resolve_local_qwen_runtime_dir", lambda: missing_runtime_dir
    )

    try:
        result = runtime_check_module.run_local_qwen_runtime_check()
    finally:
        importlib.reload(runtime_check_module)

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to verify Local Qwen Windows runtime DLL directory: "
        f"local qwen runtime directory does not exist: {missing_runtime_dir}"
    )


def test_run_local_qwen_runtime_check_reports_sherpa_onnx_import_failure(
    monkeypatch, capsys, tmp_path
) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.local_qwen_runtime_check")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module.local_qwen_runtime,
        "ensure_local_qwen_windows_runtime",
        lambda: tmp_path,
    )
    real_import_module = runtime_check_module.importlib.import_module

    def fake_import_module(name: str, *args, **kwargs):
        if name == "sherpa_onnx":
            return ModuleType("sherpa_onnx")
        if name == "sherpa_onnx.offline_recognizer":
            raise ImportError("native extension load failed")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(runtime_check_module.importlib, "import_module", fake_import_module)

    result = runtime_check_module.run_local_qwen_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to import sherpa_onnx: native extension load failed"
    )


def test_load_settings_or_default_loads_when_exists(monkeypatch, tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")

    sentinel = object()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: sentinel)

    assert main_module._load_settings_or_default(settings_path) is sentinel
