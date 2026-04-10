from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from puripuly_heart.core.local_qwen_runtime import LocalQwenRuntimeBootstrapError
from puripuly_heart.core.local_stt_assets import (
    InstalledLocalSTTManifest,
    LocalSTTManifestInvalidError,
    LocalSTTModelMissingError,
)
from puripuly_heart.providers.stt import local_qwen_sherpa as local_qwen_module
from puripuly_heart.providers.stt.local_qwen_sherpa import (
    LocalQwenSherpaInferenceError,
    LocalQwenSherpaLoadError,
    LocalQwenSherpaSTTBackend,
)


def test_local_qwen_backend_uses_thread_count_3_by_default() -> None:
    assert local_qwen_module.DEFAULT_SHERPA_NUM_THREADS == 3
    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"))
    assert backend.num_threads == 3


def _installed_manifest() -> InstalledLocalSTTManifest:
    return InstalledLocalSTTManifest(
        manifest_version=1,
        model_id="qwen3-asr-0.6b-int8-sherpa",
        engine="sherpa-onnx",
        install_dirname="qwen3-asr-0.6b-int8-sherpa",
        selected_source="huggingface",
        selected_revision="rev-1",
    )


def _install_fake_sherpa(
    monkeypatch: pytest.MonkeyPatch,
    *,
    recognizer_factory,
    qwen3_error: Exception | None = None,
    bootstrap_runtime=None,
) -> dict[str, object]:
    factory_calls: dict[str, object] = {}

    class ConfigNode:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeOfflineQwen3ASRModelConfig(ConfigNode):
        def __init__(self, **kwargs) -> None:
            if qwen3_error is not None:
                raise qwen3_error
            super().__init__(**kwargs)
            factory_calls["qwen3"] = kwargs

    class FakeOfflineModelConfig(ConfigNode):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            factory_calls["model"] = kwargs

    class FakeFeatureExtractorConfig(ConfigNode):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            factory_calls["feat"] = kwargs

    class FakeOfflineRecognizerConfig(ConfigNode):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            factory_calls["recognizer"] = kwargs

    fake_sherpa = ModuleType("sherpa_onnx")
    fake_sherpa.OfflineQwen3ASRModelConfig = FakeOfflineQwen3ASRModelConfig
    fake_sherpa.OfflineModelConfig = FakeOfflineModelConfig
    fake_sherpa.FeatureExtractorConfig = FakeFeatureExtractorConfig
    fake_sherpa.OfflineRecognizerConfig = FakeOfflineRecognizerConfig

    fake_offline_recognizer = ModuleType("sherpa_onnx.offline_recognizer")
    fake_offline_recognizer._Recognizer = recognizer_factory

    if bootstrap_runtime is None:

        def bootstrap_runtime() -> Path:
            return Path("C:/runtime")

    monkeypatch.setattr(local_qwen_module, "ensure_local_qwen_windows_runtime", bootstrap_runtime)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_sherpa)
    monkeypatch.setitem(sys.modules, "sherpa_onnx.offline_recognizer", fake_offline_recognizer)
    return factory_calls


