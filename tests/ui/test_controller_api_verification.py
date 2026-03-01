from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    QwenLLMModel,
    STTProviderName,
)
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider
from puripuly_heart.providers.stt.qwen_asr import QwenASRRealtimeSTTBackend
from puripuly_heart.ui import controller as controller_module
from puripuly_heart.ui.controller import GuiController


class DummySecrets:
    def __init__(self, values: dict[str, str]):
        self._values = values

    def get(self, key: str) -> str | None:
        return self._values.get(key)


class DummyDashboard:
    def __init__(self) -> None:
        self.translation_needs_key: bool | None = None
        self.translation_enabled: bool | None = None
        self.stt_needs_key: bool | None = None
        self.stt_enabled: bool | None = None

    def set_translation_needs_key(self, value: bool) -> None:
        self.translation_needs_key = value

    def set_translation_enabled(self, value: bool) -> None:
        self.translation_enabled = value

    def set_stt_needs_key(self, value: bool) -> None:
        self.stt_needs_key = value

    def set_stt_enabled(self, value: bool) -> None:
        self.stt_enabled = value


class DummyHub:
    def __init__(self) -> None:
        self.llm = object()
        self.stt = object()
        self.translation_enabled = True


@pytest.mark.asyncio
async def test_verify_qwen_llm_api_key_uses_async_verifier_in_low_latency(monkeypatch) -> None:
    settings = AppSettings()
    settings.stt.low_latency_mode = True
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_PLUS
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings

    seen: dict[str, str] = {}

    async def fake_async_verify(api_key: str, *, base_url: str, model: str) -> bool:
        seen["api_key"] = api_key
        seen["base_url"] = base_url
        seen["model"] = model
        return True

    async def fail_sync_verify(*_args, **_kwargs) -> bool:
        raise AssertionError("sync verifier must not be called in low latency mode")

    monkeypatch.setattr(AsyncQwenLLMProvider, "verify_api_key", staticmethod(fake_async_verify))
    monkeypatch.setattr(QwenLLMProvider, "verify_api_key", staticmethod(fail_sync_verify))

    ok = await controller._verify_qwen_llm_api_key(
        "secret", base_url="https://dashscope.aliyuncs.com/api/v1"
    )

    assert ok is True
    assert seen == {
        "api_key": "secret",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.5-plus",
    }


@pytest.mark.asyncio
async def test_verify_and_update_status_uses_qwen_specific_verifiers(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.provider.stt = STTProviderName.QWEN_ASR
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"alibaba_api_key_beijing": "secret"}),
    )

    llm_seen: list[tuple[str, str]] = []

    async def fake_verify_qwen_llm(self, api_key: str, *, base_url: str) -> bool:
        llm_seen.append((api_key, base_url))
        return True

    async def fail_qwen_asr_verify(*_args, **_kwargs) -> bool:
        raise AssertionError("qwen ASR verifier should not be called when Alibaba result is shared")

    async def fail_legacy_verify(*_args, **_kwargs) -> bool:
        raise AssertionError("legacy llm verifier path must not be called")

    monkeypatch.setattr(GuiController, "_verify_qwen_llm_api_key", fake_verify_qwen_llm)
    monkeypatch.setattr(
        QwenASRRealtimeSTTBackend, "verify_api_key", staticmethod(fail_qwen_asr_verify)
    )
    monkeypatch.setattr(QwenLLMProvider, "verify_api_key", staticmethod(fail_legacy_verify))

    await controller._verify_and_update_status()

    assert llm_seen == [("secret", "https://dashscope.aliyuncs.com/api/v1")]
    assert app.view_dashboard.translation_needs_key is False
    assert app.view_dashboard.stt_needs_key is False
