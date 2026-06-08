from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, TypeAlias

from puripuly_heart.core.messages import (
    CONTENT_POLICY_REDACTED,
    DIAGNOSTIC_FIELD_KEY_MAX_LENGTH,
    DIAGNOSTIC_FIELD_MAX_ITEMS,
    DIAGNOSTIC_FIELD_VALUE_MAX_LENGTH,
    DIAGNOSTIC_VISIBILITY_BASIC,
    DIAGNOSTIC_VISIBILITY_DETAILED,
    DIAGNOSTIC_VISIBILITY_DIAGNOSTIC_ONLY,
    DIAGNOSTIC_VISIBILITY_PERSISTED_FAILURE_ONLY,
    ContentPolicy,
    DiagnosticVisibility,
    ErrorDiagnostics,
)

DiagnosticSink: TypeAlias = Literal[
    "dashboard",
    "snackbar",
    "chatbox_disclosure",
    "basic_logs",
    "detailed_logs",
    "persisted_logs",
    "failure_jsonl",
]
DIAGNOSTIC_SINK_DASHBOARD: Final[DiagnosticSink] = "dashboard"
DIAGNOSTIC_SINK_SNACKBAR: Final[DiagnosticSink] = "snackbar"
DIAGNOSTIC_SINK_CHATBOX_DISCLOSURE: Final[DiagnosticSink] = "chatbox_disclosure"
DIAGNOSTIC_SINK_BASIC_LOGS: Final[DiagnosticSink] = "basic_logs"
DIAGNOSTIC_SINK_DETAILED_LOGS: Final[DiagnosticSink] = "detailed_logs"
DIAGNOSTIC_SINK_PERSISTED_LOGS: Final[DiagnosticSink] = "persisted_logs"
DIAGNOSTIC_SINK_FAILURE_JSONL: Final[DiagnosticSink] = "failure_jsonl"
DIAGNOSTIC_SINKS: Final[tuple[DiagnosticSink, ...]] = (
    DIAGNOSTIC_SINK_DASHBOARD,
    DIAGNOSTIC_SINK_SNACKBAR,
    DIAGNOSTIC_SINK_CHATBOX_DISCLOSURE,
    DIAGNOSTIC_SINK_BASIC_LOGS,
    DIAGNOSTIC_SINK_DETAILED_LOGS,
    DIAGNOSTIC_SINK_PERSISTED_LOGS,
    DIAGNOSTIC_SINK_FAILURE_JSONL,
)

DIAGNOSTIC_SINK_VISIBILITY_RULES: Final[
    Mapping[DiagnosticSink, frozenset[DiagnosticVisibility]]
] = MappingProxyType(
    {
        DIAGNOSTIC_SINK_DASHBOARD: frozenset({DIAGNOSTIC_VISIBILITY_BASIC}),
        DIAGNOSTIC_SINK_SNACKBAR: frozenset({DIAGNOSTIC_VISIBILITY_BASIC}),
        DIAGNOSTIC_SINK_CHATBOX_DISCLOSURE: frozenset({DIAGNOSTIC_VISIBILITY_BASIC}),
        DIAGNOSTIC_SINK_BASIC_LOGS: frozenset({DIAGNOSTIC_VISIBILITY_BASIC}),
        DIAGNOSTIC_SINK_DETAILED_LOGS: frozenset(
            {
                DIAGNOSTIC_VISIBILITY_BASIC,
                DIAGNOSTIC_VISIBILITY_DETAILED,
                DIAGNOSTIC_VISIBILITY_DIAGNOSTIC_ONLY,
            }
        ),
        DIAGNOSTIC_SINK_PERSISTED_LOGS: frozenset(
            {
                DIAGNOSTIC_VISIBILITY_BASIC,
                DIAGNOSTIC_VISIBILITY_DETAILED,
                DIAGNOSTIC_VISIBILITY_DIAGNOSTIC_ONLY,
            }
        ),
        DIAGNOSTIC_SINK_FAILURE_JSONL: frozenset({DIAGNOSTIC_VISIBILITY_PERSISTED_FAILURE_ONLY}),
    }
)

