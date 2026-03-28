from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from puripuly_heart.core.overlay.manifest import OverlayLaunchManifest
from puripuly_heart.core.overlay.process import (
    DefaultOverlayProcessRunner,
    OverlayManagedProcess,
    OverlayProcessManager,
)


@dataclass(slots=True)
class FakeOverlayManagedProcess(OverlayManagedProcess):
    ready_event_delay_ms: int | None = None
    startup_error: str | None = None
    exit_code: int | None = None
    exit_after_ready_code: int | None = None
    runtime_error_after_ready: str | None = None
    terminated: bool = False

    def __post_init__(self) -> None:
        self._events: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._exit_future: asyncio.Future[int | None] = asyncio.get_running_loop().create_future()
        self._schedule_transitions()

    async def next_event(self) -> dict[str, object]:
        return await self._events.get()

    async def wait(self) -> int | None:
        return await self._exit_future

    async def terminate(self) -> None:
        self.terminated = True
        if not self._exit_future.done():
            self._exit_future.set_result(0)

    def _schedule_transitions(self) -> None:
        async def runner() -> None:
            if self.startup_error is not None:
                await self._events.put(
                    {
                        "type": "startup_error",
                        "failure_reason": self.startup_error,
                    }
                )
                if self.exit_code is not None and not self._exit_future.done():
                    await asyncio.sleep(0)
                    self._exit_future.set_result(self.exit_code)
                return

            if self.ready_event_delay_ms is not None:
                await asyncio.sleep(self.ready_event_delay_ms / 1000.0)
                await self._events.put({"type": "overlay_ready"})
                if self.runtime_error_after_ready is not None:
                    await asyncio.sleep(0)
                    await self._events.put(
                        {
                            "type": "runtime_error",
                            "failure_reason": self.runtime_error_after_ready,
                        }
                    )
                    return
                if self.exit_after_ready_code is not None and not self._exit_future.done():
                    await asyncio.sleep(0)
                    self._exit_future.set_result(self.exit_after_ready_code)
                    return

            if self.exit_code is not None and not self._exit_future.done():
                await asyncio.sleep(0)
                self._exit_future.set_result(self.exit_code)

        asyncio.create_task(runner())


@dataclass(slots=True)
class FakeProcessRunner:
    ready_event_delay_ms: int | None = None
    startup_error: str | None = None
    exit_code: int | None = None
    exit_after_ready_code: int | None = None
    runtime_error_after_ready: str | None = None
    spawn_error: Exception | None = None
    manifest_error: Exception | None = None
    last_process: FakeOverlayManagedProcess | None = None

    def prepare(self, manifest: OverlayLaunchManifest) -> Path:
        _ = manifest
        if self.manifest_error is not None:
            raise self.manifest_error
        return Path("C:/fake/PuriPulyHeartOverlay.exe")

    async def spawn(
        self,
        executable_path: Path,
        manifest_path: Path,
    ) -> OverlayManagedProcess:
        _ = (executable_path, manifest_path)
        if self.spawn_error is not None:
            raise self.spawn_error
        self.last_process = FakeOverlayManagedProcess(
            ready_event_delay_ms=self.ready_event_delay_ms,
            startup_error=self.startup_error,
            exit_code=self.exit_code,
            exit_after_ready_code=self.exit_after_ready_code,
            runtime_error_after_ready=self.runtime_error_after_ready,
        )
        return self.last_process


@dataclass(slots=True)
class MissingExecutableRunner:
    def prepare(self, manifest: OverlayLaunchManifest) -> Path:
        _ = manifest
        raise FileNotFoundError("missing")

    async def spawn(
        self,
        executable_path: Path,
        manifest_path: Path,
    ) -> OverlayManagedProcess:
        _ = (executable_path, manifest_path)
        raise AssertionError("spawn should not be called")


@pytest.mark.asyncio
async def test_overlay_process_manager_waits_for_overlay_ready_before_connected() -> None:
    manager = OverlayProcessManager(process_runner=FakeProcessRunner(ready_event_delay_ms=50))

    try:
        await manager.start()

        assert manager.state == "connected"
        assert manager.failure_reason is None
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_overlay_process_manager_maps_missing_executable_to_failure_reason() -> None:
    manager = OverlayProcessManager(process_runner=MissingExecutableRunner())

    await manager.start()

    assert manager.state == "failed"
    assert manager.failure_reason == "missing_executable"


@pytest.mark.asyncio
async def test_overlay_process_manager_maps_spawn_failure_to_failure_reason() -> None:
    manager = OverlayProcessManager(process_runner=FakeProcessRunner(spawn_error=OSError("boom")))

    await manager.start()

    assert manager.state == "failed"
    assert manager.failure_reason == "spawn_failed"


@pytest.mark.asyncio
async def test_overlay_process_manager_maps_invalid_manifest_to_failure_reason() -> None:
    manager = OverlayProcessManager(
        process_runner=FakeProcessRunner(manifest_error=ValueError("bad manifest"))
    )

    await manager.start()

    assert manager.state == "failed"
    assert manager.failure_reason == "manifest_invalid"