def test_create_local_qwen_sherpa_recognizer_bootstraps_windows_runtime_before_using_sherpa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    def fake_bootstrap() -> Path:
        order.append("bootstrap")
        return Path("C:/runtime")

    class ConfigNode:
        def __init__(self, **kwargs) -> None:
            order.append(type(self).__name__)
            self.kwargs = kwargs

    class FakeOfflineQwen3ASRModelConfig(ConfigNode):
        pass

    class FakeOfflineModelConfig(ConfigNode):
        pass

    class FakeFeatureExtractorConfig(ConfigNode):
        pass

    class FakeOfflineRecognizerConfig(ConfigNode):
        pass

    class FakeRecognizer:
        def __init__(self, recognizer_config) -> None:
            order.append("recognizer")
            self.recognizer_config = recognizer_config

    fake_sherpa = ModuleType("sherpa_onnx")
    fake_sherpa.OfflineQwen3ASRModelConfig = FakeOfflineQwen3ASRModelConfig
    fake_sherpa.OfflineModelConfig = FakeOfflineModelConfig
    fake_sherpa.FeatureExtractorConfig = FakeFeatureExtractorConfig
    fake_sherpa.OfflineRecognizerConfig = FakeOfflineRecognizerConfig

    fake_offline_recognizer = ModuleType("sherpa_onnx.offline_recognizer")
    fake_offline_recognizer._Recognizer = FakeRecognizer

    monkeypatch.setattr(local_qwen_module, "ensure_local_qwen_windows_runtime", fake_bootstrap)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_sherpa)
    monkeypatch.setitem(sys.modules, "sherpa_onnx.offline_recognizer", fake_offline_recognizer)

    recognizer = local_qwen_module.create_local_qwen_sherpa_recognizer(
        model_dir=Path("/models/qwen"),
        num_threads=3,
    )

    assert isinstance(recognizer, FakeRecognizer)
    assert order[0] == "bootstrap"
    assert order.index("bootstrap") < order.index("FakeOfflineQwen3ASRModelConfig")
    assert order.index("bootstrap") < order.index("recognizer")


@pytest.mark.asyncio
async def test_local_qwen_backend_emits_final_transcript_on_speech_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_dir = Path("/models/qwen")
    recognizer_state: dict[str, object] = {}

    class FakeStream:
        def __init__(self) -> None:
            self.accepted: list[tuple[int, object]] = []
            self.result = SimpleNamespace(text="hello local qwen")

        def accept_waveform(self, sample_rate: int, samples) -> None:
            self.accepted.append((sample_rate, samples))

    class FakeRecognizer:
        def __init__(self) -> None:
            self.streams: list[FakeStream] = []

        def create_stream(self) -> FakeStream:
            stream = FakeStream()
            self.streams.append(stream)
            recognizer_state["stream"] = stream
            return stream

        def decode_stream(self, stream: FakeStream) -> None:
            recognizer_state["decoded"] = stream

    class FakeRecognizerEngine(FakeRecognizer):
        def __init__(self, recognizer_config) -> None:
            super().__init__()
            recognizer_state["recognizer_config"] = recognizer_config
            recognizer_state["recognizer"] = self

    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    factory_calls = _install_fake_sherpa(
        monkeypatch,
        recognizer_factory=FakeRecognizerEngine,
    )

    backend = LocalQwenSherpaSTTBackend(model_dir=model_dir, sample_rate_hz=16000, num_threads=3)
    session = await backend.open_session()
    await session.send_audio(b"\x00\x00\xff\x7f")
    await session.on_speech_end()

    gen = session.events()
    event = await gen.__anext__()

    assert event.text == "hello local qwen"
    assert event.is_final is True
    assert factory_calls == {
        "qwen3": {
            "conv_frontend": str(model_dir / "conv_frontend.onnx"),
            "encoder": str(model_dir / "encoder.int8.onnx"),
            "decoder": str(model_dir / "decoder.int8.onnx"),
            "tokenizer": str(model_dir / "tokenizer"),
            "max_total_len": 512,
            "max_new_tokens": 128,
            "temperature": 1e-06,
            "top_p": 0.8,
            "seed": 42,
        },
        "model": {
            "qwen3_asr": recognizer_state["recognizer_config"]
            .kwargs["model_config"]
            .kwargs["qwen3_asr"],
            "num_threads": 3,
            "debug": False,
            "provider": "cpu",
        },
        "feat": {
            "sampling_rate": 16000,
            "feature_dim": 128,
        },
        "recognizer": {
            "feat_config": recognizer_state["recognizer_config"].kwargs["feat_config"],
            "model_config": recognizer_state["recognizer_config"].kwargs["model_config"],
            "decoding_method": "greedy_search",
        },
    }
    assert recognizer_state["decoded"] is recognizer_state["stream"]