DiagnosticValidationStatus: TypeAlias = Literal["accepted", "rejected"]
DIAGNOSTIC_VALIDATION_STATUS_ACCEPTED: Final[DiagnosticValidationStatus] = "accepted"
DIAGNOSTIC_VALIDATION_STATUS_REJECTED: Final[DiagnosticValidationStatus] = "rejected"

DiagnosticValidationReason: TypeAlias = Literal[
    "visibility_forbidden",
    "unsupported_field_type",
    "field_limit_exceeded",
    "excessive_depth",
    "secret_pattern",
    "broker_raw_message",
    "provider_response_body",
    "sensitive_local_llm_extra_body",
    "unsafe_text_payload",
]
DIAGNOSTIC_VALIDATION_REASON_VISIBILITY_FORBIDDEN: Final[DiagnosticValidationReason] = (
    "visibility_forbidden"
)
DIAGNOSTIC_VALIDATION_REASON_UNSUPPORTED_FIELD_TYPE: Final[DiagnosticValidationReason] = (
    "unsupported_field_type"
)
DIAGNOSTIC_VALIDATION_REASON_FIELD_LIMIT_EXCEEDED: Final[DiagnosticValidationReason] = (
    "field_limit_exceeded"
)
DIAGNOSTIC_VALIDATION_REASON_EXCESSIVE_DEPTH: Final[DiagnosticValidationReason] = "excessive_depth"
DIAGNOSTIC_VALIDATION_REASON_SECRET_PATTERN: Final[DiagnosticValidationReason] = "secret_pattern"
DIAGNOSTIC_VALIDATION_REASON_BROKER_RAW_MESSAGE: Final[DiagnosticValidationReason] = (
    "broker_raw_message"
)
DIAGNOSTIC_VALIDATION_REASON_PROVIDER_RESPONSE_BODY: Final[DiagnosticValidationReason] = (
    "provider_response_body"
)
DIAGNOSTIC_VALIDATION_REASON_SENSITIVE_LOCAL_LLM_EXTRA_BODY: Final[DiagnosticValidationReason] = (
    "sensitive_local_llm_extra_body"
)
DIAGNOSTIC_VALIDATION_REASON_UNSAFE_TEXT_PAYLOAD: Final[DiagnosticValidationReason] = (
    "unsafe_text_payload"
)

DIAGNOSTIC_FIELD_MAX_DEPTH: Final = 3
DIAGNOSTIC_REDACTION_MARKER: Final = "[redacted]"
BROKER_RAW_MESSAGE_REDACTION_MARKER: Final = "[broker-raw-message-redacted]"
PROVIDER_RESPONSE_BODY_REDACTION_MARKER: Final = "[provider-response-body-redacted]"
LOCAL_LLM_EXTRA_BODY_REDACTION_MARKER: Final = "[local-llm-extra-body-redacted]"

_SAFE_REDACTION_MARKERS: Final = frozenset(
    {
        BROKER_RAW_MESSAGE_REDACTION_MARKER,
        DIAGNOSTIC_REDACTION_MARKER,
        PROVIDER_RESPONSE_BODY_REDACTION_MARKER,
        LOCAL_LLM_EXTRA_BODY_REDACTION_MARKER,
    }
)
_SENSITIVE_KEY_FRAGMENTS: Final = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "managed_private_key",
        "password",
        "private_key",
        "secret",
        "session_token",
        "token",
    }
)
_SENSITIVE_TOKEN_KEYS: Final = frozenset(
    {
        "access_token",
        "auth_token",
        "bearer_token",
        "id_token",
        "refresh_token",
        "session_token",
        "token",
    }
)
_LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS: Final = frozenset(
    {"api_key", "authorization", "headers", "password", "secret", "token"}
)
_BROKER_RAW_MESSAGE_KEYS: Final = frozenset(
    {
        "broker_eligibility_message",
        "broker_raw_eligibility_message",
        "broker_raw_message",
        "broker_sensitive_message",
        "raw_broker_eligibility_message",
        "raw_broker_message",
    }
)
_PROVIDER_RESPONSE_BODY_KEYS: Final = frozenset(
    {
        "provider_payload",
        "provider_response",
        "provider_response_body",
        "raw_body",
        "raw_payload",
        "raw_provider_payload",
        "raw_provider_response",
        "raw_response",
        "raw_response_body",
        "response_body",
        "response_text",
    }
)
_UNSAFE_TEXT_KEYS: Final = frozenset(
    {
        "exception_text",
        "file_content",
        "file_contents",
        "raw_exception",
        "raw_file",
        "stack_trace",
        "traceback",
    }
)
_SECRET_VALUE_PATTERNS: Final = (
    re.compile(r"(?i)\bmanaged[\s_-]?private[\s_-]?key\b\s*[\"']?\s*[:=]"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|authorization|password|private[_-]?key|secret|session[_-]?token|token)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;}]+"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9._-]{8,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_STACK_TRACE_PATTERNS: Final = (
    re.compile(r"Traceback \(most recent call last\):"),
    re.compile(r'File "[^"]+", line \d+'),
)


