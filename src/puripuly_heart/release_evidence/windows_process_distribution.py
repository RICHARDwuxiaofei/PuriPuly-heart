from __future__ import annotations

import hashlib
import json
import ntpath
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DISTRIBUTION_EVIDENCE_SCHEMA = "puripuly-heart/windows-process-distribution/v1"
PRODUCTION_APP_ID = "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"
CLIENT_KEYS = ("vrchat", "discord_stable", "discord_ptb", "discord_canary")
ClientStatus = Literal["passed", "waived", "unavailable", "not_run"]


@dataclass(frozen=True, slots=True)
class ManualClientCell:
    status: ClientStatus
    result: str | None
    multilevel_ancestry_risk: bool
    waiver_authority: str | None = None
    waiver_reason: str | None = None


def validate_installer_isolation(*, app_id: str, install_dir: Path, workspace_root: Path) -> None:
    normalized_app_id = app_id.strip().strip("{").strip("}").casefold()
    normalized_production = PRODUCTION_APP_ID.strip("{").strip("}").casefold()
    if normalized_app_id == normalized_production or not normalized_app_id:
        raise ValueError("installer smoke requires a non-production alternate AppId")
    resolved_install = install_dir.resolve()
    resolved_workspace = workspace_root.resolve()
    if resolved_install == resolved_workspace or resolved_workspace in resolved_install.parents:
        raise ValueError("installer smoke directory must be outside the workspace")
    normalized = ntpath.normcase(str(resolved_install))
    if normalized.endswith(ntpath.normcase(r"Program Files\PuriPulyHeart")):
        raise ValueError("installer smoke directory must not use the production install path")


def validate_runtime_report(report: dict[str, object], *, expected_root: Path) -> None:
    required = {
        "schema",
        "status",
        "proctap_version",
        "proctap_module",
        "native_module",
        "native_sha256",
        "native_process_specific",
        "capture_started",
        "device_fallback_used",
        "credentials_used",
        "network_used",
    }
    if set(report) != required or report.get("schema") != (
        "puripuly-heart/process-capture-runtime-check/v1"
    ):
        raise ValueError("invalid process-capture runtime report schema")
    if (
        report.get("status") != "passed"
        or report.get("proctap_version") != "1.0.3"
        or report.get("native_process_specific") is not True
        or report.get("capture_started") is not True
        or report.get("device_fallback_used") is not False
        or report.get("credentials_used") is not False
        or report.get("network_used") is not False
    ):
        raise ValueError("process-capture runtime report did not pass strict validation")
    native_path = Path(str(report["native_module"])).resolve()
    expected = expected_root.resolve()
    if expected != native_path and expected not in native_path.parents:
        raise ValueError("reported ProcTap native module is outside the expected artifact root")
    if not native_path.is_file() or _sha256(native_path) != report["native_sha256"]:
        raise ValueError("reported ProcTap native module hash does not match")


def validate_manual_matrix(matrix: dict[str, ManualClientCell]) -> None:
    if tuple(matrix) != CLIENT_KEYS:
        raise ValueError("manual client matrix keys or order are invalid")
    for cell in matrix.values():
        if cell.status == "passed" and not cell.result:
            raise ValueError("passed manual client cells require a result")
        if cell.status == "waived" and (
            cell.result != "not_tested"
            or cell.waiver_authority != "acceptance_authority"
            or not cell.waiver_reason
        ):
            raise ValueError("waived manual client cells require explicit acceptance authority")
        if cell.status not in {"passed", "waived"} and cell.result is not None:
            raise ValueError("unavailable or not-run cells cannot claim a result")
        if cell.status != "waived" and (
            cell.waiver_authority is not None or cell.waiver_reason is not None
        ):
            raise ValueError("only waived manual client cells can include waiver facts")
        if not cell.multilevel_ancestry_risk:
            raise ValueError("manual matrix must carry the multilevel ancestry risk")


def validate_distribution_evidence(evidence: dict[str, object]) -> None:
    required = {
        "schema",
        "status",
        "classification",
        "supported_target",
        "packaged",
        "installed",
        "installer_isolation",
        "manual_matrix",
        "manual_matrix_complete",
        "manual_matrix_status",
        "technical_status",
        "overlay",
        "workflow",
        "commands",
    }
    if set(evidence) != required or evidence.get("schema") != DISTRIBUTION_EVIDENCE_SCHEMA:
        raise ValueError("invalid Windows process distribution evidence schema")
    if evidence.get("status") not in {
        "passed",
        "closed_with_waiver",
        "partial",
        "failed",
        "blocked",
    }:
        raise ValueError("invalid Windows process distribution evidence status")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_report(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("runtime report must be an object")
    return value


def artifact_sha256(path: Path) -> str:
    return _sha256(path)