@pytest.mark.asyncio
async def test_local_qwen_backend_sets_stream_language_hint_and_hotwords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recognizer_state: dict[str, object] = {}

    class FakeStream:
        def __init__(self) -> None:
            self.options: dict[str, str] = {}
            self.result = SimpleNamespace(text="hello local qwen")

        def set_option(self, key: str, value: str) -> None:
            self.options[key] = value

        def accept_waveform(self, sample_rate: int, samples) -> None:
            _ = (sample_rate, samples)

    class FakeRecognizer:
        def create_stream(self) -> FakeStream:
            stream = FakeStream()
            recognizer_state["stream"] = stream
            return stream

        def decode_stream(self, stream: FakeStream) -> None:
            recognizer_state["decoded"] = stream

    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    _install_fake_sherpa(monkeypatch, recognizer_factory=lambda _config: FakeRecognizer())

    backend = LocalQwenSherpaSTTBackend(
        model_dir=Path("/models/qwen"),
        language_hint="Korean",
        hotwords=("Puripuly", "VRChat"),
    )
    session = await backend.open_session()
    await session.send_audio(b"\x00\x00")
    await session.on_speech_end()

    stream = recognizer_state["stream"]
    assert isinstance(stream, FakeStream)
    assert stream.options == {"language": "Korean", "hotwords": "Puripuly,VRChat"}


@pytest.mark.asyncio
async def test_local_qwen_backend_resamples_8000_input_to_16000_for_recognizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recognizer_state: dict[str, object] = {}

    class FakeStream:
        def __init__(self) -> None:
            self.accepted: list[tuple[int, np.ndarray]] = []
            self.result = SimpleNamespace(text="hello local qwen")

        def accept_waveform(self, sample_rate: int, samples) -> None:
            self.accepted.append((sample_rate, np.asarray(samples)))

    class FakeRecognizer:
        def create_stream(self) -> FakeStream:
            stream = FakeStream()
            recognizer_state["stream"] = stream
            return stream

        def decode_stream(self, stream: FakeStream) -> None:
            recognizer_state["decoded"] = stream

    class FakeRecognizerEngine(FakeRecognizer):
        def __init__(self, recognizer_config) -> None:
            recognizer_state["recognizer_config"] = recognizer_config

    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    _install_fake_sherpa(monkeypatch, recognizer_factory=FakeRecognizerEngine)

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"), sample_rate_hz=8000)
    session = await backend.open_session()
    await session.send_audio(b"\x00\x00\xff\x7f")
    await session.on_speech_end()

    stream = recognizer_state["stream"]
    assert isinstance(stream, FakeStream)
    accepted_rate, accepted_samples = stream.accepted[0]
    assert accepted_rate == 16000
    assert accepted_samples.shape == (4,)
    assert accepted_samples[0] == pytest.approx(0.0)
    assert accepted_samples[-1] == pytest.approx(32767 / 32768.0, rel=1e-4)
    assert (
        recognizer_state["recognizer_config"].kwargs["feat_config"].kwargs["sampling_rate"] == 16000
    )


@pytest.mark.asyncio
async def test_local_qwen_backend_keeps_16000_input_without_resampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recognizer_state: dict[str, object] = {}

    class FakeStream:
        def __init__(self) -> None:
            self.accepted: list[tuple[int, np.ndarray]] = []
            self.result = SimpleNamespace(text="hello local qwen")

        def accept_waveform(self, sample_rate: int, samples) -> None:
            self.accepted.append((sample_rate, np.asarray(samples)))

    class FakeRecognizer:
        def create_stream(self) -> FakeStream:
            stream = FakeStream()
            recognizer_state["stream"] = stream
            return stream

        def decode_stream(self, stream: FakeStream) -> None:
            recognizer_state["decoded"] = stream

    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    _install_fake_sherpa(monkeypatch, recognizer_factory=lambda _config: FakeRecognizer())

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"), sample_rate_hz=16000)
    session = await backend.open_session()
    await session.send_audio(b"\x00\x00\xff\x7f")
    await session.on_speech_end()

    stream = recognizer_state["stream"]
    assert isinstance(stream, FakeStream)
    accepted_rate, accepted_samples = stream.accepted[0]
    assert accepted_rate == 16000


