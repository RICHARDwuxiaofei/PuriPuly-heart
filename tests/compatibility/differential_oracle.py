from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Difference:
    path: str
    baseline: object
    current: object


def differences(baseline: object, current: object, *, path: str = "") -> tuple[Difference, ...]:
    if isinstance(baseline, dict) and isinstance(current, dict):
        found: list[Difference] = []
        for key in sorted(set(baseline) | set(current)):
            child = f"{path}.{key}" if path else key
            if key not in baseline or key not in current:
                found.append(Difference(child, baseline.get(key), current.get(key)))
            else:
                found.extend(differences(baseline[key], current[key], path=child))
        return tuple(found)
    if isinstance(baseline, list) and isinstance(current, list):
        return () if baseline == current else (Difference(path, baseline, current),)
    return () if baseline == current else (Difference(path, baseline, current),)


def compare(
    baseline: dict[str, Any],
    current: dict[str, Any],
    approved_differences: dict[str, dict[str, object]],
) -> tuple[Difference, ...]:
    normalized_baseline = {key: value for key, value in baseline.items() if key != "provenance"}
    normalized_current = {key: value for key, value in current.items() if key != "provenance"}
    found = differences(normalized_baseline, normalized_current)
    unapproved = tuple(
        item
        for item in found
        if item.path not in approved_differences
        or approved_differences[item.path].get("baseline") != item.baseline
        or approved_differences[item.path].get("current") != item.current
        or not approved_differences[item.path].get("classification")
    )
    stale_rules = set(approved_differences) - {item.path for item in found}
    if stale_rules:
        raise AssertionError(f"stale approved-difference rules: {sorted(stale_rules)}")
    return unapproved