@dataclass(frozen=True, slots=True)
class DiagnosticRedactionPolicy:
    allow_sensitive_local_llm_extra_body_fields: bool = False


DEFAULT_DIAGNOSTIC_REDACTION_POLICY: Final = DiagnosticRedactionPolicy()


@dataclass(frozen=True, slots=True)
class DiagnosticValidationResult:
    status: DiagnosticValidationStatus
    sink: DiagnosticSink
    diagnostics: ErrorDiagnostics | None
    redacted: bool
    reasons: tuple[DiagnosticValidationReason, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))


def validate_diagnostics_for_sink(
    diagnostics: ErrorDiagnostics,
    sink: DiagnosticSink,
    *,
    policy: DiagnosticRedactionPolicy = DEFAULT_DIAGNOSTIC_REDACTION_POLICY,
) -> DiagnosticValidationResult:
    reasons = _validation_reasons(diagnostics, sink, policy=policy)
    if reasons:
        return DiagnosticValidationResult(
            status=DIAGNOSTIC_VALIDATION_STATUS_REJECTED,
            sink=sink,
            diagnostics=None,
            redacted=False,
            reasons=reasons,
        )
    return DiagnosticValidationResult(
        status=DIAGNOSTIC_VALIDATION_STATUS_ACCEPTED,
        sink=sink,
        diagnostics=diagnostics,
        redacted=False,
        reasons=(),
    )


def redact_diagnostics_for_sink(
    diagnostics: ErrorDiagnostics,
    sink: DiagnosticSink,
    *,
    policy: DiagnosticRedactionPolicy = DEFAULT_DIAGNOSTIC_REDACTION_POLICY,
) -> DiagnosticValidationResult:
    fields, redacted = _redact_fields(diagnostics, policy=policy)
    redacted_diagnostics = _copy_diagnostics_with_fields(diagnostics, fields)
    reasons = _validation_reasons(redacted_diagnostics, sink, policy=policy)
    if reasons:
        return DiagnosticValidationResult(
            status=DIAGNOSTIC_VALIDATION_STATUS_REJECTED,
            sink=sink,
            diagnostics=None,
            redacted=redacted,
            reasons=reasons,
        )
    return DiagnosticValidationResult(
        status=DIAGNOSTIC_VALIDATION_STATUS_ACCEPTED,
        sink=sink,
        diagnostics=redacted_diagnostics,
        redacted=redacted,
        reasons=(),
    )


def _validation_reasons(
    diagnostics: ErrorDiagnostics,
    sink: DiagnosticSink,
    *,
    policy: DiagnosticRedactionPolicy,
) -> tuple[DiagnosticValidationReason, ...]:
    reasons: list[DiagnosticValidationReason] = []
    allowed_visibility = DIAGNOSTIC_SINK_VISIBILITY_RULES.get(sink)
    if allowed_visibility is None or diagnostics.visibility not in allowed_visibility:
        reasons.append(DIAGNOSTIC_VALIDATION_REASON_VISIBILITY_FORBIDDEN)

    reasons.extend(_field_shape_reasons(diagnostics.fields))
    reasons.extend(_content_reasons(diagnostics.fields, policy=policy))
    return tuple(dict.fromkeys(reasons))


