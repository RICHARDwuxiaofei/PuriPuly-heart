from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path

import pytest

SRC_PATH = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_PATH))


def _is_puripuly_gui_command(command: object) -> bool:
    if isinstance(command, (str, bytes)):
        return False
    arguments = [str(argument).replace("\\", "/").lower() for argument in command]
    return any(
        argument == "-m"
        and index + 1 < len(arguments)
        and arguments[index + 1] == "puripuly_heart.main"
        and "run-gui" in arguments[index + 2 :]
        for index, argument in enumerate(arguments)
    )


def _terminate_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    import psutil

    active = []
    for launched in processes:
        try:
            process = psutil.Process(launched.pid)
            active.extend(process.children(recursive=True))
            active.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    for process in reversed(active):
        with contextlib.suppress(psutil.AccessDenied, psutil.NoSuchProcess):
            process.terminate()
    _, alive = psutil.wait_procs(active, timeout=3)
    for process in alive:
        with contextlib.suppress(psutil.AccessDenied, psutil.NoSuchProcess):
            process.kill()
    psutil.wait_procs(alive, timeout=3)


@pytest.fixture(autouse=True)
def reject_new_puripuly_gui_processes(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
):
    original_popen = subprocess.Popen
    launched: list[subprocess.Popen[bytes]] = []

    def tracking_popen(*args: object, **kwargs: object):
        process = original_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args")
        if _is_puripuly_gui_command(command):
            launched.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", tracking_popen)
    yield
    leaked = [process for process in launched if process.poll() is None]
    if not leaked:
        return
    _terminate_processes(leaked)
    pytest.fail(
        f"{request.node.nodeid} leaked PuriPuly GUI process(es): "
        + "; ".join(f"pid={process.pid} command={process.args}" for process in leaked)
    )
