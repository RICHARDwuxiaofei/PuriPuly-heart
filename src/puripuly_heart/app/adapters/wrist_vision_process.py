from __future__ import annotations

import asyncio
import os
import subprocess

from puripuly_heart.core.overlay.openvr_vendor import validate_openvr_runtime_dll
from puripuly_heart.runtime_layout import current_runtime_layout


async def spawn_wrist_vision(*, mock=False, executable=None):
    executable = executable or current_runtime_layout().native(
        "wrist_vision", "PuriPulyHeartWristVision.exe"
    )
    if not executable.is_file():
        raise FileNotFoundError("wrist_component_missing")
    validate_openvr_runtime_dll(executable.with_name("openvr_api.dll"))
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(
            part in key.upper()
            for part in ("API_KEY", "TOKEN", "PSK", "PASSWORD", "SECRET", "PROXY")
        )
    }
    return await asyncio.create_subprocess_exec(
        str(executable),
        *(["--mock"] if mock else []),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=8 * 1024 * 1024,
        cwd=executable.parent,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
