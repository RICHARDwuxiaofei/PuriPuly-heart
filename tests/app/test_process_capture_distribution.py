from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from puripuly_heart.release_evidence.windows_process_distribution import (
    CLIENT_KEYS,
    DISTRIBUTION_EVIDENCE_SCHEMA,
    PRODUCTION_APP_ID,
    ManualClientCell,
    validate_distribution_evidence,
    validate_installer_isolation,
    validate_manual_matrix,
    validate_runtime_report,
)

ROOT = Path(__file__).resolve().parents[2]


def test_build_spec_collects_pinned_proctap_hidden_imports_and_native_binary() -> None:
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")

    assert 'collect_dynamic_libs("proctap", destdir="proctap")' in spec
    assert 'get_module_file_attribute("proctap._native")' in spec
    assert 'collect_submodules("proctap")' in spec
    assert '"proctap", "proctap._native", "proctap.backends.windows"' in spec
    assert "Pinned ProcTap package did not provide a packageable _native extension" in spec


def test_release_workflow_runs_packaged_installed_strict_smoke_and_alternate_installer() -> None:
    script = (ROOT / "scripts/ci/build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert script.count("Invoke-ProcessCaptureRuntimeSmokeCheck") >= 4
    assert '"/DMyAppId=$InstallerTestAppId"' in script
    assert '$InstallerTestAppId = "{{C2E4A7B1-59F3-4C89-9D21-7E6B5A4032F8}"' in script
    assert '"/DSkipLocalSttProvisioning=1"' in script
    assert "process-capture-runtime-check" in script
    assert "native_process_specific" in script
    assert "device_fallback_used" in script


def test_installer_smoke_skip_is_compile_time_only_and_production_default_is_unchanged() -> None:
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert "#ifdef SkipLocalSttProvisioning" in script
    assert "Local STT provisioning skipped for isolated installer smoke." in script
    assert f'#define MyAppId "{{{PRODUCTION_APP_ID}}}"' not in script
    assert '#define MyAppId "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"' in script


def test_installer_isolation_rejects_production_identity_and_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    install = tmp_path / "isolated-install"
    workspace.mkdir()

    validate_installer_isolation(
        app_id="{C2E4A7B1-59F3-4C89-9D21-7E6B5A4032F8}",
        install_dir=install,
        workspace_root=workspace,
    )
    with pytest.raises(ValueError, match="alternate AppId"):
        validate_installer_isolation(
            app_id=PRODUCTION_APP_ID,
            install_dir=install,
            workspace_root=workspace,
        )
    with pytest.raises(ValueError, match="outside the workspace"):
        validate_installer_isolation(
            app_id="{C2E4A7B1-59F3-4C89-9D21-7E6B5A4032F8}",
            install_dir=workspace / "install",
            workspace_root=workspace,
        )


def test_runtime_report_requires_native_hash_strict_mode_and_no_fallback(tmp_path: Path) -> None:
    native = tmp_path / "proctap" / "_native.cp312-win_amd64.pyd"
    native.parent.mkdir()
    native.write_bytes(b"native")
    report = {
        "schema": "puripuly-heart/process-capture-runtime-check/v1",
        "status": "passed",
        "proctap_version": "1.0.3",
        "proctap_module": str(tmp_path / "proctap" / "__init__.py"),
        "native_module": str(native),
        "native_sha256": hashlib.sha256(b"native").hexdigest(),
        "native_process_specific": True,
        "capture_started": True,
        "device_fallback_used": False,
        "credentials_used": False,
        "network_used": False,
    }

    validate_runtime_report(report, expected_root=tmp_path)
    with pytest.raises(ValueError, match="strict validation"):
        validate_runtime_report({**report, "device_fallback_used": True}, expected_root=tmp_path)


def test_manual_matrix_cannot_claim_unavailable_clients_and_carries_risk() -> None:
    matrix = {
        key: ManualClientCell(
            status="unavailable",
            result=None,
            multilevel_ancestry_risk=True,
        )
        for key in CLIENT_KEYS
    }

    validate_manual_matrix(matrix)
    matrix["vrchat"] = ManualClientCell(
        status="unavailable", result="passed", multilevel_ancestry_risk=True
    )
    with pytest.raises(ValueError, match="cannot claim"):
        validate_manual_matrix(matrix)


def test_distribution_evidence_schema_is_strict() -> None:
    evidence = {
        "schema": DISTRIBUTION_EVIDENCE_SCHEMA,
        "status": "passed",
        "classification": None,
        "supported_target": {},
        "packaged": {},
        "installed": {},
        "installer_isolation": {},
        "manual_matrix": {},
        "manual_matrix_complete": False,
        "manual_matrix_status": "waived",
        "technical_status": "passed",
        "overlay": {},
        "workflow": {},
        "commands": [],
    }

    validate_distribution_evidence(evidence)
    with pytest.raises(ValueError, match="schema"):
        validate_distribution_evidence({**evidence, "extra": json.loads("true")})


def test_release_workflow_uses_short_overlay_target_and_verifies_current_version() -> None:
    script = (ROOT / "scripts/ci/build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert "PURIPULY_HEART_RELEASE_BUILD_ROOT" in script
    assert 'Join-Path $env:TEMP "PuriPulyHeart-ReleaseBuild-$AppVersion"' in script
    assert "$overlayReleasePath --version" in script
    assert 'throw "Rust overlay version mismatch: expected $AppVersion' in script


def test_checked_in_distribution_evidence_matches_schema_and_manual_claim_rules() -> None:
    evidence = json.loads(
        (ROOT / "docs/release-evidence/windows-process-distribution-host.json").read_text(
            encoding="utf-8"
        )
    )
    matrix = {
        key: ManualClientCell(
            status=evidence["manual_matrix"][key]["status"],
            result=evidence["manual_matrix"][key]["result"],
            multilevel_ancestry_risk=evidence["manual_matrix"][key]["multilevel_ancestry_risk"],
            waiver_authority=evidence["manual_matrix"][key].get("waiver_authority"),
            waiver_reason=evidence["manual_matrix"][key].get("waiver_reason"),
        )
        for key in CLIENT_KEYS
    }

    validate_distribution_evidence(evidence)
    validate_manual_matrix(matrix)
    assert evidence["installer_isolation"]["production_app_id_used"] is False
    assert evidence["installer_isolation"]["production_install_path_used"] is False
    assert evidence["technical_status"] == "passed"
    assert evidence["manual_matrix_complete"] is False
    assert evidence["manual_matrix_status"] == "waived"
    assert evidence["status"] == "closed_with_waiver"
    assert matrix["discord_stable"].status == "waived"
    assert matrix["discord_stable"].result == "not_tested"
    assert matrix["vrchat"].status == "waived"
    assert matrix["vrchat"].result == "not_tested"
    assert all(cell.status != "passed" for cell in matrix.values())
