from __future__ import annotations

from dataclasses import dataclass

OVERLAY_CONTRACT_VERSION = 4
_MANIFEST_FIELDS = {
    "contract_version",
    "app_version",
    "overlay_instance_id",
    "bridge_url",
    "session_token",
    "parent_pid",
    "startup_deadline_ms",
    "log_dir",
    "log_level",
    "locale",
    "diagnostics_enabled",
}


@dataclass(frozen=True, slots=True)
class OverlayLaunchManifest:
    contract_version: int
    app_version: str
    overlay_instance_id: str
    bridge_url: str
    session_token: str
    parent_pid: int
    startup_deadline_ms: int
    log_dir: str
    log_level: str
    locale: str
    diagnostics_enabled: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "app_version": self.app_version,
            "overlay_instance_id": self.overlay_instance_id,
            "bridge_url": self.bridge_url,
            "session_token": self.session_token,
            "parent_pid": self.parent_pid,
            "startup_deadline_ms": self.startup_deadline_ms,
            "log_dir": self.log_dir,
            "log_level": self.log_level,
            "locale": self.locale,
            "diagnostics_enabled": self.diagnostics_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "OverlayLaunchManifest":
        extra_fields = set(data) - _MANIFEST_FIELDS
        if extra_fields:
            joined = ", ".join(sorted(extra_fields))
            raise ValueError(f"overlay manifest contains unsupported runtime fields: {joined}")

        missing_fields = [field for field in _MANIFEST_FIELDS if field not in data]
        if missing_fields:
            joined = ", ".join(sorted(missing_fields))
            raise ValueError(f"overlay manifest is missing required fields: {joined}")

        return cls(
            contract_version=int(data["contract_version"]),
            app_version=str(data["app_version"]),
            overlay_instance_id=str(data["overlay_instance_id"]),
            bridge_url=str(data["bridge_url"]),
            session_token=str(data["session_token"]),
            parent_pid=int(data["parent_pid"]),
            startup_deadline_ms=int(data["startup_deadline_ms"]),
            log_dir=str(data["log_dir"]),
            log_level=str(data["log_level"]),
            locale=str(data["locale"]),
            diagnostics_enabled=bool(data["diagnostics_enabled"]),
        )
