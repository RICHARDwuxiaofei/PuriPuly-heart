from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GpuDeviceOption:
    device_id: str
    display_name: str
    backend_name: str
    device_type: str = "unknown"
    registry_index: int | None = None
    memory_total_bytes: int = 0
