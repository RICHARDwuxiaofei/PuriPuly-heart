from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import time
import uuid

from puripuly_heart.config.paths import test_profile_name, user_config_dir
from puripuly_heart.core.qq_auth_diagnostics import safe_fields
from puripuly_heart.core.runtime_logging import emit_basic_log

_trace = contextvars.ContextVar("qq_auth_trace", default=None)
_STAGES = {
    "preflight",
    "operation_prepare",
    "assert",
    "credential_save",
    "delivery_ack",
    "settings_commit",
    "recovery",
    "provider_apply",
    "usage_refresh",
    "authentication",
}


def correlation_id() -> str | None:
    current = _trace.get()
    return current[0] if current else None


@contextlib.contextmanager
def auth_trace():
    if _trace.get() is not None:
        yield
        return
    token = _trace.set((uuid.uuid4().hex[:16], time.monotonic()))
    try:
        yield
    finally:
        _trace.reset(token)


def trace_event(
    stage: str,
    outcome: str,
    *,
    elapsed_ms: int = 0,
    fields: dict | None = None,
    code: str | None = None,
):
    current = _trace.get()
    if (
        current is None
        or stage not in _STAGES
        or outcome not in {"entered", "completed", "failed", "cancelled", "pending"}
    ):
        return
    record = dict(
        correlation=current[0],
        profile=test_profile_name() or "stable",
        stage=stage,
        outcome=outcome,
        elapsed_ms=max(0, elapsed_ms),
        since_start_ms=int((time.monotonic() - current[1]) * 1000),
        **safe_fields(fields or {}),
    )
    from puripuly_heart.core.qq_auth_diagnostics import CODES

    if code in CODES:
        record["code"] = code
    emit_basic_log(
        logging.getLogger(__name__),
        "[QQAuthTrace] " + json.dumps(record, ensure_ascii=True),
        level=logging.INFO,
    )
    if test_profile_name() == "qq-auth-beta":
        try:
            destination = user_config_dir() / "logs" / "qq-auth-stages.jsonl"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.stat().st_size > 2_000_000:
                destination.replace(destination.with_suffix(".previous.jsonl"))
            with destination.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=True) + "\n")
        except OSError:
            pass


@contextlib.contextmanager
def trace_stage(name: str):
    started = time.monotonic()
    trace_event(name, "entered")
    try:
        yield
    except BaseException as exc:
        from puripuly_heart.core.qq_auth_diagnostics import exception_fields

        trace_event(
            name,
            "cancelled" if type(exc).__name__ == "CancelledError" else "failed",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            fields=exception_fields(exc),
        )
        raise
    else:
        trace_event(name, "completed", elapsed_ms=int((time.monotonic() - started) * 1000))