def _field_shape_reasons(
    fields: Mapping[str, object],
) -> tuple[DiagnosticValidationReason, ...]:
    reasons: list[DiagnosticValidationReason] = []
    if len(fields) > DIAGNOSTIC_FIELD_MAX_ITEMS:
        reasons.append(DIAGNOSTIC_VALIDATION_REASON_FIELD_LIMIT_EXCEEDED)

    for key, value in fields.items():
        if not isinstance(key, str):
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_UNSUPPORTED_FIELD_TYPE)
            continue
        if len(key) > DIAGNOSTIC_FIELD_KEY_MAX_LENGTH:
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_FIELD_LIMIT_EXCEEDED)
        if _max_depth(value) > DIAGNOSTIC_FIELD_MAX_DEPTH:
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_EXCESSIVE_DEPTH)
        if not _is_supported_diagnostic_value(value):
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_UNSUPPORTED_FIELD_TYPE)
            continue
        if isinstance(value, str) and len(value) > DIAGNOSTIC_FIELD_VALUE_MAX_LENGTH:
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_FIELD_LIMIT_EXCEEDED)
        if isinstance(value, float) and not math.isfinite(value):
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_UNSUPPORTED_FIELD_TYPE)

    return tuple(dict.fromkeys(reasons))


def _content_reasons(
    fields: Mapping[str, object],
    *,
    policy: DiagnosticRedactionPolicy,
) -> tuple[DiagnosticValidationReason, ...]:
    reasons: list[DiagnosticValidationReason] = []
    for key, value in fields.items():
        key_text = str(key)
        if _contains_unredacted_provider_response_body(key_text, value):
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_PROVIDER_RESPONSE_BODY)
        if _contains_unredacted_broker_raw_message(key_text, value):
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_BROKER_RAW_MESSAGE)
        if _contains_unredacted_sensitive_local_llm_extra_body(key_text, value):
            if not policy.allow_sensitive_local_llm_extra_body_fields:
                reasons.append(DIAGNOSTIC_VALIDATION_REASON_SENSITIVE_LOCAL_LLM_EXTRA_BODY)
            elif value != LOCAL_LLM_EXTRA_BODY_REDACTION_MARKER:
                reasons.append(DIAGNOSTIC_VALIDATION_REASON_SENSITIVE_LOCAL_LLM_EXTRA_BODY)
        if _contains_unredacted_secret_pattern(key_text, value):
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_SECRET_PATTERN)
        if _contains_unredacted_unsafe_text_payload(key_text, value):
            reasons.append(DIAGNOSTIC_VALIDATION_REASON_UNSAFE_TEXT_PAYLOAD)
    return tuple(dict.fromkeys(reasons))


def _redact_fields(
    diagnostics: ErrorDiagnostics,
    *,
    policy: DiagnosticRedactionPolicy,
) -> tuple[Mapping[str, object], bool]:
    redacted = False
    fields: dict[str, object] = {}
    for key, value in diagnostics.fields.items():
        key_text = str(key)
        if _contains_unredacted_provider_response_body(key_text, value):
            if diagnostics.content_policy == CONTENT_POLICY_REDACTED:
                fields[key] = PROVIDER_RESPONSE_BODY_REDACTION_MARKER
                redacted = True
            else:
                fields[key] = value
            continue

        if _contains_unredacted_broker_raw_message(key_text, value):
            if diagnostics.content_policy == CONTENT_POLICY_REDACTED:
                fields[key] = BROKER_RAW_MESSAGE_REDACTION_MARKER
                redacted = True
            else:
                fields[key] = value
            continue

        if _contains_unredacted_sensitive_local_llm_extra_body(key_text, value):
            if (
                diagnostics.content_policy == CONTENT_POLICY_REDACTED
                and policy.allow_sensitive_local_llm_extra_body_fields
            ):
                fields[key] = LOCAL_LLM_EXTRA_BODY_REDACTION_MARKER
                redacted = True
            else:
                fields[key] = value
            continue

        if _contains_unredacted_secret_pattern(key_text, value):
            if diagnostics.content_policy == CONTENT_POLICY_REDACTED:
                fields[key] = DIAGNOSTIC_REDACTION_MARKER
                redacted = True
            else:
                fields[key] = value
            continue

        if _contains_unredacted_unsafe_text_payload(key_text, value):
            if diagnostics.content_policy == CONTENT_POLICY_REDACTED:
                fields[key] = DIAGNOSTIC_REDACTION_MARKER
                redacted = True
            else:
                fields[key] = value
            continue

        fields[key] = value
    return MappingProxyType(fields), redacted


