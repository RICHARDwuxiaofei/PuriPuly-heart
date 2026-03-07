from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PINNED_PYTHON_VERSION = 'PYTHON_VERSION: "3.12.10"'
PINNED_UV_VERSION = 'UV_VERSION: "0.9.17"'
PINNED_INNOSETUP_VERSION = 'INNOSETUP_VERSION: "6.6.1"'
SHARED_SETUP_ACTION = "./.github/actions/setup-uv-environment"


def test_pyproject_caps_deepgram_sdk_below_v6() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "deepgram-sdk>=5.0.0,<6.0.0" in pyproject["project"]["dependencies"]


def test_release_workflow_uses_frozen_lockfile_sync() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert SHARED_SETUP_ACTION in workflow
    assert 'python -m pip install -e ".[build]"' not in workflow


def test_push_ci_workflow_uses_frozen_lockfile_sync() -> None:
    workflow = (ROOT / ".github" / "workflows" / "push-ci.yml").read_text(encoding="utf-8")

    assert SHARED_SETUP_ACTION in workflow
    assert 'python -m pip install -e ".[dev]"' not in workflow


def test_workflows_pin_exact_python_and_uv_versions() -> None:
    for workflow_path in (
        ROOT / ".github" / "workflows" / "push-ci.yml",
        ROOT / ".github" / "workflows" / "release.yml",
    ):
        workflow = workflow_path.read_text(encoding="utf-8")
        assert PINNED_PYTHON_VERSION in workflow
        assert PINNED_UV_VERSION in workflow
        assert SHARED_SETUP_ACTION in workflow


def test_workflows_pin_innosetup_and_use_shared_windows_build_script() -> None:
    for workflow_path in (
        ROOT / ".github" / "workflows" / "push-ci.yml",
        ROOT / ".github" / "workflows" / "release.yml",
    ):
        workflow = workflow_path.read_text(encoding="utf-8")
        assert PINNED_INNOSETUP_VERSION in workflow
        assert "scripts/ci/build-release-artifacts.ps1" in workflow


def test_push_ci_has_windows_release_path_job() -> None:
    workflow = (ROOT / ".github" / "workflows" / "push-ci.yml").read_text(encoding="utf-8")

    assert "runs-on: windows-latest" in workflow
    assert "Build Windows release path" in workflow


def test_shared_windows_build_script_runs_packaged_smoke_test() -> None:
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert "Start-Process" in script
    assert "osc-send" in script
    assert '"innosetup"' in script
    assert '"--version=$InnoSetupVersion"' in script
    assert "DisplayVersion" in script
    assert "Get-Command choco" in script


def test_shared_setup_action_installs_pinned_uv_and_uses_frozen_sync() -> None:
    action = (ROOT / ".github" / "actions" / "setup-uv-environment" / "action.yml").read_text(
        encoding="utf-8"
    )

    assert "uses: actions/setup-python@v5" in action
    assert "cache-dependency-path: uv.lock" in action
    assert '"uv==${{ inputs.uv-version }}"' in action
    assert "uv sync ${{ inputs.sync-args }} --frozen" in action
