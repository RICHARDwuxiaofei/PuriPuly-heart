from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

from tests.compatibility.differential_oracle import compare

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
CURRENT_REPAIR_BASE = "01b3973b64425c8feb9955696d5217fed5d422a4"


def _json(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _isolated_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "APPDATA",
            "HOME",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "WINDIR",
        }
    }
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _probe(source_root: Path) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("differential_probe.py")),
            "--source-root",
            str(source_root),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_isolated_env(),
    )
    return json.loads(completed.stdout)


def _scenario_probe(source_root: Path, source: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("observable_scenario_probe.py")),
            "--source-root",
            str(source_root),
            "--source",
            source,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_isolated_env(),
    )
    return json.loads(completed.stdout)


def _changed_production_files() -> tuple[str, ...]:
    tracked = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "-z",
            CURRENT_REPAIR_BASE,
            "--",
            "src/puripuly_heart",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    untracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            "src/puripuly_heart",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    changed = {
        path.decode("utf-8").replace("\\", "/")
        for output in (tracked.stdout, untracked.stdout)
        for path in output.split(b"\0")
        if path
    }
    return tuple(sorted(changed))


def _source_delta_digest(
    records: list[tuple[str, bytes | None, bytes | None]],
) -> str:
    digest = hashlib.sha256()
    for relative, base_bytes, current_bytes in sorted(records):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if base_bytes is None:
            digest.update(b"base-absent\0")
        else:
            digest.update(b"base-present\0")
            digest.update(base_bytes)
            digest.update(b"\0")
        if current_bytes is None:
            digest.update(b"current-absent\0")
        else:
            digest.update(b"current-present\0")
            digest.update(current_bytes)
            digest.update(b"\0")
    return digest.hexdigest()


def _base_blob(relative: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{CURRENT_REPAIR_BASE}:{relative}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return result.stdout
    if b"does not exist" in result.stderr or b"exists on disk, but not in" in result.stderr:
        return None
    raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))


def _current_worktree_bytes(relative: str, *, root: Path = ROOT) -> bytes | None:
    path = root / relative
    if not path.exists():
        return None
    current_bytes = path.read_bytes()
    if path.suffix.lower() in {".json", ".py"}:
        return current_bytes.replace(b"\r\n", b"\n")
    return current_bytes


def _current_file_digest(path: Path) -> str:
    relative = path.relative_to(ROOT).as_posix()
    current_bytes = _current_worktree_bytes(relative)
    if current_bytes is None:
        raise AssertionError(f"missing current file: {relative}")
    return hashlib.sha256(current_bytes).hexdigest()


def _manifest_production_source_digest(files: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in files:
        digest.update(relative.encode())
        digest.update(b"\0")
        current_bytes = _current_worktree_bytes(relative)
        digest.update(current_bytes if current_bytes is not None else b"current-absent")
        digest.update(b"\0")
    return digest.hexdigest()


def _manifest_source_delta_digest(files: list[str]) -> str:
    return _source_delta_digest(
        [
            (
                relative,
                _base_blob(relative),
                _current_worktree_bytes(relative),
            )
            for relative in files
        ]
    )


def test_source_delta_digest_distinguishes_base_absent_from_empty_blob() -> None:
    absent = _source_delta_digest([("src/new.py", None, b"current")])
    empty = _source_delta_digest([("src/new.py", b"", b"current")])
    assert absent != empty
    assert absent == _source_delta_digest([("src/new.py", None, b"current")])


def test_current_worktree_text_bytes_normalize_only_line_endings(tmp_path: Path) -> None:
    relative = "source.py"
    source = tmp_path / relative
    source.write_bytes(b"first\nsecond\n")
    lf_bytes = _current_worktree_bytes(relative, root=tmp_path)
    assert lf_bytes is not None
    lf_digest = hashlib.sha256(lf_bytes).hexdigest()

    source.write_bytes(b"first\r\nsecond\r\n")
    crlf_bytes = _current_worktree_bytes(relative, root=tmp_path)
    assert crlf_bytes == lf_bytes
    assert hashlib.sha256(crlf_bytes).hexdigest() == lf_digest

    source.write_bytes(b"first\r\nchanged\r\n")
    edited_bytes = _current_worktree_bytes(relative, root=tmp_path)
    assert edited_bytes != lf_bytes
    assert hashlib.sha256(edited_bytes).hexdigest() != lf_digest

    source.unlink()
    assert _current_worktree_bytes(relative, root=tmp_path) is None

    untracked = tmp_path / "untracked.json"
    untracked.write_bytes(b'{\r\n  "present": true\r\n}\r\n')
    assert _current_worktree_bytes("untracked.json", root=tmp_path) == (
        b'{\n  "present": true\n}\n'
    )


def test_current_worktree_non_text_bytes_remain_raw(tmp_path: Path) -> None:
    raw = b"first\r\nsecond\r\n\x00"
    (tmp_path / "source.bin").write_bytes(raw)

    assert _current_worktree_bytes("source.bin", root=tmp_path) == raw


def _archive_fixed_source(
    archive_path: Path,
    revision: str,
    *,
    env: dict[str, str] | None = None,
) -> None:
    deterministic_env = dict(os.environ if env is None else env)
    deterministic_env["GIT_CONFIG_GLOBAL"] = os.devnull
    deterministic_env["GIT_CONFIG_NOSYSTEM"] = "1"
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "archive",
            "--format=tar",
            "-o",
            str(archive_path),
            revision,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        env=deterministic_env,
    )


