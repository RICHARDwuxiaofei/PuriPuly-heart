from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GpuDeviceOption:
    device_id: str
    display_name: str
    backend_name: str