def _copy_diagnostics_with_fields(
    diagnostics: ErrorDiagnostics,
    fields: Mapping[str, object],
) -> ErrorDiagnostics:
    return ErrorDiagnostics(
        component=diagnostics.component,
        operation=diagnostics.operation,
        code=diagnostics.code,
        category=diagnostics.category,
        visibility=diagnostics.visibility,
        content_policy=diagnostics.content_policy,
        status_code=diagnostics.status_code,
        retry_after_ms=diagnostics.retry_after_ms,
        fields=fields,
    )


def _is_supported_diagnostic_value(value: object) -> bool:
    return isinstance(value, str | int | float | bool) or value is None


def _max_depth(value: object) -> int:
    if isinstance(value, Mapping):
        if not value:
            return 1
        return 1 + max(_max_depth(child) for child in value.values())
    if isinstance(value, list | tuple | set | frozenset):
        if not value:
            return 1
        return 1 + max(_max_depth(child) for child in value)
    return 0


def _normalized_key(key: str) -> str:
    return key.lower().replace("-", "_").replace(" ", "_")


def _key_segments(key: str) -> frozenset[str]:
    return frozenset(
        segment for segment in re.split(r"[.\[\]{}:/\\\s-]+", _normalized_key(key)) if segment
    )


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalized_key(key)
    segments = _key_segments(normalized)
    if normalized in _SENSITIVE_TOKEN_KEYS or normalized.endswith("_token") or "token" in segments:
        return True
    return any(
        fragment != "token" and (fragment in normalized or fragment in segments)
        for fragment in _SENSITIVE_KEY_FRAGMENTS
    )


def _is_provider_response_body_key(key: str) -> bool:
    normalized = _normalized_key(key)
    compact = _compact_key(key)
    segments = _key_segments(normalized)
    return (
        normalized in _PROVIDER_RESPONSE_BODY_KEYS
        or compact in _PROVIDER_RESPONSE_BODY_KEYS
        or bool(_PROVIDER_RESPONSE_BODY_KEYS.intersection(segments))
    )


def _is_broker_raw_message_key(key: str) -> bool:
    normalized = _normalized_key(key)
    segments = _key_segments(normalized)
    if normalized in _BROKER_RAW_MESSAGE_KEYS:
        return True
    if "broker" not in segments and "broker" not in normalized:
        return False
    return (
        {"raw", "message"} <= segments
        or {"eligibility", "message"} <= segments
        or {"sensitive", "message"} <= segments
    )


def _is_local_llm_extra_body_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return "extra_body" in normalized and (
        "local_llm" in normalized
        or "local_openai" in normalized
        or "local_llm" in _key_segments(key)
    )


def _is_unsafe_text_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return normalized in _UNSAFE_TEXT_KEYS or bool(
        _UNSAFE_TEXT_KEYS.intersection(_key_segments(key))
    )


def _contains_unredacted_provider_response_body(key: str, value: object) -> bool:
    return _is_provider_response_body_key(key) and value != PROVIDER_RESPONSE_BODY_REDACTION_MARKER


def _contains_unredacted_broker_raw_message(key: str, value: object) -> bool:
    return _is_broker_raw_message_key(key) and value != BROKER_RAW_MESSAGE_REDACTION_MARKER