def test_normalized_current_trace_matches_baseline_golden() -> None:
    baseline = _json("baseline_normalized_golden.json")
    current = _json("current_normalized_trace.json")
    rules = _json("approved_difference_rules.json")["rules"]

    assert compare(baseline, current, rules) == ()


def test_approved_difference_rules_are_narrow_and_classification_linked() -> None:
    rules = _json("approved_difference_rules.json")["rules"]
    scenario_rules = _json("approved_difference_rules.json")["scenario_rules"]
    artifact = _json("vnext_architecture_classification_proposal.json")
    approved_ids = {
        *(entry["id"] for entry in artifact["approved_classifications"]),
        *(entry["id"] for entry in artifact["mandatory_preserved_surfaces"]),
        *(
            item_id
            for item_ids in artifact["final_user_classification_map"].values()
            for item_id in item_ids
        ),
    }

    assert all("*" not in path for path in (*rules, *scenario_rules))
    assert {
        *(rule["classification"] for rule in rules.values()),
        *(rule["classification"] for rule in scenario_rules.values()),
    }.issubset(approved_ids)
    assert all(set(rule) == {"classification", "baseline", "current"} for rule in rules.values())
    assert all(
        set(rule) == {"classification", "baseline", "current"} for rule in scenario_rules.values()
    )


def test_seeded_contract_violation_is_detected() -> None:
    baseline = _json("baseline_normalized_golden.json")
    current = _json("current_normalized_trace.json")
    mutated = json.loads(json.dumps(current))
    mutated["prompt_routing"]["cerebras"] = "CEREBRAS_NAMED"

    assert [
        item.path
        for item in compare(baseline, mutated, _json("approved_difference_rules.json")["rules"])
    ] == ["prompt_routing.cerebras"]


@pytest.mark.parametrize(
    ("category", "scenario_name", "field", "mutated_value", "classification_ref"),
    [
        ("routing_channel", "output_routes", "peer_chatbox", "published", "A-002"),
        (
            "stt_lifecycle",
            "stt_toggle_off_restart",
            "finalization",
            "unexpected",
            "P-020",
        ),
        (
            "settings_persistence",
            "settings_persistence",
            "operational_state",
            "data_loss",
            "M-001",
        ),
        (
            "provider_failure",
            "provider_timeout_non_success_invalid_response",
            "invalid_response",
            "accepted",
            "P-022",
        ),
        ("diagnostics", "safe_diagnostics", "raw_error_visible", "leaked", "P-025"),
        (
            "overlay",
            "overlay_disconnect_reconnect_target",
            "restored_snapshot",
            "failed",
            "M-005",
        ),
        (
            "stale_result",
            "lifecycle_races_stale_result",
            "after_shutdown",
            "accepted",
            "P-033",
        ),
    ],
)
def test_representative_seeded_scenario_violations_fail_comparison(
    category: str,
    scenario_name: str,
    field: str,
    mutated_value: object,
    classification_ref: str,
) -> None:
    scenarios = _json("observable_scenario_traces.json")["scenarios"]
    baseline = {name: scenario["baseline"] for name, scenario in scenarios.items()}
    current = {name: scenario["current"] for name, scenario in scenarios.items()}
    mutated = json.loads(json.dumps(current))
    mutated[scenario_name][field] = mutated_value
    scenario = scenarios[scenario_name]
    found = compare(
        {"observable": baseline},
        {"observable": mutated},
        _json("approved_difference_rules.json")["scenario_rules"],
    )

    assert category
    assert classification_ref in scenario["classification_refs"]
    assert [item.path for item in found] == [f"observable.{scenario_name}.{field}"]