@pytest.mark.asyncio
async def test_overlay_process_manager_prefers_explicit_startup_error_event_over_exit_code() -> (
    None
):
    manager = OverlayProcessManager(
        process_runner=FakeProcessRunner(startup_error="bridge_auth_failed", exit_code=21)
    )

    await manager.start()

    assert manager.state == "failed"
    assert manager.failure_reason == "bridge_auth_failed"


@pytest.mark.asyncio
async def test_overlay_process_manager_maps_pre_ready_exit_code_to_standard_failure_reason() -> (
    None
):
    manager = OverlayProcessManager(process_runner=FakeProcessRunner(exit_code=21))

    await manager.start()

    assert manager.state == "failed"
    assert manager.failure_reason == "renderer_init_failed"


@pytest.mark.asyncio
async def test_overlay_process_manager_maps_post_ready_exit_to_runtime_crashed_without_restart() -> (
    None
):
    manager = OverlayProcessManager(
        process_runner=FakeProcessRunner(ready_event_delay_ms=0, exit_after_ready_code=1)
    )

    await manager.start()

    assert manager.state == "failed"
    assert manager.failure_reason == "runtime_crashed"
    assert manager.restart_scheduled is False
    assert manager._manifest_path is None


@pytest.mark.asyncio
async def test_overlay_process_manager_terminates_child_on_startup_timeout() -> None:
    runner = FakeProcessRunner()
    manager = OverlayProcessManager(process_runner=runner, startup_timeout_ms=10)

    await manager.start()

    assert manager.state == "failed"
    assert manager.failure_reason == "startup_timeout"
    assert runner.last_process is not None
    assert runner.last_process.terminated is True


@pytest.mark.asyncio
async def test_overlay_process_manager_consumes_structured_stdout_events_from_default_runner(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "overlay_stub.py"
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import sys",
                "import time",
                "assert sys.argv[1] == '--config'",
                'print(\'{"type": "overlay_ready"}\', flush=True)',
                "time.sleep(5)",
            ]
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)

    manager = OverlayProcessManager(
        process_runner=DefaultOverlayProcessRunner(executable_path=script_path),
        startup_timeout_ms=500,
    )

    try:
        await manager.start()

        assert manager.state == "connected"
        assert manager.failure_reason is None
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_overlay_process_manager_logs_tagged_overlay_child_lines(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    script_path = tmp_path / "overlay_stub_logs.py"
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import sys",
                "import time",
                "assert sys.argv[1] == '--config'",
                'print("[overlay][INFO] child line", flush=True)',
                'print(\'{"type": "overlay_ready"}\', flush=True)',
                "time.sleep(5)",
            ]
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)

    manager = OverlayProcessManager(
        process_runner=DefaultOverlayProcessRunner(executable_path=script_path),
        startup_timeout_ms=500,
    )

    try:
        with caplog.at_level("INFO", logger="puripuly_heart.core.overlay.process"):
            await manager.start()

        assert manager.state == "connected"
        assert any("[overlay][INFO] child line" in message for message in caplog.messages)
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_overlay_process_manager_maps_post_ready_runtime_error_to_failure_reason() -> None:
    manager = OverlayProcessManager(
        process_runner=FakeProcessRunner(
            ready_event_delay_ms=0,
            runtime_error_after_ready="runtime_disconnected",
        )
    )

    await manager.start()
    await asyncio.sleep(0)

    assert manager.state == "failed"
    assert manager.failure_reason == "runtime_disconnected"
    assert manager._manifest_path is None


@pytest.mark.asyncio
async def test_overlay_process_manager_accepts_overlay_ready_from_bridge_messages() -> None:
    bridge_messages: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    manager = OverlayProcessManager(
        process_runner=FakeProcessRunner(),
        bridge_messages=bridge_messages,
        startup_timeout_ms=100,
    )

    async def publish_ready() -> None:
        await asyncio.sleep(0)
        await bridge_messages.put({"type": "overlay_ready"})

    publisher = asyncio.create_task(publish_ready())
    try:
        await manager.start()
        assert manager.state == "connected"
        assert manager.failure_reason is None
    finally:
        publisher.cancel()
        await asyncio.gather(publisher, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_overlay_process_manager_maps_bridge_runtime_error_after_ready() -> None:
    bridge_messages: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    manager = OverlayProcessManager(
        process_runner=FakeProcessRunner(ready_event_delay_ms=0),
        bridge_messages=bridge_messages,
    )

    await manager.start()
    await bridge_messages.put(
        {
            "type": "runtime_error",
            "failure_reason": "runtime_disconnected",
        }
    )

    for _ in range(10):
        if manager.state == "failed":
            break
        await asyncio.sleep(0)

    assert manager.state == "failed"
    assert manager.failure_reason == "runtime_disconnected"
