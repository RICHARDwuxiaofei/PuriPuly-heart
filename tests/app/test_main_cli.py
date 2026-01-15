from __future__ import annotations

import sys
from types import ModuleType

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


def test_load_settings_or_default_loads_when_exists(monkeypatch, tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")

    sentinel = object()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: sentinel)

    assert main_module._load_settings_or_default(settings_path) is sentinel
