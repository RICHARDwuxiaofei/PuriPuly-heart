from __future__ import annotations

from dataclasses import dataclass, replace

import puripuly_heart.app.headless_runtime_config as runtime_config_module
import puripuly_heart.app.wiring as wiring_module
import puripuly_heart.main as cli
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


@dataclass
class FakeRunner:
    runtime_config: object
    llm: object | None
    last_llm: object | None = None

    async def run(self) -> int:
        FakeRunner.last_llm = self.llm
        return 0


def test_run_stdin_use_llm_wires_llm(monkeypatch, tmp_path):
    llm_obj = object()
    monkeypatch.setattr(cli, "HeadlessStdinRunner", FakeRunner)
    monkeypatch.setattr(
        runtime_config_module,
        "create_secret_store_from_vnext_intent",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        wiring_module,
        "create_llm_provider_from_resolved_config",
        lambda *_a, **_k: llm_obj,
    )

    code = cli.main(["--config", str(tmp_path / "settings.json"), "run-stdin", "--use-llm"])
    assert code == 0
    assert FakeRunner.last_llm is llm_obj


def test_run_stdin_use_llm_returns_error_on_init_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "HeadlessStdinRunner", FakeRunner)
    monkeypatch.setattr(
        runtime_config_module,
        "create_secret_store_from_vnext_intent",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)

    def _boom(*_a, **_k):
        raise ValueError("missing secret")

    monkeypatch.setattr(wiring_module, "create_llm_provider_from_resolved_config", _boom)

    code = cli.main(["--config", str(tmp_path / "settings.json"), "run-stdin", "--use-llm"])
    assert code == 2


def test_run_stdin_use_llm_forwards_qwen_low_latency_mode(monkeypatch, tmp_path):
    settings = replace(
        AppSettingsVNext(),
        intent=replace(
            AppSettingsVNext().intent,
            stt=replace(AppSettingsVNext().intent.stt, low_latency_mode=False),
        ),
    )
    monkeypatch.setattr(cli, "_load_settings_or_default", lambda _path: settings)
    monkeypatch.setattr(cli, "HeadlessStdinRunner", FakeRunner)
    monkeypatch.setattr(
        runtime_config_module,
        "create_secret_store_from_vnext_intent",
        lambda *_a, **_k: object(),
    )

    captured: dict[str, object] = {}

    def fake_create_llm(*_a, **kwargs):
        captured["qwen_low_latency_mode"] = kwargs.get("qwen_low_latency_mode")
        return "llm"

    monkeypatch.setattr(wiring_module, "create_llm_provider_from_resolved_config", fake_create_llm)

    code = cli.main(["--config", str(tmp_path / "settings.json"), "run-stdin", "--use-llm"])

    assert code == 0
    assert captured["qwen_low_latency_mode"] is False


def test_run_stdin_without_use_llm_does_not_create_llm_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "HeadlessStdinRunner", FakeRunner)

    def raise_if_called(*_a, **_k):
        raise AssertionError("LLM provider should not be created without --use-llm")

    monkeypatch.setattr(wiring_module, "create_llm_provider_from_resolved_config", raise_if_called)

    code = cli.main(["--config", str(tmp_path / "settings.json"), "run-stdin"])

    assert code == 0
