from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from puripuly_heart.core.diagnostic_validation import (
    DIAGNOSTIC_SINK_PERSISTED_LOGS,
    DIAGNOSTIC_VALIDATION_STATUS_ACCEPTED,
    validate_desktop_overlay_repro_record,
    validate_desktop_overlay_repro_result,
    validate_diagnostics_for_sink,
)
from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_LIFECYCLE,
    DIAGNOSTIC_VISIBILITY_DIAGNOSTIC_ONLY,
    ErrorDiagnostics,
)

REPRO_JSONL_NAME = "desktop-overlay-repro.jsonl"
REPRO_RESULT_NAME = "result.json"
REPRO_CAPTURE_NAME = "desktop-overlay-repro.mp4"


class ReproArtifactError(Exception):
    pass


def expected_repro_outcomes(cycles: int) -> tuple[tuple[int, int, str], ...]:
    dispositions = (
        (1, "committed"),
        (2, "committed"),
        (3, "committed"),
        (4, "committed"),
        (10, "superseded"),
        (9, "stale"),
        (11, "committed"),
        (12, "superseded"),
        (13, "superseded"),
        (14, "committed"),
        (15, "committed"),
        (16, "committed"),
        (17, "committed"),
    )
    return tuple(
        (cycle, 17 * (cycle - 1) + revision, disposition)
        for cycle in range(1, cycles + 1)
        for revision, disposition in dispositions
    )


def expected_repro_visual_state(revision: int) -> Mapping[str, object]:
    offset = ((revision - 1) % 17) + 1
    line_counts = {2: 2, 4: 2, 15: 2}
    slot_counts = {15: 2, 16: 0}
    line_count = 0 if offset == 16 else line_counts.get(offset, 1)
    slot_count = slot_counts.get(offset, 1)
    return {
        "slot_count": slot_count,
        "line_count": line_count,
        "surface_visible": offset != 16,
        "interaction_mode": "locked",
        "window_width": 1344,
        "window_height": 336,
    }


def write_repro_artifacts(
    output_dir: Path,
    records: Sequence[Mapping[str, object]],
    result: Mapping[str, object],
) -> None:
    validated_records = [validate_desktop_overlay_repro_record(record) for record in records]
    validated_result = validate_desktop_overlay_repro_result(result)
    if any(record is None for record in validated_records) or validated_result is None:
        raise ReproArtifactError("validation_failed")
    jsonl = "".join(json.dumps(dict(record), sort_keys=True) + "\n" for record in validated_records)
    (output_dir / REPRO_JSONL_NAME).write_text(jsonl, encoding="utf-8")
    (output_dir / REPRO_RESULT_NAME).write_text(
        json.dumps(dict(validated_result), sort_keys=True), encoding="utf-8"
    )


def validate_repro_run_records(
    records: Sequence[Mapping[str, object]],
    *,
    cycles: int,
) -> None:
    expected = expected_repro_outcomes(cycles)
    if len(records) != len(expected):
        raise ReproArtifactError("validation_failed")
    for record, (cycle, revision, disposition) in zip(records, expected, strict=True):
        validated = validate_desktop_overlay_repro_record(record)
        if (
            validated is None
            or validated["cycle"] != cycle
            or validated["synthetic_revision"] != revision
            or validated["expected_disposition"] != disposition
            or validated["actual_disposition"] != disposition
            or validated["render_commit_acknowledged"] != (disposition == "committed")
            or any(
                validated[key] != value
                for key, value in expected_repro_visual_state(revision).items()
            )
        ):
            raise ReproArtifactError("validation_failed")


def verify_desktop_overlay_repro(*, output_dir: Path) -> int:
    try:
        validate_repro_artifacts(output_dir)
    except ReproArtifactError:
        print("artifact_invalid")
        return 1
    return 0


def validate_repro_artifacts(output_dir: Path) -> Mapping[str, object]:
    try:
        lines = (output_dir / REPRO_JSONL_NAME).read_text(encoding="utf-8").splitlines()
        result_payload = json.loads((output_dir / REPRO_RESULT_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReproArtifactError("artifact_invalid") from exc
    if not lines or not isinstance(result_payload, dict):
        raise ReproArtifactError("artifact_invalid")
    records: list[Mapping[str, object]] = []
    try:
        for line in lines:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ReproArtifactError("artifact_invalid")
            record = validate_desktop_overlay_repro_record(payload)
            if record is None:
                raise ReproArtifactError("artifact_invalid")
            records.append(record)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReproArtifactError("artifact_invalid") from exc
    result = validate_desktop_overlay_repro_result(result_payload)
    if result is None or result["outcome"] != "completed" or result["reason"] is not None:
        raise ReproArtifactError("artifact_invalid")
    cycles = result["cycles_completed"]
    if not isinstance(cycles, int) or isinstance(cycles, bool) or cycles < 100:
        raise ReproArtifactError("artifact_invalid")
    if cycles != result["cycles_requested"]:
        raise ReproArtifactError("artifact_invalid")
    try:
        validate_repro_run_records(records, cycles=cycles)
    except ReproArtifactError as exc:
        raise ReproArtifactError("artifact_invalid") from exc
    counts = {disposition: 0 for disposition in ("committed", "superseded", "stale", "failed")}
    for record in records:
        counts[str(record["actual_disposition"])] += 1
    if any(result[f"{name}_count"] != value for name, value in counts.items()):
        raise ReproArtifactError("artifact_invalid")
    if not all(
        result[key]
        for key in (
            "renderer_shutdown_completed",
            "bridge_shutdown_completed",
            "backdrop_shutdown_completed",
        )
    ):
        raise ReproArtifactError("artifact_invalid")
    try:
        if (output_dir / REPRO_CAPTURE_NAME).stat().st_size <= 0:
            raise ReproArtifactError("artifact_invalid")
    except OSError as exc:
        raise ReproArtifactError("artifact_invalid") from exc
    return capture_presence_metadata()


def capture_presence_metadata() -> Mapping[str, object]:
    metadata = {
        "capture_name": REPRO_CAPTURE_NAME,
        "capture_present": True,
        "capture_nonempty": True,
    }
    validation = validate_diagnostics_for_sink(
        ErrorDiagnostics(
            component="desktop_overlay_repro",
            operation="capture_presence",
            code="v1",
            category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
            visibility=DIAGNOSTIC_VISIBILITY_DIAGNOSTIC_ONLY,
            content_policy=CONTENT_POLICY_METADATA_ONLY,
            status_code=None,
            retry_after_ms=None,
            fields=metadata,
        ),
        DIAGNOSTIC_SINK_PERSISTED_LOGS,
    )
    if validation.status != DIAGNOSTIC_VALIDATION_STATUS_ACCEPTED:
        raise ReproArtifactError("artifact_invalid")
    return metadata
