from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import sys
from logging.handlers import QueueHandler
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import puripuly_heart.main as main_module
from puripuly_heart import __version__
from puripuly_heart.core.runtime_logging import (
    SessionRuntimeLoggingService,
    configure_main_logging,
)


def test_main_version_prints(capsys) -> None:
    result = main_module.main(["--version"])
    assert result == 0
    assert capsys.readouterr().out.strip() == __version__


def test_main_version_prints_without_soxr_runtime_startup_check(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        main_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: pytest.fail("--version should not run the soxr startup check"),
        raising=False,
    )

    result = main_module.main(["--version"])

    assert result == 0
    assert capsys.readouterr().out.strip() == __version__


@pytest.mark.parametrize(
    "argv",
    [
        ["osc-send", "hello"],
        ["run-stdin"],
        ["run-mic"],
    ],
)
def test_main_rejects_removed_cli_commands(argv, capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main_module.main(argv)

    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def _install_async_gui_harness(monkeypatch, tmp_path, calls, *, on_main_gui=None) -> None:
    from puripuly_heart.app import wiring_composition
    from puripuly_heart.app.adapters import overlay_lifecycle_production
    from puripuly_heart.ui import app as ui_app
    from puripuly_heart.ui import fonts

    class Runtime:
        async def start(self, *, auto_flush_osc=True) -> None:
            calls["runtime_start"] = auto_flush_osc

        async def shutdown(self) -> None:
            calls["runtime_stop"] = int(calls.get("runtime_stop", 0)) + 1

    class Overlay:
        async def startup(self) -> None:
            calls["overlay_start"] = True

        async def shutdown(self) -> None:
            calls["overlay_stop"] = int(calls.get("overlay_stop", 0)) + 1

    class Controller:
        async def prepare_presentation(self) -> None:
            calls["presentation_prepare"] = True

        async def start_rendering(self) -> None:
            calls["render_start"] = True

        async def freeze_application_ingress(self) -> None:
            calls["ingress_freeze"] = True

        async def stop_rendering(self, failures=()) -> None:
            calls["render_stop"] = len(failures)

    class Page:
        on_disconnect = None

        def run_task(self, handler):  # noqa: ANN001, ANN201
            return asyncio.create_task(handler())

    overlay = Overlay()
    runtime = Runtime()
    monkeypatch.setattr(
        wiring_composition,
        "create_overlay_production_composition",
        lambda **_kwargs: SimpleNamespace(
            commands=overlay,
            state=object(),
            transactions=object(),
            ui_projection=object(),
            audio_gate=object(),
            runtime=object(),
        ),
    )
    monkeypatch.setattr(
        wiring_composition,
        "create_application_runtime_production_composition",
        lambda *_args, **_kwargs: SimpleNamespace(
            runtime_host=runtime,
            canonical_commands=object(),
            start=runtime.start,
            shutdown=runtime.shutdown,
            close=runtime.shutdown,
        ),
    )
    monkeypatch.setattr(
        overlay_lifecycle_production,
        "resolve_overlay_lifecycle_configuration",
        lambda _settings: object(),
    )

    async def main_gui(page, **kwargs):  # noqa: ANN001, ANN003, ANN201
        calls["config_path"] = kwargs["config_path"]
        calls["debug_ui_preview"] = kwargs["debug_ui_preview"]
        calls["defer_startup"] = kwargs["defer_startup"]
        if on_main_gui is not None:
            on_main_gui()
        return SimpleNamespace(controller=Controller())

    async def complete_main_gui_startup(_app, _page) -> None:
        calls["ui_complete"] = True

    monkeypatch.setattr(ui_app, "main_gui", main_gui)
    monkeypatch.setattr(ui_app, "complete_main_gui_startup", complete_main_gui_startup)
    monkeypatch.setattr(fonts, "assets_dir", lambda: tmp_path)

    fake_flet = ModuleType("flet")

    async def fake_app_async(*, target, assets_dir) -> None:
        calls["assets_dir"] = assets_dir
        page = Page()
        await target(page)
        page.on_disconnect(None)

    fake_flet.app_async = fake_app_async
    monkeypatch.setitem(sys.modules, "flet", fake_flet)


def test_gui_cli_tests_do_not_reintroduce_synchronous_flet_contract() -> None:
    source = Path(__file__).read_text(encoding="utf-8")

    assert "fake_flet" + ".app =" not in source
    assert "calls" + '["target"]' not in source
    assert "asyncio.run" + "(calls" not in source


def test_main_run_gui_invokes_flet_app(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}
    _install_async_gui_harness(monkeypatch, tmp_path, calls)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-gui"])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    assert calls["config_path"] == config_path
    assert calls["debug_ui_preview"] is False
    assert calls["runtime_stop"] == calls["overlay_stop"] == 1


def test_main_default_invokes_gui(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}
    _install_async_gui_harness(monkeypatch, tmp_path, calls)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path)])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    assert calls["config_path"] == config_path
    assert calls["debug_ui_preview"] is False


