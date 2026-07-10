from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.facade import load_vnext_settings, save_vnext_settings
from puripuly_heart.config.settings_vnext.migration import from_legacy_app_settings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


def _apply_changed_mapping_values(
    target: dict[str, Any],
    baseline: Mapping[str, object],
    next_values: Mapping[str, object],
) -> None:
    for key in baseline:
        if key not in next_values:
            target.pop(key, None)
    for key, next_value in next_values.items():
        previous_value = baseline.get(key)
        if isinstance(previous_value, Mapping) and isinstance(next_value, Mapping):
            target_value = target.get(key)
            if not isinstance(target_value, dict):
                target_value = {}
                target[key] = target_value
            _apply_changed_mapping_values(target_value, previous_value, next_value)
        elif previous_value != next_value:
            target[key] = copy.deepcopy(next_value)


class SettingsVNextCanonicalPersistenceAdapter:
    def load(self, path: Path, compatibility_settings: object) -> AppSettingsVNext:
        result = load_vnext_settings(path)
        if result.settings is not None:
            return result.settings
        return from_legacy_app_settings(compatibility_settings)

    def persist(self, path: Path, settings: AppSettingsVNext) -> None:
        result = save_vnext_settings(path, settings)
        if not result.ok:
            status = getattr(result.status, "value", result.status)
            message = result.error.message if result.error is not None else status
            raise RuntimeError(message)

    def project(
        self,
        settings: object,
        *,
        canonical: AppSettingsVNext | None,
        authoritative: bool,
    ) -> AppSettingsVNext:
        if canonical is None or not authoritative:
            return from_legacy_app_settings(settings)
        return canonical

    def apply_legacy_delta(
        self,
        *,
        canonical: AppSettingsVNext | None,
        base_settings: object | None,
        next_settings: object,
    ) -> AppSettingsVNext:
        converted_next = from_legacy_app_settings(next_settings)
        if canonical is None or base_settings is None:
            return converted_next
        converted_base = from_legacy_app_settings(base_settings)
        canonical_data = serialization.to_dict(canonical)
        _apply_changed_mapping_values(
            canonical_data,
            serialization.to_dict(converted_base),
            serialization.to_dict(converted_next),
        )
        return serialization.from_dict(canonical_data)

    def snapshot(self, canonical: AppSettingsVNext | None) -> AppSettingsVNext | None:
        return copy.deepcopy(canonical)

    def rollback(self, snapshot: AppSettingsVNext | None) -> AppSettingsVNext | None:
        return copy.deepcopy(snapshot)
