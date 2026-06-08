from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType


def freeze_settings_values(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze_settings_value(value) for key, value in values.items()})


def _freeze_settings_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_settings_value(nested_value) for key, nested_value in value.items()}
        )

    if isinstance(value, (list, tuple)):
        return tuple(_freeze_settings_value(item) for item in value)

    return deepcopy(value)


__all__ = ["freeze_settings_values"]
