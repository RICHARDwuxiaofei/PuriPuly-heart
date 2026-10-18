from __future__ import annotations

import socket
from dataclasses import dataclass

import httpx

from puripuly_heart.core.network_diagnostics import (
    classify_transport_error,
    safe_transport_fields,
    transport_exception_chain,
)


@dataclass(frozen=True, slots=True)
class OpenRouterKeyMetadata:
    limit_usd: float | None
    remaining_usd: float | None
    usage_usd: float | None


class OpenRouterMetadataError(RuntimeError):
    def __init__(self, code: str, *, http_status: int | None = None, fields=None) -> None:
        super().__init__("Managed usage metadata query failed")
        self.code = code
        self.http_status = http_status
        self.fields = fields or {}

    @classmethod
    def from_exception(cls, exc: Exception) -> "OpenRouterMetadataError":
        if isinstance(exc, httpx.HTTPStatusError):
            return cls(
                "http_error",
                http_status=exc.response.status_code,
                fields={"error_type": "HTTPStatusError"},
            )
        kind = classify_transport_error(exc)
        if any(isinstance(item, socket.gaierror) for item in transport_exception_chain(exc)):
            kind = "dns"
        fields = {**safe_transport_fields(exc), "error_type": type(exc).__name__}
        return cls(kind or "unknown_error", fields=fields)


__all__ = ["OpenRouterKeyMetadata", "OpenRouterMetadataError"]
