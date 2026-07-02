from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import asdict, fields, is_dataclass
from typing import Any, Final

from puripuly_heart.config.settings_vnext.schema import (
    VNEXT_SETTINGS_SCHEMA_VERSION,
    AppSettingsVNext,
)

CANONICAL_TOP_LEVEL_KEYS: Final = frozenset({"settings_version", "intent", "state"})
_PROVIDER_VERIFICATION_FIELDS: Final = (
    "deepgram",
    "soniox",
    "google",
    "openrouter",
    "deepseek",
    "cerebras",
    "alibaba_beijing",
    "alibaba_singapore",
)
_PROVIDER_VERIFICATION_NON_UNKNOWN_STATUSES: Final = frozenset({"verified", "failed", "skipped"})


def to_dict(settings: AppSettingsVNext) -> dict[str, Any]:
    """Serialize canonical vNext settings.

    The vNext persisted schema intentionally writes no legacy projection keys. Runtime-only
    state and raw secret values are excluded by the schema itself.
    """

    if not isinstance(settings, AppSettingsVNext):
        raise TypeError("vNext settings serializer requires AppSettingsVNext")
    data = asdict(settings)
    return {
        "settings_version": VNEXT_SETTINGS_SCHEMA_VERSION,
        "intent": data["intent"],
        "state": data["state"],
    }


def to_json_text(settings: AppSettingsVNext) -> str:
    return json.dumps(to_dict(settings), ensure_ascii=False, indent=2)


def from_dict(data: Mapping[str, Any]) -> AppSettingsVNext:
    if not isinstance(data, Mapping):
        raise ValueError("vNext settings must be a JSON object")
    raw_version = data.get("settings_version", VNEXT_SETTINGS_SCHEMA_VERSION)
    if isinstance(raw_version, bool):
        raise ValueError("vNext settings_version must be an integer")
    try:
        settings_version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("vNext settings_version must be an integer") from exc

    default = AppSettingsVNext(settings_version=settings_version)
    compatible_data = _downgrade_unbound_provider_verification_entries(data)
    merged = _merge_dataclass(default, compatible_data, path="settings")
    if not isinstance(merged, AppSettingsVNext):
        raise TypeError("vNext settings merge produced unexpected type")
    return merged


def _downgrade_unbound_provider_verification_entries(
    data: Mapping[str, Any],
) -> Mapping[str, Any]:
    state = data.get("state")
    if not isinstance(state, Mapping):
        return data
    provider_verification = state.get("provider_verification")
    if not isinstance(provider_verification, Mapping):
        return data

    entries_to_downgrade = {
        provider
        for provider in _PROVIDER_VERIFICATION_FIELDS
        if _is_unbound_non_unknown_provider_verification_entry(provider_verification.get(provider))
    }
    if not entries_to_downgrade:
        return data

    compatible = copy.deepcopy(dict(data))
    compatible_state = dict(compatible.get("state", {}))
    compatible_provider_verification = dict(compatible_state.get("provider_verification", {}))
    for provider in entries_to_downgrade:
        compatible_provider_verification[provider] = {"status": "unknown"}
    compatible_state["provider_verification"] = compatible_provider_verification
    compatible["state"] = compatible_state
    return compatible


def _is_unbound_non_unknown_provider_verification_entry(entry: object) -> bool:
    if not isinstance(entry, Mapping):
        return False
    if entry.get("status") not in _PROVIDER_VERIFICATION_NON_UNKNOWN_STATUSES:
        return False
    return not _has_provider_verification_binding_evidence(entry)


def _has_provider_verification_binding_evidence(entry: Mapping[object, object]) -> bool:
    return (
        _is_non_empty_string(entry.get("provider"))
        and _is_non_empty_string(entry.get("secret_key"))
        and (
            _is_non_empty_string(entry.get("secret_revision"))
            or _is_non_empty_string(entry.get("secret_fingerprint"))
        )
        and isinstance(entry.get("verifier_context"), Mapping)
        and bool(entry.get("verifier_context"))
    )


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _merge_dataclass(default: object, raw: object, *, path: str) -> object:
    if not is_dataclass(default) or isinstance(default, type):
        return copy.deepcopy(raw)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path} must be a JSON object")

    kwargs: dict[str, object] = {}
    for field in fields(default):
        default_value = getattr(default, field.name)
        child_path = f"{path}.{field.name}"
        if field.name not in raw:
            kwargs[field.name] = copy.deepcopy(default_value)
            continue
        raw_value = raw[field.name]
        if is_dataclass(default_value) and not isinstance(default_value, type):
            kwargs[field.name] = _merge_dataclass(default_value, raw_value, path=child_path)
        elif isinstance(default_value, dict):
            if not isinstance(raw_value, Mapping):
                raise ValueError(f"{child_path} must be a JSON object")
            kwargs[field.name] = copy.deepcopy(dict(raw_value))
        elif isinstance(default_value, list):
            if not isinstance(raw_value, list):
                raise ValueError(f"{child_path} must be a JSON array")
            kwargs[field.name] = copy.deepcopy(raw_value)
        else:
            kwargs[field.name] = copy.deepcopy(raw_value)

    merged = type(default)(**kwargs)
    validate = getattr(merged, "validate", None)
    if callable(validate):
        validate()
    return merged


__all__ = [
    "CANONICAL_TOP_LEVEL_KEYS",
    "from_dict",
    "to_dict",
    "to_json_text",
]