def test_main_run_gui_passes_debug_ui_preview_flag(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}
    _install_async_gui_harness(monkeypatch, tmp_path, calls)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-gui", "--debug-ui-preview"])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    assert calls["config_path"] == config_path
    assert calls["debug_ui_preview"] is True


def test_main_run_gui_force_closes_logging_when_gui_runtime_logging_leaks(
    monkeypatch, tmp_path
) -> None:
    root_logger = logging.getLogger(f"test.main.gui.logging.force_close.{tmp_path.name}")
    root_logger.handlers.clear()
    root_logger.propagate = False
    leaked_services: list[SessionRuntimeLoggingService] = []
    calls: dict[str, object] = {}
    monkeypatch.setattr("puripuly_heart.core.runtime_logging.user_config_dir", lambda: tmp_path)

    monkeypatch.setattr(
        main_module,
        "configure_main_logging",
        lambda: configure_main_logging(root_logger=root_logger),
    )

    def leak_runtime_logging() -> None:
        leaked_services.append(SessionRuntimeLoggingService(root_logger=root_logger))

    _install_async_gui_harness(monkeypatch, tmp_path, calls, on_main_gui=leak_runtime_logging)

    try:
        result = main_module.main(["--config", str(tmp_path / "settings.json"), "run-gui"])

        assert result == 0
        assert leaked_services
        assert [
            handler for handler in root_logger.handlers if isinstance(handler, QueueHandler)
        ] == []
    finally:
        for service in leaked_services:
            service.close()


def test_main_default_gui_passes_debug_ui_preview_flag(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}
    _install_async_gui_harness(monkeypatch, tmp_path, calls)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "--debug-ui-preview"])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    assert calls["config_path"] == config_path
    assert calls["debug_ui_preview"] is True