def test_stale_or_wildcard_difference_rule_is_rejected() -> None:
    baseline = _json("baseline_normalized_golden.json")
    current = _json("current_normalized_trace.json")

    with pytest.raises(AssertionError, match="stale approved-difference rules"):
        compare(
            baseline,
            current,
            {
                "settings_defaults.*": {
                    "classification": "P-010",
                    "baseline": None,
                    "current": None,
                }
            },
        )


def test_source_listed_observable_scenarios_execute_exact_nodes_and_terminal_outcomes() -> None:
    fixture = _json("observable_scenario_traces.json")
    scenarios = fixture["scenarios"]
    artifact = _json("vnext_architecture_classification_proposal.json")
    classification_ids = {
        *(entry["id"] for entry in artifact["approved_classifications"]),
        *(entry["id"] for entry in artifact["mandatory_preserved_surfaces"]),
        *(
            item_id
            for values in artifact["final_user_classification_map"].values()
            for item_id in values
        ),
    }

    assert len(scenarios) == 15
    for scenario in scenarios.values():
        assert scenario["baseline"]
        assert scenario["current"]
        assert set(scenario["classification_refs"]).issubset(classification_ids)
    baseline = {name: scenario["baseline"] for name, scenario in scenarios.items()}
    current = {name: scenario["current"] for name, scenario in scenarios.items()}
    scenario_rules = _json("approved_difference_rules.json")["scenario_rules"]
    found = compare(
        {"observable": baseline},
        {"observable": current},
        scenario_rules,
    )
    assert found == ()
    current_probe = _scenario_probe(ROOT, "current")
    assert current_probe["scenarios"] == current
    assert set(current_probe["executed_nodes"]) == set(scenarios)
    expected_current_nodes = _json("current_observable_probe_nodes.json")
    assert current_probe["executed_nodes"] == expected_current_nodes
    field_indexes = _json("observable_field_node_indexes.json")
    expected_current_field_evidence = {
        name: {
            field: [expected_current_nodes[name][index] for index in indexes]
            for field, indexes in field_indexes["current"][name].items()
        }
        for name in scenarios
    }
    structured_probe_id = (
        "structured_scenario_probe.py::stale_completion_after_replacement_and_shutdown"
    )
    prompt_order_probe_id = "structured_scenario_probe.py::prompt_fallback_order"
    cerebras_probe_id = "structured_scenario_probe.py::cerebras_shared_routing"
    expected_current_field_evidence["lifecycle_races_stale_result"]["after_replacement"] = [
        structured_probe_id
    ]
    expected_current_field_evidence["lifecycle_races_stale_result"]["after_shutdown"] = [
        structured_probe_id
    ]
    expected_current_field_evidence["prompt_fallback"]["fallback_order"] = [prompt_order_probe_id]
    expected_current_field_evidence["prompt_fallback"]["cerebras"] = [cerebras_probe_id]
    assert current_probe["field_evidence"] == expected_current_field_evidence
    assert current_probe["executed_structured_probes"] == [
        structured_probe_id,
        prompt_order_probe_id,
        cerebras_probe_id,
    ]

    with tempfile.TemporaryDirectory(prefix="puripuly-fixed-scenario-source-") as directory:
        temp_root = Path(directory)
        archive_path = temp_root / "fixed.tar"
        extracted = temp_root / "fixed"
        extracted.mkdir()
        _archive_fixed_source(archive_path, "957731aa")
        with tarfile.open(archive_path) as archive:
            archive.extractall(extracted, filter="data")
        baseline_probe = _scenario_probe(extracted, "baseline")
    assert baseline_probe["scenarios"] == baseline
    assert set(baseline_probe["executed_nodes"]) == set(scenarios)
    expected_baseline_nodes = _json("baseline_observable_probe_nodes.json")
    assert baseline_probe["executed_nodes"] == expected_baseline_nodes
    expected_baseline_field_evidence = {
        name: {
            field: [expected_baseline_nodes[name][index] for index in indexes]
            for field, indexes in field_indexes["baseline"][name].items()
        }
        for name in scenarios
    }
    expected_baseline_field_evidence["lifecycle_races_stale_result"]["after_replacement"] = [
        structured_probe_id
    ]
    expected_baseline_field_evidence["lifecycle_races_stale_result"]["after_shutdown"] = [
        structured_probe_id
    ]
    expected_baseline_field_evidence["prompt_fallback"]["fallback_order"] = [prompt_order_probe_id]
    expected_baseline_field_evidence["prompt_fallback"]["cerebras"] = [cerebras_probe_id]
    assert baseline_probe["field_evidence"] == expected_baseline_field_evidence
    assert baseline_probe["executed_structured_probes"] == [
        structured_probe_id,
        prompt_order_probe_id,
        cerebras_probe_id,
    ]


