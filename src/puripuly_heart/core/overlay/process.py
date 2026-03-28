from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from puripuly_heart import __version__

from .manifest import OVERLAY_CONTRACT_VERSION, OverlayLaunchManifest

OVERLAY_EXECUTABLE_NAME = "PuriPulyHeartOverlay.exe"
_EXIT_CODE_TO_FAILURE_REASON = {
    10: "contract_mismatch",
    12: "bridge_auth_failed",
    20: "openvr_init_failed",
    21: "renderer_init_failed",
}


class OverlayManagedProcess(Protocol):
    async def next_event(self) -> dict[str, object]: ...
    async def wait(self) -> int | None: ...
    async def terminate(self) -> None: ...


class OverlayProcessRunner(Protocol):
    def prepare(self, manifest: OverlayLaunchManifest) -> Path: ...
    async def spawn(
        self,
        executable_path: Path,
        manifest_path: Path,
    ) -> OverlayManagedProcess: ...


@dataclass(slots=True)
class _AsyncioOverlayProcess:
    process: asyncio.subprocess.Process
    _events: asyncio.Queue[dict[str, object]] = field(default_factory=asyncio.Queue)
    _reader_tasks: list[asyncio.Task[None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._start_reader(self.process.stdout)
        self._start_reader(self.process.stderr)

    async def next_event(self) -> dict[str, object]:
        return await self._events.get()

    async def wait(self) -> int | None:
        exit_code = await self.process.wait()
        await self._finish_readers()
        return exit_code

    async def terminate(self) -> None:
        if self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
        await self.wait()

    def _start_reader(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        self._reader_tasks.append(asyncio.create_task(self._read_stream(stream)))

    async def _read_stream(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                raw_line = await stream.readline()
                if not raw_line:
                    return
                event = self._parse_event_line(raw_line.decode("utf-8", errors="replace").strip())
                if event is not None:
                    await self._events.put(event)
        except asyncio.CancelledError:
            raise

    def _parse_event_line(self, line: str) -> dict[str, object] | None:
        if not line:
            return None

        candidates = [line]
        if line.startswith("EVENT "):
            candidates.insert(0, line[len("EVENT ") :].strip())

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("type"), str):
                return payload
        return None

    async def _finish_readers(self) -> None:
        tasks = self._reader_tasks
        self._reader_tasks = []
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(slots=True)
class DefaultOverlayProcessRunner:
    executable_path: Path | None = None

    def prepare(self, manifest: OverlayLaunchManifest) -> Path:
        _ = manifest
        if self.executable_path is not None:
            path = self.executable_path
        else:
            path = self._resolve_default_executable()
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    async def spawn(
        self,
        executable_path: Path,
        manifest_path: Path,
    ) -> OverlayManagedProcess:
        process = await asyncio.create_subprocess_exec(
            str(executable_path),
            "--config",
            str(manifest_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return _AsyncioOverlayProcess(process=process)

    def _resolve_default_executable(self) -> Path:
        packaged_sibling = Path(sys.executable).resolve().with_name(OVERLAY_EXECUTABLE_NAME)
        if packaged_sibling.exists():
            return packaged_sibling

        repo_root = Path(__file__).resolve().parents[4]
        staged = repo_root / "build" / "overlay" / OVERLAY_EXECUTABLE_NAME
        if staged.exists():
            return staged
        return packaged_sibling


@dataclass(slots=True)
class OverlayProcessManager:
    process_runner: OverlayProcessRunner = field(default_factory=DefaultOverlayProcessRunner)
    startup_timeout_ms: int = 3000
    bridge_url: str = "ws://127.0.0.1:0"
    bridge_messages: asyncio.Queue[dict[str, object]] | None = None
    session_token: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    locale: str = "en"
    log_dir: str = "logs"
    log_level: str = "INFO"
    diagnostics_enabled: bool = False

    state: str = field(init=False, default="off")
    failure_reason: str | None = field(init=False, default=None)
    restart_scheduled: bool = field(init=False, default=False)
    overlay_instance_id: str = field(init=False, default_factory=lambda: f"overlay-{uuid4()}")
    _manifest_path: Path | None = field(init=False, default=None)
    _process: OverlayManagedProcess | None = field(init=False, default=None)
    _monitor_task: asyncio.Task[None] | None = field(init=False, default=None)

    async def start(self) -> None:
        if self.state in {"starting", "connected"}:
            return

        self.state = "starting"
        self.restart_scheduled = False

        manifest = self._build_manifest()
        try:
            executable_path = self.process_runner.prepare(manifest)
            self._manifest_path = self._write_manifest(manifest)
            self._process = await self.process_runner.spawn(executable_path, self._manifest_path)
            await self._wait_for_startup()
        except FileNotFoundError:
            await self._fail("missing_executable")
        except ValueError:
            await self._fail("manifest_invalid")
        except OSError:
            await self._fail("spawn_failed")

    async def stop(self) -> None:
        self.state = "stopping"

        monitor_task = self._monitor_task
        self._monitor_task = None
        if monitor_task is not None:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)

        process = self._process
        self._process = None
        if process is not None:
            await process.terminate()

        self._cleanup_manifest()
        self.state = "off"

    def _build_manifest(self) -> OverlayLaunchManifest:
        return OverlayLaunchManifest(
            contract_version=OVERLAY_CONTRACT_VERSION,
            app_version=__version__,
            overlay_instance_id=self.overlay_instance_id,
            bridge_url=self.bridge_url,
            session_token=self.session_token,
            parent_pid=os.getpid(),
            startup_deadline_ms=self.startup_timeout_ms,
            log_dir=self.log_dir,
            log_level=self.log_level,
            locale=self.locale,
            diagnostics_enabled=self.diagnostics_enabled,
        )

    def _write_manifest(self, manifest: OverlayLaunchManifest) -> Path:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".json",
            prefix="puripuly-overlay-",
            delete=False,
        ) as handle:
            json.dump(manifest.to_dict(), handle)
        return Path(handle.name)

    async def _wait_for_startup(self) -> None:
        if self._process is None:
            await self._fail("unknown")
            return

        event_task = asyncio.create_task(self._process.next_event())
        bridge_task = self._create_bridge_event_task()
        exit_task = asyncio.create_task(self._process.wait())
        timeout_task = asyncio.create_task(asyncio.sleep(self.startup_timeout_ms / 1000.0))

        try:
            while True:
                pending_tasks: set[asyncio.Task[object]] = {exit_task, timeout_task}
                pending_tasks.add(event_task)
                if bridge_task is not None:
                    pending_tasks.add(bridge_task)
                done, _pending = await asyncio.wait(
                    pending_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if event_task in done:
                    outcome = await self._handle_lifecycle_event(
                        event_task.result(), allow_ready=True
                    )
                    if outcome == "ready":
                        handoff_exit_task = exit_task
                        exit_task = None
                        self._monitor_task = asyncio.create_task(
                            self._monitor_connected_process(exit_task=handoff_exit_task)
                        )
                        await asyncio.sleep(0)
                        return
                    if outcome == "failed":
                        return
                    event_task = asyncio.create_task(self._process.next_event())

                if bridge_task is not None and bridge_task in done:
                    outcome = await self._handle_lifecycle_event(
                        bridge_task.result(),
                        allow_ready=True,
                    )
                    if outcome == "ready":
                        handoff_exit_task = exit_task
                        exit_task = None
                        self._monitor_task = asyncio.create_task(
                            self._monitor_connected_process(exit_task=handoff_exit_task)
                        )
                        await asyncio.sleep(0)
                        return
                    if outcome == "failed":
                        return
                    bridge_task = self._create_bridge_event_task()

                if exit_task in done:
                    await self._fail(self._map_exit_code_to_failure_reason(exit_task.result()))
                    return

                if timeout_task in done:
                    await self._fail("startup_timeout")
                    return
        finally:
            for task in (event_task, bridge_task, exit_task, timeout_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *[
                    task
                    for task in (event_task, bridge_task, exit_task, timeout_task)
                    if task is not None
                ],
                return_exceptions=True,
            )

    async def _monitor_connected_process(
        self,
        exit_task: asyncio.Task[int | None] | None = None,
    ) -> None:
        process = self._process
        if process is None:
            return
        event_task = asyncio.create_task(process.next_event())
        bridge_task = self._create_bridge_event_task()
        if exit_task is None:
            exit_task = asyncio.create_task(process.wait())
        try:
            while True:
                pending_tasks: set[asyncio.Task[object]] = {exit_task}
                pending_tasks.add(event_task)
                if bridge_task is not None:
                    pending_tasks.add(bridge_task)
                done, _pending = await asyncio.wait(
                    pending_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if event_task in done:
                    if (
                        await self._handle_lifecycle_event(event_task.result(), allow_ready=False)
                        == "failed"
                    ):
                        return
                    event_task = asyncio.create_task(process.next_event())

                if bridge_task is not None and bridge_task in done:
                    if (
                        await self._handle_lifecycle_event(
                            bridge_task.result(),
                            allow_ready=False,
                        )
                        == "failed"
                    ):
                        return
                    bridge_task = self._create_bridge_event_task()

                if exit_task in done:
                    exit_code = exit_task.result()
                    if self.state == "connected" and exit_code is not None:
                        await self._fail("runtime_crashed", terminate_process=False)
                    return
        finally:
            for task in (event_task, bridge_task, exit_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *[task for task in (event_task, bridge_task, exit_task) if task is not None],
                return_exceptions=True,
            )

    def _create_bridge_event_task(self) -> asyncio.Task[dict[str, object]] | None:
        if self.bridge_messages is None:
            return None
        return asyncio.create_task(self.bridge_messages.get())

    async def _handle_lifecycle_event(
        self,
        event: dict[str, object],
        *,
        allow_ready: bool,
    ) -> str:
        event_type = str(event.get("type", ""))
        if allow_ready and event_type == "overlay_ready":
            self.state = "connected"
            self.failure_reason = None
            return "ready"
        if event_type in {"startup_error", "runtime_error"}:
            await self._fail(self._extract_failure_reason(event))
            return "failed"
        return "ignored"

    def _extract_failure_reason(self, event: dict[str, object]) -> str:
        failure_reason = event.get("failure_reason")
        if isinstance(failure_reason, str) and failure_reason:
            return failure_reason
        return "unknown"

    def _map_exit_code_to_failure_reason(self, exit_code: int | None) -> str:
        if exit_code is None:
            return "unknown"
        return _EXIT_CODE_TO_FAILURE_REASON.get(exit_code, "unknown")

    async def _fail(
        self,
        failure_reason: str,
        *,
        cleanup_manifest: bool = True,
        terminate_process: bool = True,
    ) -> None:
        self.state = "failed"
        self.failure_reason = failure_reason
        self.restart_scheduled = False

        process = self._process
        self._process = None
        if terminate_process and process is not None:
            await process.terminate()

        if cleanup_manifest:
            self._cleanup_manifest()

    def _cleanup_manifest(self) -> None:
        manifest_path = self._manifest_path
        self._manifest_path = None
        if manifest_path is None:
            return
        with contextlib.suppress(FileNotFoundError):
            manifest_path.unlink()