@pytest.mark.asyncio
async def test_local_qwen_backend_runtime_validator_runs_only_until_recognizer_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []

    def fake_runtime_ready(model_dir: Path, **_kwargs) -> InstalledLocalSTTManifest:
        calls.append(model_dir)
        return _installed_manifest()

    monkeypatch.setattr(local_qwen_module, "validate_local_stt_runtime_ready", fake_runtime_ready)
    _install_fake_sherpa(monkeypatch, recognizer_factory=lambda _config: SimpleNamespace())

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"), sample_rate_hz=16000)

    await backend.open_session()
    await backend.open_session()

    assert calls == [Path("/models/qwen")]


@pytest.mark.asyncio
async def test_local_qwen_backend_revalidates_runtime_assets_after_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []

    def fake_runtime_ready(model_dir: Path, **_kwargs) -> InstalledLocalSTTManifest:
        calls.append(model_dir)
        return _installed_manifest()

    monkeypatch.setattr(local_qwen_module, "validate_local_stt_runtime_ready", fake_runtime_ready)
    _install_fake_sherpa(monkeypatch, recognizer_factory=lambda _config: SimpleNamespace())

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"), sample_rate_hz=16000)

    await backend.open_session()
    await backend.close()
    await backend.open_session()

    assert calls == [Path("/models/qwen"), Path("/models/qwen")]


@pytest.mark.asyncio
async def test_local_qwen_backend_surfaces_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: (_ for _ in ()).throw(LocalSTTModelMissingError("missing")),
    )

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"))

    with pytest.raises(LocalSTTModelMissingError, match="missing"):
        await backend.open_session()


@pytest.mark.asyncio
async def test_local_qwen_backend_surfaces_invalid_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LocalSTTManifestInvalidError("manifest invalid")
        ),
    )

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"))

    with pytest.raises(LocalSTTManifestInvalidError, match="manifest invalid"):
        await backend.open_session()


@pytest.mark.asyncio
async def test_local_qwen_backend_wraps_load_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    _install_fake_sherpa(
        monkeypatch,
        recognizer_factory=lambda _config: None,
        qwen3_error=RuntimeError("load failed"),
    )

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"))

    with pytest.raises(LocalQwenSherpaLoadError, match="load failed"):
        await backend.open_session()


@pytest.mark.asyncio
async def test_local_qwen_backend_wraps_runtime_bootstrap_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    monkeypatch.setattr(
        local_qwen_module,
        "create_local_qwen_sherpa_recognizer",
        lambda **_kwargs: (_ for _ in ()).throw(
            LocalQwenRuntimeBootstrapError("runtime bootstrap failed")
        ),
    )

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"))

    with pytest.raises(LocalQwenSherpaLoadError, match="runtime bootstrap failed") as exc_info:
        await backend.open_session()

    assert isinstance(exc_info.value.__cause__, LocalQwenRuntimeBootstrapError)


@pytest.mark.asyncio
async def test_local_qwen_backend_preserves_missing_onnxruntime_bootstrap_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    _install_fake_sherpa(
        monkeypatch,
        recognizer_factory=lambda _config: None,
        bootstrap_runtime=lambda: (_ for _ in ()).throw(
            ModuleNotFoundError("No module named 'onnxruntime'")
        ),
    )

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"))

    with pytest.raises(LocalQwenSherpaLoadError, match="onnxruntime") as exc_info:
        await backend.open_session()

    assert str(exc_info.value) != "failed to import sherpa_onnx"
    assert isinstance(exc_info.value.__cause__, ModuleNotFoundError)


