from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = REPO_ROOT / "src" / "puripuly_heart" / "data" / "i18n"
RUNTIME_SOURCE_DIR = REPO_ROOT / "src" / "puripuly_heart"

DYNAMIC_I18N_PREFIXES = (
    "language.",
    "locale.",
    "provider.",
    "region.",
    "settings.subtab.",
    "settings.overlay.calibration.anchor.",
    "settings.overlay.calibration.text_scale.",
    "settings.overlay.failure.",
    "settings.overlay.status.",
    "settings.peer_translation.status.",
    "logs.mode.",
    "settings.translation_model.",
)


def _load_bundles() -> dict[str, dict[str, str]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(I18N_DIR.glob("*.json"))
    }


def _runtime_python_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(RUNTIME_SOURCE_DIR.rglob("*.py"))
    )


def test_i18n_bundles_share_the_same_keys() -> None:
    bundles = _load_bundles()
    assert "en" in bundles

    expected_keys = set(bundles["en"])
    mismatches = {
        locale: {
            "missing": sorted(expected_keys - set(bundle)),
            "extra": sorted(set(bundle) - expected_keys),
        }
        for locale, bundle in bundles.items()
        if set(bundle) != expected_keys
    }

    assert mismatches == {}


def test_i18n_bundles_do_not_keep_unused_runtime_keys() -> None:
    bundles = _load_bundles()
    all_keys = sorted(set().union(*(bundle.keys() for bundle in bundles.values())))
    runtime_source = _runtime_python_source()

    unused_keys = [
        key
        for key in all_keys
        if key not in runtime_source and not key.startswith(DYNAMIC_I18N_PREFIXES)
    ]

    assert unused_keys == []