def test_current_trace_reproduces_in_an_isolated_subprocess() -> None:
    assert _probe(ROOT) == _json("current_normalized_trace.json")


def test_fixed_archive_ignores_hostile_user_autocrlf_configuration() -> None:
    with tempfile.TemporaryDirectory(prefix="puripuly-hostile-git-config-") as directory:
        root = Path(directory)
        hostile_home = root / "home"
        hostile_home.mkdir()
        (hostile_home / ".gitconfig").write_text(
            "[core]\n\tautocrlf = true\n\teol = crlf\n",
            encoding="utf-8",
        )
        normal_archive = root / "normal.tar"
        hostile_archive = root / "hostile.tar"
        _archive_fixed_source(normal_archive, "957731aa")
        hostile_env = dict(os.environ)
        hostile_env["HOME"] = str(hostile_home)
        hostile_env["USERPROFILE"] = str(hostile_home)
        _archive_fixed_source(hostile_archive, "957731aa", env=hostile_env)

        assert normal_archive.read_bytes() == hostile_archive.read_bytes()


def test_dual_run_provenance_names_fixed_sources_and_reproduced_traces() -> None:
    provenance = _json("dual_run_provenance.json")
    tag_object = subprocess.run(
        ["git", "rev-parse", "v2.2.2"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    expected_tag_object = subprocess.run(
        ["git", "rev-parse", "e2df705"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tag_type = subprocess.run(
        ["git", "cat-file", "-t", tag_object],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    peeled = subprocess.run(
        ["git", "rev-parse", "v2.2.2^{}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert tag_object == expected_tag_object
    assert tag_type == "tag"
    assert peeled == "957731aa87fdc96681004764626a90901a9a670b"
    assert provenance["baseline"]["commit"] == "957731aa87fdc96681004764626a90901a9a670b"
    assert provenance["current"]["commit"] == CURRENT_REPAIR_BASE
    assert provenance["result"] == "reproduced"
    assert provenance["approved_difference_rules"] == "approved_difference_rules.json"
    with tempfile.TemporaryDirectory(prefix="puripuly-fixed-source-") as directory:
        temp_root = Path(directory)
        archive_path = temp_root / "fixed.tar"
        extracted = temp_root / "fixed"
        extracted.mkdir()
        _archive_fixed_source(archive_path, peeled)
        with tarfile.open(archive_path) as archive:
            archive.extractall(extracted, filter="data")
        assert _probe(extracted) == _json("baseline_normalized_golden.json")
        assert _probe(ROOT) == _json("current_normalized_trace.json")
        extracted_path = extracted
    assert not extracted_path.exists()

    repair_manifest = provenance["current"]["repair_manifest"]
    assert provenance["current"]["repair_base"] == CURRENT_REPAIR_BASE
    assert _changed_production_files() == tuple(sorted(repair_manifest["files"]))
    assert (
        _manifest_production_source_digest(repair_manifest["files"])
        == repair_manifest["production_source_sha256"]
    )
    assert (
        _manifest_source_delta_digest(repair_manifest["files"])
        == repair_manifest["source_delta_sha256"]
    )
    assert provenance["sha256"] == {
        "runner": _current_file_digest(Path(__file__).with_name("differential_probe.py")),
        "baseline_trace": _current_file_digest(FIXTURES / "baseline_normalized_golden.json"),
        "current_trace": _current_file_digest(FIXTURES / "current_normalized_trace.json"),
        "difference_rules": _current_file_digest(FIXTURES / "approved_difference_rules.json"),
    }
    assert provenance["scenario_sha256"] == {
        "runner": _current_file_digest(Path(__file__).with_name("observable_scenario_probe.py")),
        "structured_runner": _current_file_digest(
            Path(__file__).with_name("structured_scenario_probe.py")
        ),
        "traces": _current_file_digest(FIXTURES / "observable_scenario_traces.json"),
        "baseline_nodes": _current_file_digest(FIXTURES / "baseline_observable_probe_nodes.json"),
        "current_nodes": _current_file_digest(FIXTURES / "current_observable_probe_nodes.json"),
        "field_node_indexes": _current_file_digest(FIXTURES / "observable_field_node_indexes.json"),
    }
    skip_report = _json("deterministic_environment_and_skips.json")
    assert skip_report["skip_classes"]["unknown"]["count"] == 0
    assert sum(item["count"] for item in skip_report["skip_classes"].values()) == 33