@pytest.mark.asyncio
async def test_local_qwen_session_surfaces_inference_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.result = SimpleNamespace(text="")

        def accept_waveform(self, sample_rate: int, samples) -> None:
            _ = sample_rate, samples

    class FakeRecognizer:
        def create_stream(self) -> FakeStream:
            return FakeStream()

        def decode_stream(self, stream: FakeStream) -> None:
            _ = stream
            raise RuntimeError("decode failed")

    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    _install_fake_sherpa(monkeypatch, recognizer_factory=lambda _config: FakeRecognizer())

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"))
    session = await backend.open_session()
    await session.send_audio(b"\x00\x00")
    await session.on_speech_end()

    gen = session.events()
    with pytest.raises(LocalQwenSherpaInferenceError, match="decode failed"):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_local_qwen_session_close_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.result = SimpleNamespace(text="")

        def accept_waveform(self, sample_rate: int, samples) -> None:
            _ = sample_rate, samples

    class FakeRecognizer:
        def create_stream(self) -> FakeStream:
            return FakeStream()

        def decode_stream(self, stream: FakeStream) -> None:
            _ = stream

    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    _install_fake_sherpa(monkeypatch, recognizer_factory=lambda _config: FakeRecognizer())

    backend = LocalQwenSherpaSTTBackend(model_dir=Path("/models/qwen"))
    session = await backend.open_session()

    await session.close()
    await session.close()

    gen = session.events()
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_local_qwen_session_logs_inference_metrics_and_summary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    texts = iter(["first local qwen", "second local qwen"])

    class FakeStream:
        def __init__(self) -> None:
            self.result = SimpleNamespace(text=next(texts))

        def accept_waveform(self, sample_rate: int, samples) -> None:
            _ = sample_rate, samples

    class FakeRecognizer:
        def create_stream(self) -> FakeStream:
            return FakeStream()

        def decode_stream(self, stream: FakeStream) -> None:
            _ = stream

    perf_values = iter([1.0, 1.25, 2.0, 2.2])
    monkeypatch.setattr(local_qwen_module.time, "perf_counter", lambda: next(perf_values))
    monkeypatch.setattr(
        local_qwen_module,
        "validate_local_stt_runtime_ready",
        lambda *args, **kwargs: _installed_manifest(),
    )
    _install_fake_sherpa(monkeypatch, recognizer_factory=lambda _config: FakeRecognizer())

    backend = LocalQwenSherpaSTTBackend(
        model_dir=Path("/models/qwen"),
        sample_rate_hz=16000,
        stream_label="peer",
    )
    session = await backend.open_session()

    with caplog.at_level(logging.INFO, logger="puripuly_heart.providers.stt.local_qwen_sherpa"):
        await session.send_audio(b"\x00\x00" * 16000)
        await session.on_speech_end()
        await session.send_audio(b"\x00\x00" * 8000)
        await session.on_speech_end()
        await session.close()

    messages = [record.getMessage() for record in caplog.records]
    final_messages = [message for message in messages if "Transcript:" in message]
    assert len(final_messages) == 2
    assert (
        "[STT][local_qwen][peer] Transcript: 'first local qwen' "
        "(final, audio_ms=1000.0, inference_ms=250.0, rtf=0.250)"
    ) in final_messages
    assert (
        "[STT][local_qwen][peer] Transcript: 'second local qwen' "
        "(final, audio_ms=500.0, inference_ms=200.0, rtf=0.400)"
    ) in final_messages
    assert (
        "[STT][local_qwen][peer] Session summary: utterances=2 "
        "total_audio_ms=1500.0 total_inference_ms=450.0 weighted_total_rtf=0.300 mean_rtf=0.325"
    ) in messages