def _contains_unredacted_sensitive_local_llm_extra_body(key: str, value: object) -> bool:
    if value == LOCAL_LLM_EXTRA_BODY_REDACTION_MARKER:
        return False
    if not _is_local_llm_extra_body_key(key):
        return False
    if _LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(_key_segments(key)):
        return True
    return _value_contains_sensitive_extra_body_key(value)


def _value_contains_sensitive_extra_body_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS
            or _value_contains_sensitive_extra_body_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list | tuple | set | frozenset):
        return any(_value_contains_sensitive_extra_body_key(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(
            re.search(rf'"{re.escape(sensitive_key)}"\s*:', lowered)
            or re.search(rf"\b{re.escape(sensitive_key)}\s*[:=]", lowered)
            for sensitive_key in _LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS
        )
    return False


def _contains_unredacted_secret_pattern(key: str, value: object) -> bool:
    if _is_safe_redaction_marker(value):
        return False
    if _is_sensitive_key(key):
        return True
    return isinstance(value, str) and any(
        pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS
    )


def _contains_unredacted_unsafe_text_payload(key: str, value: object) -> bool:
    if _is_safe_redaction_marker(value):
        return False
    if _is_unsafe_text_key(key):
        return True
    return isinstance(value, str) and any(
        pattern.search(value) for pattern in _STACK_TRACE_PATTERNS
    )


def _is_safe_redaction_marker(value: object) -> bool:
    return isinstance(value, str) and value in _SAFE_REDACTION_MARKERS


def _compact_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


__all__ = [
    "BROKER_RAW_MESSAGE_REDACTION_MARKER",
    "DEFAULT_DIAGNOSTIC_REDACTION_POLICY",
    "DIAGNOSTIC_FIELD_MAX_DEPTH",
    "DIAGNOSTIC_REDACTION_MARKER",
    "DIAGNOSTIC_SINK_BASIC_LOGS",
    "DIAGNOSTIC_SINK_CHATBOX_DISCLOSURE",
    "DIAGNOSTIC_SINK_DASHBOARD",
    "DIAGNOSTIC_SINK_DETAILED_LOGS",
    "DIAGNOSTIC_SINK_FAILURE_JSONL",
    "DIAGNOSTIC_SINK_PERSISTED_LOGS",
    "DIAGNOSTIC_SINK_SNACKBAR",
    "DIAGNOSTIC_SINK_VISIBILITY_RULES",
    "DIAGNOSTIC_SINKS",
    "DIAGNOSTIC_VALIDATION_REASON_EXCESSIVE_DEPTH",
    "DIAGNOSTIC_VALIDATION_REASON_BROKER_RAW_MESSAGE",
    "DIAGNOSTIC_VALIDATION_REASON_FIELD_LIMIT_EXCEEDED",
    "DIAGNOSTIC_VALIDATION_REASON_PROVIDER_RESPONSE_BODY",
    "DIAGNOSTIC_VALIDATION_REASON_SECRET_PATTERN",
    "DIAGNOSTIC_VALIDATION_REASON_SENSITIVE_LOCAL_LLM_EXTRA_BODY",
    "DIAGNOSTIC_VALIDATION_REASON_UNSAFE_TEXT_PAYLOAD",
    "DIAGNOSTIC_VALIDATION_REASON_UNSUPPORTED_FIELD_TYPE",
    "DIAGNOSTIC_VALIDATION_REASON_VISIBILITY_FORBIDDEN",
    "DIAGNOSTIC_VALIDATION_STATUS_ACCEPTED",
    "DIAGNOSTIC_VALIDATION_STATUS_REJECTED",
    "LOCAL_LLM_EXTRA_BODY_REDACTION_MARKER",
    "PROVIDER_RESPONSE_BODY_REDACTION_MARKER",
    "ContentPolicy",
    "DiagnosticRedactionPolicy",
    "DiagnosticSink",
    "DiagnosticValidationReason",
    "DiagnosticValidationResult",
    "DiagnosticValidationStatus",
    "redact_diagnostics_for_sink",
    "validate_diagnostics_for_sink",
]
