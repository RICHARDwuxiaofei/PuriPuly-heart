from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from puripuly_heart.app.adapters.gpu_worker_process import (
    DefaultGpuWorkerProcessFactory,
)
from puripuly_heart.app.ports.gpu_worker import (
    GpuWorkerClosedError,
    GpuWorkerRequestError,
)

FAKE_WORKER = Path(__file__).parents[1] / "fixtures" / "fake_gpu_worker.py"


def _factory(**overrides: object) -> DefaultGpuWorkerProcessFactory:
    values = {
        "executable_path": FAKE_WORKER,
        "command_prefix": (sys.executable,),
        "startup_timeout_s": 1.0,
        "request_timeout_s": 1.0,
        "heartbeat_interval_ms": 100,
        "heartbeat_timeout_s": 0.5,
        "cooperative_shutdown_s": 0.2,
        "terminate_grace_s": 0.5,
        **overrides,
    }
    return DefaultGpuWorkerProcessFactory(**values)


async def test_authenticated_local_process_discovers_and_shuts_down() -> None:
    client = await asyncio.wait_for(_factory().start(mode="discovery"), timeout=2.0)
    temporary_root = client.temporary_directory_path

    event = await asyncio.wait_for(client.next_event(), timeout=1.0)
    devices = await asyncio.wait_for(client.discover(), timeout=1.0)

    assert event.name == "startup"
    assert len(devices) == 1
    assert devices[0].device_id == "vulkan:0"
    assert client.pid is not None
    assert temporary_root.is_dir()

    await asyncio.wait_for(client.close(), timeout=2.0)
    assert client.is_closed
    assert not temporary_root.exists()


async def test_cancel_response_preserves_decode_only_failure_fields(
    tmp_path: Path,
) -> None:
    client = await _factory().start(mode="persistent")
    await client.next_event()
    await client.activate(model_path=tmp_path / "model.gguf", device_id="vulkan:0")
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake")

    transcribe = asyncio.create_task(
        client.transcribe(
            request_id="decode-1",
            channel="peer",
            audio_path=audio_path,
        )
    )
    started = await client.next_event()
    assert started.name == "transcribe_started"
    assert started.fields["audio_seconds"] == 1.0
    await client.cancel("decode-1")

    with pytest.raises(GpuWorkerRequestError) as error:
        await transcribe
    assert error.value.code == "cancelled"
    assert error.value.attempt_started is True
    assert error.value.fields == {
        "audio_seconds": 1.0,
        "decode_seconds": 0.25,
        "rtf": 0.25,
    }
    await client.close()


async def test_prestart_invalid_audio_has_no_started_event_or_timing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_GPU_WORKER_PRESTART_AUDIO_INVALID", "1")
    client = await _factory().start(mode="persistent")
    await client.next_event()
    await client.activate(model_path=tmp_path / "model.gguf", device_id="vulkan:0")
    audio_path = tmp_path / "invalid.wav"
    audio_path.write_bytes(b"invalid")

    with pytest.raises(GpuWorkerRequestError) as error:
        await client.transcribe(
            request_id="invalid-audio",
            channel="self",
            audio_path=audio_path,
        )

    assert error.value.code == "audio_invalid"
    assert error.value.attempt_started is False
    assert error.value.fields == {"channel": "self", "backend": "Vulkan"}
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(client.next_event(), timeout=0.05)
    await client.close()


async def test_started_decode_failure_has_event_and_finite_exact_timing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_GPU_WORKER_STARTED_FAILURE", "1")
    client = await _factory().start(mode="persistent")
    await client.next_event()
    await client.activate(model_path=tmp_path / "model.gguf", device_id="vulkan:0")
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fixture")

    transcribe = asyncio.create_task(
        client.transcribe(
            request_id="decode-failure",
            channel="peer",
            audio_path=audio_path,
        )
    )
    started = await client.next_event()
    with pytest.raises(GpuWorkerRequestError) as error:
        await transcribe

    assert started.name == "transcribe_started"
    assert started.request_id == "decode-failure"
    assert started.fields["audio_seconds"] == 1.0
    assert error.value.attempt_started is True
    assert error.value.fields["audio_seconds"] == 1.0
    assert error.value.fields["decode_seconds"] == 0.25
    assert error.value.fields["rtf"] == (
        error.value.fields["decode_seconds"] / error.value.fields["audio_seconds"]
    )
    await client.close()


async def test_started_decode_success_has_event_and_finite_exact_timing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_GPU_WORKER_STARTED_SUCCESS", "1")
    client = await _factory().start(mode="persistent")
    await client.next_event()
    await client.activate(model_path=tmp_path / "model.gguf", device_id="vulkan:0")
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fixture")

    transcribe = asyncio.create_task(
        client.transcribe(
            request_id="decode-success",
            channel="self",
            audio_path=audio_path,
        )
    )
    started = await client.next_event()
    result = await transcribe

    assert started.name == "transcribe_started"
    assert started.request_id == "decode-success"
    assert started.fields["audio_seconds"] == 1.0
    assert result.audio_seconds == 1.0
    assert result.decode_seconds == 0.2
    assert result.rtf == result.decode_seconds / result.audio_seconds
    await client.close()


async def test_close_forces_process_termination_after_cooperative_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_GPU_WORKER_IGNORE_SHUTDOWN", "1")
    client = await _factory(cooperative_shutdown_s=0.05).start(mode="persistent")
    await client.next_event()

    await asyncio.wait_for(client.close(), timeout=2.0)

    assert client.returncode is not None


async def test_invalid_authentication_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_GPU_WORKER_BAD_AUTH", "1")

    with pytest.raises(GpuWorkerClosedError, match="authentication"):
        await _factory(startup_timeout_s=0.25).start(mode="discovery")