def test_main_owns_gui_lifecycle_start_disconnect_and_awaited_stop(monkeypatch, tmp_path) -> None:
    events: list[str] = []
    loop_ids: list[int] = []
    from puripuly_heart.app import wiring_composition
    from puripuly_heart.app.adapters import overlay_lifecycle_production
    from puripuly_heart.ui import app as ui_app

    class Controller:
        async def prepare_presentation(self) -> None:
            loop_ids.append(id(asyncio.get_running_loop()))
            events.append("prepare")

        async def start_rendering(self) -> None:
            events.append("render_start")

        async def freeze_application_ingress(self) -> None:
            loop_ids.append(id(asyncio.get_running_loop()))
            events.append("freeze")

        async def stop_rendering(self, failures=()) -> None:
            events.append(f"render_stop:{len(failures)}")

    class Runtime:
        async def start(self, *, auto_flush_osc=True) -> None:
            events.append(f"runtime_start:{auto_flush_osc}")

        async def shutdown(self) -> None:
            loop_ids.append(id(asyncio.get_running_loop()))
            await asyncio.sleep(0)
            events.append("runtime_stop")

    class Overlay:
        async def startup(self) -> None:
            events.append("overlay_start")

        async def shutdown(self) -> None:
            events.append("overlay_stop")

    class Page:
        on_disconnect = None

        def __init__(self) -> None:
            self.tasks = []

        def run_task(self, callback):  # noqa: ANN001, ANN201
            task = asyncio.create_task(callback())
            self.tasks.append(task)
            return task

    fake_flet = ModuleType("flet")

    async def fake_app_async(*, target, assets_dir) -> None:
        _ = assets_dir

        async def run() -> None:
            page = Page()
            await target(page)
            events.append("target_return")
            page.on_disconnect(None)
            events.append("flet_return")

        await run()

    fake_flet.app_async = fake_app_async
    monkeypatch.setitem(sys.modules, "flet", fake_flet)

    runtime = Runtime()
    overlay = Overlay()
    monkeypatch.setattr(
        wiring_composition,
        "create_overlay_production_composition",
        lambda **_kwargs: SimpleNamespace(
            commands=overlay,
            state=object(),
            transactions=object(),
            ui_projection=object(),
            audio_gate=object(),
            runtime=object(),
        ),
    )
    monkeypatch.setattr(
        wiring_composition,
        "create_application_runtime_production_composition",
        lambda *_args, **_kwargs: SimpleNamespace(
            runtime_host=runtime,
            canonical_commands=object(),
            start=runtime.start,
            shutdown=runtime.shutdown,
            close=runtime.shutdown,
        ),
    )
    monkeypatch.setattr(
        overlay_lifecycle_production,
        "resolve_overlay_lifecycle_configuration",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        ui_app,
        "main_gui",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=SimpleNamespace(controller=Controller())),
    )

    async def complete(_app, _page) -> None:
        events.append("ui_complete")

    monkeypatch.setattr(ui_app, "complete_main_gui_startup", complete)
    monkeypatch.setattr(main_module, "_call_load_settings_or_default", lambda *_a, **_k: object())

    assert (
        main_module._run_gui(
            tmp_path / "settings.json",
            debug_ui_preview=False,
            allow_stable_settings_import=False,
        )
        == 0
    )
    assert events == [
        "prepare",
        "runtime_start:True",
        "overlay_start",
        "render_start",
        "ui_complete",
        "target_return",
        "flet_return",
        "freeze",
        "overlay_stop",
        "runtime_stop",
        "render_stop:0",
    ]
    assert len(set(loop_ids)) == 1


