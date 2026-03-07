from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pyproject_caps_deepgram_sdk_below_v6() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "deepgram-sdk>=5.0.0,<6.0.0" in pyproject["project"]["dependencies"]


def test_release_workflow_uses_frozen_lockfile_sync() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "uv sync --extra build --frozen" in workflow
    assert 'python -m pip install -e ".[build]"' not in workflow


def test_push_ci_workflow_uses_frozen_lockfile_sync() -> None:
    workflow = (ROOT / ".github" / "workflows" / "push-ci.yml").read_text(encoding="utf-8")

    assert "uv sync --dev --frozen" in workflow
    assert 'python -m pip install -e ".[dev]"' not in workflow
