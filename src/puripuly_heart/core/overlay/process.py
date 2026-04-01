from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol
from uuid import uuid4

from puripuly_heart import __version__

from .manifest import OVERLAY_CONTRACT_VERSION, OverlayLaunchManifest

logger = logging.getLogger(__name__)

OVERLAY_EXECUTABLE_NAME = "PuriPulyHeartOverlay.exe"
OPENVR_RUNTIME_DLL_NAME = "openvr_api.dll"
_STEAMVR_OPENVR_RUNTIME_DLL_RELATIVE_PATH = (
    Path("Steam") / "steamapps" / "common" / "SteamVR" / "bin" / "win64" / OPENVR_RUNTIME_DLL_NAME
)
_EXIT_CODE_TO_FAILURE_REASON = {
    10: "contract_mismatch",
    12: "bridge_auth_failed",
    20: "openvr_init_failed",
    21: "renderer_init_failed",
}


class OverlayPreparationError(Exception):
    def __init__(self, failure_reason: str, message: str | None = None) -> None:
        super().__init__(message or failure_reason)
        self.failure_reason = failure_reason


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
        self._start_reader(self.process.stdout, "stdout")
        self._start_reader(self.process.stderr, "stderr")

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

    def _start_reader(self, stream: asyncio.StreamReader | None, stream_name: str) -> None:
        if stream is None:
            return
        self._reader_tasks.append(asyncio.create_task(self._read_stream(stream, stream_name)))

    async def _read_stream(self, stream: asyncio.StreamReader, stream_name: str) -> None:
        try:
            while True:
                raw_line = await stream.readline()
                if not raw_line:
                    return
                line = raw_line.decode("utf-8", errors="replace").strip()
                event = self._parse_event_line(line)
                if event is not None:
                    await self._events.put(event)
                    continue
                self._log_passthrough_line(line, stream_name)
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

    def _log_passthrough_line(self, line: str, stream_name: str) -> None:
        if not line:
            return
        if stream_name == "stderr" or "[ERROR]" in line:
            logger.error(line)
            return
        if "[WARN]" in line:
            logger.warning(line)
            return
        logger.info(line)

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
        stale_source = self._newer_local_dev_overlay_source(path)
        if stale_source is not None:
            raise OverlayPreparationError(
                "stale_overlay_build",
                f"staged overlay executable is older than overlay source: {stale_source}",
            )
        if path.name == OVERLAY_EXECUTABLE_NAME:
            try:
                bundled_runtime_path = self.ensure_bundled_openvr_runtime_dll(path)
            except FileNotFoundError:
                logger.warning(
                    "[overlay] bundled OpenVR runtime DLL source not found; continuing without %s",
                    OPENVR_RUNTIME_DLL_NAME,
                )
            else:
                logger.info("[overlay] OpenVR runtime DLL ready at %s", bundled_runtime_path)
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

    @classmethod
    def default_executable_candidates(
        cls,
        *,
        sys_executable: Path | None = None,
        repo_root: Path | None = None,
    ) -> tuple[Path, Path]:
        executable = (sys_executable or Path(sys.executable)).resolve()
        root = repo_root or Path(__file__).resolve().parents[4]
        return executable.with_name(OVERLAY_EXECUTABLE_NAME), root / "build" / "overlay" / (
            OVERLAY_EXECUTABLE_NAME
        )

    @classmethod
    def resolve_default_executable(
        cls,
        *,
        sys_executable: Path | None = None,
        repo_root: Path | None = None,
    ) -> Path:
        packaged_sibling, staged = cls.default_executable_candidates(
            sys_executable=sys_executable,
            repo_root=repo_root,
        )
        if packaged_sibling.exists():
            return packaged_sibling
        if staged.exists():
            return staged
        return packaged_sibling

    def _resolve_default_executable(self) -> Path:
        return self.resolve_default_executable()

    @classmethod
    def _newer_local_dev_overlay_source(cls, executable_path: Path) -> Path | None:
        repo_root = cls._local_dev_repo_root_for_staged_executable(executable_path)
        if repo_root is None:
            return None

        executable_mtime = executable_path.stat().st_mtime
        for source_path in cls._local_dev_overlay_source_paths(repo_root):
            if source_path.stat().st_mtime > executable_mtime:
                return source_path
        return None

    @classmethod
    def _local_dev_repo_root_for_staged_executable(cls, executable_path: Path) -> Path | None:
        if executable_path.name != OVERLAY_EXECUTABLE_NAME:
            return None
        if executable_path.parent.name != "overlay":
            return None
        build_dir = executable_path.parent.parent
        if build_dir.name != "build":
            return None

        repo_root = build_dir.parent
        source_root = repo_root / "native" / "overlay" / "src"
        if not source_root.exists():
            return None
        return repo_root

    @classmethod
    def _local_dev_overlay_source_paths(cls, repo_root: Path) -> tuple[Path, ...]:
        overlay_root = repo_root / "native" / "overlay"
        source_paths: list[Path] = []
        for relative_path in ("Cargo.toml", "Cargo.lock", "build.rs"):
            candidate = overlay_root / relative_path
            if candidate.exists():
                source_paths.append(candidate)

        source_root = overlay_root / "src"
        if source_root.exists():
            source_paths.extend(
                sorted(path for path in source_root.rglob("*.rs") if path.is_file())
            )
        return tuple(source_paths)

    @classmethod
    def bundled_openvr_runtime_dll_path(cls, executable_path: Path) -> Path:
        return executable_path.with_name(OPENVR_RUNTIME_DLL_NAME)

    @classmethod
    def default_openvr_runtime_dll_candidates(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> tuple[Path, ...]:
        env = environ or os.environ
        candidate_roots: list[Path] = []
        for key in (
            "ProgramFiles(x86)",
            "PROGRAMFILES(X86)",
            "ProgramW6432",
            "ProgramFiles",
            "PROGRAMFILES",
        ):
            raw_value = env.get(key)
            if not raw_value:
                continue
            root = Path(raw_value)
            if root not in candidate_roots:
                candidate_roots.append(root)
        return tuple(root / _STEAMVR_OPENVR_RUNTIME_DLL_RELATIVE_PATH for root in candidate_roots)

    @classmethod
    def ensure_bundled_openvr_runtime_dll(
        cls,
        executable_path: Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> Path:
        bundled_path = cls.bundled_openvr_runtime_dll_path(executable_path)
        if bundled_path.exists():
            return bundled_path

        for candidate in cls.default_openvr_runtime_dll_candidates(environ=environ):
            if not candidate.exists():
                continue
            bundled_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, bundled_path)
            return bundled_path

        raise FileNotFoundError(
            "OpenVR runtime DLL not found in default SteamVR locations: "
            f"{_STEAMVR_OPENVR_RUNTIME_DLL_RELATIVE_PATH}"
        )


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
        except OverlayPreparationError as error:
            await self._fail(error.failure_reason)
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