@pytest.mark.parametrize("failure_stage", ["ui", "complete_startup"])
def test_main_closes_constructed_resources_when_flet_swallows_construction_failure(
    monkeypatch, tmp_path, failure_stage
) -> None:
    from puripuly_heart.app import wiring_composition
    from puripuly_heart.app.adapters import overlay_lifecycle_production
    from puripuly_heart.app.services.application_lifecycle import ApplicationStartupError
    from puripuly_heart.ui import app as ui_app

    events: list[str] = []

    class SocketRuntime:
        def __init__(self) -> None:
            self.socket_open = True
            self.close_calls = 0

        async def start(self, *, auto_flush_osc=True) -> None:
            _ = auto_flush_osc

        async def shutdown(self) -> None:
            self.close_calls += 1
            self.socket_open = False
            events.append("socket_closed")

    class Overlay:
        close_calls = 0

        async def startup(self) -> None:
            return None

        async def shutdown(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                events.append("overlay_close_failed")
                raise RuntimeError("overlay close failed")
            events.append("overlay_closed")

    runtime = SocketRuntime()
    overlay = Overlay()

    fake_flet = ModuleType("flet")

    async def fake_app_async(*, target, assets_dir) -> None:
        _ = assets_dir
        try:
            await target(SimpleNamespace())
        except BaseException:
            events.append("flet_swallowed_target_failure")

    fake_flet.app_async = fake_app_async
    monkeypatch.setitem(sys.modules, "flet", fake_flet)
    monkeypatch.setattr(
        wiring_composition,
        "create_overlay_production_composition",
        lambda **_kwargs: SimpleNamespace(
            commands=overlay,
            state=object(),
            transactions=object(),
            ui_projection=object(),
            audio_gate=object(),
            runtime=object(),
        ),
    )
    monkeypatch.setattr(
        wiring_composition,
        "create_application_runtime_production_composition",
        lambda *_args, **_kwargs: SimpleNamespace(
            runtime_host=runtime,
            canonical_commands=object(),
            start=runtime.start,
            shutdown=runtime.shutdown,
            close=runtime.shutdown,
        ),
    )
    monkeypatch.setattr(
        overlay_lifecycle_production,
        "resolve_overlay_lifecycle_configuration",
        lambda _settings: object(),
    )

    async def fail_ui_construction(*_args, **_kwargs):
        if failure_stage == "ui":
            raise ValueError("presentation construction failed")
        return SimpleNamespace(controller=Controller())

    class Controller:
        async def prepare_presentation(self) -> None:
            return None

        async def start_rendering(self) -> None:
            return None

        async def freeze_application_ingress(self) -> None:
            return None

        async def stop_rendering(self, failures=()) -> None:
            _ = failures

    monkeypatch.setattr(ui_app, "main_gui", fail_ui_construction)

    async def complete_startup(*_args) -> None:
        if failure_stage == "complete_startup":
            raise ValueError("complete startup failed")

    monkeypatch.setattr(ui_app, "complete_main_gui_startup", complete_startup)
    monkeypatch.setattr(main_module, "_call_load_settings_or_default", lambda *_a, **_k: object())

    with pytest.raises(ApplicationStartupError) as raised:
        main_module._run_gui(
            tmp_path / "settings.json",
            debug_ui_preview=False,
            allow_stable_settings_import=False,
        )

    assert isinstance(raised.value.exceptions[0], ValueError)
    assert runtime.socket_open is False
    assert runtime.close_calls == 1
    assert overlay.close_calls == 2
    expected = ["overlay_close_failed", "socket_closed"]
    if failure_stage == "ui":
        expected.extend(["overlay_closed", "flet_swallowed_target_failure"])
    else:
        expected.extend(["flet_swallowed_target_failure", "overlay_closed"])
    assert events == expected


def test_real_main_gui_accepts_debug_ui_preview_keyword_only() -> None:
    from puripuly_heart.ui.app import main_gui

    parameters = inspect.signature(main_gui).parameters

    assert "debug_ui_preview" in parameters
    debug_ui_preview = parameters["debug_ui_preview"]
    assert debug_ui_preview.kind is inspect.Parameter.KEYWORD_ONLY
    assert debug_ui_preview.default is False


def test_main_production_overlay_graph_is_coherent_and_typed() -> None:
    from puripuly_heart.app.adapters.overlay_runtime_effects import (
        ProductionOverlaySafeLog,
        ProductionVrcMicrophoneEffects,
    )
    from puripuly_heart.app.adapters.overlay_ui_projection import ProductionUiProjection
    from puripuly_heart.app.wiring_composition import create_overlay_production_composition

    composition = create_overlay_production_composition()

    assert composition.commands is composition.state
    assert composition.commands.runtime is composition.runtime
    assert composition.runtime.renderer_output is composition.ui_projection
    assert composition.runtime.dashboard is composition.ui_projection
    assert isinstance(composition.ui_projection, ProductionUiProjection)
    assert isinstance(composition.logging, ProductionOverlaySafeLog)
    assert composition.runtime.safe_log is composition.logging
    assert isinstance(composition.vrc, ProductionVrcMicrophoneEffects)
    assert composition.runtime.vrc_microphone is composition.vrc
    assert composition.transactions is not None


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


def test_main_soxr_runtime_check_dispatches_runner(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        main_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: None,
        raising=False,
    )

    def fake_run_soxr_runtime_check() -> int:
        calls["called"] = True
        return 0

    monkeypatch.setattr(
        main_module,
        "run_soxr_runtime_check",
        fake_run_soxr_runtime_check,
        raising=False,
    )

    config_path = tmp_path / "settings.json"
    try:
        result = main_module.main(["--config", str(config_path), "soxr-runtime-check"])
    except SystemExit as exc:  # pragma: no cover - red phase guard
        pytest.fail(f"unexpected SystemExit: {exc}")

    assert result == 0
    assert calls["called"] is True


def test_run_soxr_runtime_check_rejects_non_windows(monkeypatch, capsys) -> None:
    try:
        runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")
    except ModuleNotFoundError:  # pragma: no cover - red phase guard
        pytest.fail("soxr_runtime_check module is missing")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "linux", raising=False)
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: pytest.fail("should not validate soxr runtime on non-Windows"),
        raising=False,
    )

    result = runtime_check_module.run_soxr_runtime_check()

    assert result == 2
    assert (
        capsys.readouterr().out.strip() == "Error: soxr-runtime-check is only supported on Windows"
    )


