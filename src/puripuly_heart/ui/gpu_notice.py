from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GpuNoticeAction = Literal["install", "repair", "reinstall", "rediscover", "restart"]


@dataclass(frozen=True, slots=True)
class GpuDashboardNotice:
    status: str
    progress_percent: int | None = None
    action: GpuNoticeAction | None = None
