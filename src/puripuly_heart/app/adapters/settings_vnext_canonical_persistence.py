from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from puripuly_heart.app.adapters.canonical_state_repository import CanonicalStateRevisionConflict
from puripuly_heart.app.ports.settings_repository import (
    SettingsCommitReceipt,
    SettingsNotInitializedError,
)
from puripuly_heart.config.settings import from_dict
from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.compat import canonical_settings_path_lock
from puripuly_heart.config.settings_vnext.facade import load_vnext_settings, save_vnext_settings
from puripuly_heart.config.settings_vnext.migration import (
    from_legacy_app_settings,
    to_legacy_dict,
)
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

    def initialize(
        self,
        path: Path,
        settings: AppSettingsVNext,
        *,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt:
        with canonical_settings_path_lock(path):
            if path.exists():
                raise CanonicalStateRevisionConflict("canonical settings already initialized")
            result = save_vnext_settings(path, settings)
            if not result.ok:
                status = getattr(result.status, "value", result.status)
                raise RuntimeError(str(status))
            return _receipt(settings, reason=reason, correlation_id=correlation_id)

    def load_receipt(
        self,
        path: Path,
        *,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt:
        if not path.exists():
            raise SettingsNotInitializedError("canonical settings are not initialized")
        result = load_vnext_settings(path)
        if result.settings is None:
            status = getattr(result.status, "value", result.status)
            raise RuntimeError(str(status))
        return _receipt(result.settings, reason=reason, correlation_id=correlation_id)

    def legacy_projection(self, settings: AppSettingsVNext) -> object:
        return from_dict(to_legacy_dict(settings))

    def values_for(self, settings: AppSettingsVNext) -> Mapping[str, object]:
        return serialization.to_dict(settings)

    def envelope_from_values(self, values: Mapping[str, object]) -> AppSettingsVNext:
        return serialization.from_dict(_mutable_settings_values(values))

    def receipt_for(
        self,
        settings: AppSettingsVNext,
        *,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt:
        return _receipt(settings, reason=reason, correlation_id=correlation_id)

    def persist_delta(
        self,
        path: Path,
        *,
        baseline: AppSettingsVNext,
        next_settings: AppSettingsVNext,
        expected_revision: str,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt:
        if baseline.intent.telemetry.consent != next_settings.intent.telemetry.consent:
            raise RuntimeError("telemetry consent requires atomic telemetry state transition")
        with canonical_settings_path_lock(path):
            load_result = load_vnext_settings(path)
            if load_result.settings is None:
                status = getattr(load_result.status, "value", load_result.status)
                message = load_result.error.message if load_result.error is not None else status
                raise RuntimeError(f"canonical latest load failed: {message}")
            latest = load_result.settings
            revision = f"sha256:{hashlib.sha256(serialization.to_json_text(latest).encode('utf-8')).hexdigest()}"
            if revision != expected_revision:
                raise CanonicalStateRevisionConflict("canonical state revision conflict")
            merged_data = serialization.to_dict(latest)
            _apply_changed_mapping_values(
                merged_data,
                serialization.to_dict(baseline),
                serialization.to_dict(next_settings),
            )
            merged = serialization.from_dict(merged_data)
            result = save_vnext_settings(path, merged)
            if not result.ok:
                status = getattr(result.status, "value", result.status)
                message = result.error.message if result.error is not None else status
                raise RuntimeError(message)
            return _receipt(merged, reason=reason, correlation_id=correlation_id)

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


def _receipt(
    settings: AppSettingsVNext,
    *,
    reason: str | None,
    correlation_id: str | None,
) -> SettingsCommitReceipt:
    revision = (
        f"sha256:{hashlib.sha256(serialization.to_json_text(settings).encode('utf-8')).hexdigest()}"
    )
    return SettingsCommitReceipt(settings, revision, reason, correlation_id)


def _mutable_settings_values(values: Mapping[str, object]) -> dict[str, Any]:
    def copy_value(value: object) -> Any:
        if isinstance(value, Mapping):
            return {str(key): copy_value(nested) for key, nested in value.items()}
        if isinstance(value, tuple | list):
            return [copy_value(item) for item in value]
        return copy.deepcopy(value)

    return {str(key): copy_value(value) for key, value in values.items()}