def test_run_soxr_runtime_check_reports_runtime_validation_failure(monkeypatch, capsys) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")

    class FakeSoxrRuntimeAvailabilityError(RuntimeError):
        pass

    def raise_runtime_error() -> None:
        raise FakeSoxrRuntimeAvailabilityError("missing packaged soxr sibling dll")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module,
        "SoxrRuntimeAvailabilityError",
        FakeSoxrRuntimeAvailabilityError,
        raising=False,
    )
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        raise_runtime_error,
        raising=False,
    )

    result = runtime_check_module.run_soxr_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to verify packaged soxr runtime: missing packaged soxr sibling dll"
    )


def test_run_soxr_runtime_check_reports_soxr_import_or_smoke_failure(
    monkeypatch, capsys, tmp_path
) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")

    runtime_paths = type(
        "RuntimePaths",
        (),
        {
            "extension_path": tmp_path / "soxr_ext.cp312-win_amd64.pyd",
            "runtime_dir": tmp_path,
            "sibling_dll_path": tmp_path / "soxr.dll",
        },
    )()

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: runtime_paths,
        raising=False,
    )
    real_import_module = runtime_check_module.importlib.import_module

    def fake_import_module(name: str, *args, **kwargs):
        if name == "soxr":
            raise ImportError("native extension load failed")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(
        runtime_check_module.importlib,
        "import_module",
        fake_import_module,
    )

    result = runtime_check_module.run_soxr_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to import or smoke-test soxr: native extension load failed"
    )


def test_run_soxr_runtime_check_imports_soxr_runs_smoke_and_reports_paths(
    monkeypatch, capsys, tmp_path
) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")
    runtime_dir = tmp_path / "soxr"
    runtime_dir.mkdir()
    extension_path = runtime_dir / "soxr_ext.cp312-win_amd64.pyd"
    extension_path.write_bytes(b"")
    sibling_dll_path = runtime_dir / "soxr.dll"
    sibling_dll_path.write_bytes(b"")

    runtime_paths = type(
        "RuntimePaths",
        (),
        {
            "extension_path": extension_path,
            "runtime_dir": runtime_dir,
            "sibling_dll_path": sibling_dll_path,
        },
    )()
    calls: dict[str, object] = {}

    class FakeResampleStream:
        def __init__(self, in_rate, out_rate, num_channels, dtype="float32"):
            calls["init"] = (in_rate, out_rate, num_channels, dtype)

        def resample_chunk(self, samples, last=False):
            calls["len"] = len(samples)
            calls["last"] = last
            return [0.0, 0.0, 0.0]

    fake_soxr = ModuleType("soxr")
    fake_soxr.ResampleStream = FakeResampleStream

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: runtime_paths,
        raising=False,
    )

    real_import_module = runtime_check_module.importlib.import_module

    def fake_import_module(name: str, *args, **kwargs):
        if name == "soxr":
            return fake_soxr
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(runtime_check_module.importlib, "import_module", fake_import_module)

    result = runtime_check_module.run_soxr_runtime_check()

    assert result == 0
    assert calls["init"] == (48000, 16000, 1, "float32")
    assert calls["len"] == 480
    assert calls["last"] is True
    assert capsys.readouterr().out.strip().splitlines() == [
        f"soxr_extension_path={extension_path}",
        f"soxr_runtime_dir={runtime_dir}",
        f"soxr_sibling_dll={sibling_dll_path}",
    ]


