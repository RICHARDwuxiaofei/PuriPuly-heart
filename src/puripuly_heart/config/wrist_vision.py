from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WristVisionIntent:
    enabled: bool = False
    hand: str = "left"
    width_m: float = 0.24
    opacity: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.08
    offset_z: float = 0.02
    self_display: str = "both"
    peer_display: str = "both"
    provider: str = "deepseek"
    model: str = "deepseek-flash"
    target_language: str = ""
    timeout: float = 45.0
    capture_mode: str = "wrist_frame"
    spatial_enabled: bool = False
    fallback_to_wrist: bool = True

    def __post_init__(self):
        for name in ("enabled", "spatial_enabled", "fallback_to_wrist"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("invalid_wrist_boolean")
        for name, low, high in (
            ("width_m", 0.12, 0.6),
            ("opacity", 0.4, 1.0),
            ("offset_x", -0.5, 0.5),
            ("offset_y", -0.5, 0.5),
            ("offset_z", -0.5, 0.5),
            ("timeout", 5, 120),
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError("invalid_wrist_range")
        if self.hand not in {"left", "right"} or self.provider != "deepseek":
            raise ValueError("invalid_wrist_provider_or_hand")
        if self.capture_mode not in {"wrist_frame", "head_frame"}:
            raise ValueError("invalid_capture_mode")
        if any(
            mode not in {"both", "original", "translation"}
            for mode in (self.self_display, self.peer_display)
        ):
            raise ValueError("invalid_wrist_display_mode")
        if self.model not in {"deepseek-flash", "deepseek-v4-flash-vision-exp"}:
            raise ValueError("unsupported_vision_model")
        if not isinstance(self.target_language, str) or len(self.target_language) > 64:
            raise ValueError("invalid_vision_language")