def test_run_soxr_runtime_check_writes_json_report_when_env_var_is_set(
    monkeypatch, tmp_path
) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")
    report_path = tmp_path / "soxr-runtime-report.json"
    runtime_paths = type(
        "RuntimePaths",
        (),
        {
            "extension_path": Path("C:/temp/soxr/soxr_ext.cp312-win_amd64.pyd"),
            "runtime_dir": Path("C:/temp/soxr"),
            "sibling_dll_path": Path("C:/temp/soxr/soxr.dll"),
        },
    )()

    class FakeResampleStream:
        def __init__(self, in_rate, out_rate, channels, dtype="float32"):
            self.args = (in_rate, out_rate, channels, dtype)

        def resample_chunk(self, samples, last=False):
            return [0.0, 0.0, 0.0]

    fake_soxr_module = type("FakeSoxr", (), {"ResampleStream": FakeResampleStream})
    fake_soxr_ext_module = type("FakeSoxrExt", (), {"__file__": str(runtime_paths.extension_path)})

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)

    def fake_import_module(name: str):
        if name == "soxr":
            return fake_soxr_module
        if name == "soxr.soxr_ext":
            return fake_soxr_ext_module
        raise AssertionError(name)

    monkeypatch.setenv("PURIPULY_HEART_SOXR_RUNTIME_REPORT_PATH", str(report_path))
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: runtime_paths,
    )
    monkeypatch.setattr(
        runtime_check_module,
        "_resolve_loaded_soxr_dll_path",
        lambda: runtime_paths.sibling_dll_path,
    )
    monkeypatch.setattr(runtime_check_module.importlib, "import_module", fake_import_module)

    assert runtime_check_module.run_soxr_runtime_check() == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["expected_extension_path"] == str(runtime_paths.extension_path)
    assert payload["expected_sibling_dll_path"] == str(runtime_paths.sibling_dll_path)
    assert payload["imported_extension_path"] == str(runtime_paths.extension_path)
    assert payload["loaded_sibling_dll_path"] == str(runtime_paths.sibling_dll_path)


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

    class FakeLoadResult:
        settings = sentinel
        status = "ok"
        error = None
        warnings = []

    monkeypatch.setattr(
        "puripuly_heart.config.settings_vnext.facade.load_vnext_settings",
        lambda _path: FakeLoadResult(),
    )

    assert main_module._load_settings_or_default(settings_path) is sentinel


def test_settings_config_path_marks_default_as_implicit(monkeypatch, tmp_path) -> None:
    default_path = tmp_path / "vnext" / "settings.json"
    monkeypatch.setattr(main_module, "default_settings_path", lambda: default_path)
    args = type("Args", (), {})()

    path, explicit = main_module._settings_config_path(args)

    assert path == default_path
    assert explicit is False


def test_settings_config_path_marks_custom_config_as_explicit(tmp_path) -> None:
    custom_path = tmp_path / "custom.json"
    args = type("Args", (), {"config": custom_path})()

    path, explicit = main_module._settings_config_path(args)

    assert path == custom_path
    assert explicit is True


def test_production_cli_does_not_advertise_or_accept_process_capture_smoke(capsys) -> None:
    help_text = main_module.build_parser().format_help()

    assert "process-capture-runtime-check" not in help_text
    with pytest.raises(SystemExit):
        main_module.main(["process-capture-runtime-check"])
    assert "process-capture-runtime-check" not in capsys.readouterr().out
