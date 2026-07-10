from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROPOSAL_PATH = (
    REPO_ROOT
    / "tests"
    / "compatibility"
    / "fixtures"
    / "vnext_architecture_classification_proposal.json"
)


def _proposal() -> dict[str, object]:
    return json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))


def test_classification_proposal_freezes_sources_and_comparison_identities() -> None:
    proposal = _proposal()
    metadata = proposal["metadata"]

    assert proposal["artifact_state"] == "approved_classification_oracle"
    assert metadata["bundle"]["sha256"] == (
        "665fe63ac999534f046b1d10b1e1a57f0faff6c5040d5cbdb21a163a5e28e80f"
    )
    assert metadata["source"]["sha256"] == (
        "ef5a96c656ba684589ced4abda2f2aafda417ee2b229e6b8afec759d6d736705"
    )
    assert metadata["baseline"]["commit"] == "957731aa87fdc96681004764626a90901a9a670b"
    assert metadata["current"]["commit"] == "b4ace2631793aa1a1e384c0440b8ec7f315559ae"
    assert metadata["current"]["post_claim_commits_explicitly_included"] == [
        "05d496c",
        "b4ace26",
    ]


def test_classification_oracle_records_every_user_decision_without_unresolved_rows() -> None:
    proposal = _proposal()
    policy = proposal["classification_policy"]
    classified = proposal["classified_behavior_rows"]

    assert policy["all_rows_have_user_authority"] is True
    assert (
        policy["row_recommendation_fields_are_superseded_by_final_user_classification_map"] is True
    )
    assert policy["release_ready_requires_approval"] is False
    assert policy["unresolved_blocks_oracle_acceptance"] is True
    expected_pending_ids = [
        *(f"P-{index:03d}" for index in range(1, 8)),
        *(f"P-{index:03d}" for index in range(9, 23)),
        *(f"P-{index:03d}" for index in range(24, 28)),
        *(f"P-{index:03d}" for index in range(29, 32)),
        *(f"P-{index:03d}" for index in range(33, 39)),
        *(f"P-{index:03d}" for index in range(40, 43)),
        "P-047",
    ]
    assert "pending_item_review" not in proposal
    assert len(classified) == 38
    assert [entry["id"] for entry in classified] == expected_pending_ids
    assert len({entry["id"] for entry in classified}) == len(classified)
    assert all(entry["available_classifications"] == policy["allowed"] for entry in classified)
    assert all(entry["baseline_behavior"] for entry in classified)
    assert all(entry["current_behavior"] for entry in classified)
    assert all(entry["user_impact"] for entry in classified)
    assert all(entry["direct_authority"] for entry in classified)
    assert all(entry["evidence"] for entry in classified)
    final_map = proposal["final_user_classification_map"]
    assert set(final_map) == {"preserved", "removed", "intentionally_changed"}
    assert {item_id for item_ids in final_map.values() for item_id in item_ids} == set(
        expected_pending_ids
    )
    assert sum(len(item_ids) for item_ids in final_map.values()) == len(expected_pending_ids)
    assert proposal["final_user_decision_authority"]["authority"]
    assert final_map == {
        "preserved": ["P-003", "P-005", "P-010", "P-012", "P-017", "P-035"],
        "removed": ["P-007", "P-009", "P-036", "P-037", "P-038"],
        "intentionally_changed": [
            "P-001",
            "P-002",
            "P-004",
            "P-006",
            "P-011",
            "P-013",
            "P-014",
            "P-015",
            "P-016",
            "P-018",
            "P-019",
            "P-020",
            "P-021",
            "P-022",
            "P-024",
            "P-025",
            "P-026",
            "P-027",
            "P-029",
            "P-030",
            "P-031",
            "P-033",
            "P-034",
            "P-040",
            "P-041",
            "P-042",
            "P-047",
        ],
    }
    repairs = proposal["final_user_decision_authority"]["repairs"]
    assert "64-lowercase-hex" in repairs["P-003"]
    assert "primary error or timeout" in repairs["P-004"]
    assert "supersede" in repairs["P-007"]
    assert "Intentionally change fixed close drain/finalization" in repairs["P-020"]
    assert "Cloudflare and Wafer" in repairs["P-042"]
    _assert_approved_rows_have_direct_authority()


def _assert_approved_rows_have_direct_authority() -> None:
    proposal = _proposal()

    approved = proposal["approved_classifications"]
    assert [entry["id"] for entry in approved] == [f"A-{index:03d}" for index in range(1, 10)]
    assert all(entry["authority"] for entry in approved)
    assert "adr-2026-06-11-peer-utterance-chatbox-hard-deny.md" in approved[1]["authority"]
    assert "vnext-desktop-subtitle-overlay-flicker-free-updates.md" in approved[2]["authority"]
    assert "soniox-multilingual-peer-auto-detection.yaml" in approved[4]["authority"]
    assert approved[5]["classification"] == "preserved"
    assert approved[6]["classification"] == "preserved"
    assert "soniox-multilingual-peer-auto-detection.yaml:109-173" in approved[7]["authority"]
    assert approved[4]["surface"] == "Soniox multilingual peer auto-detection runtime"
    assert approved[7]["surface"] == (
        "Peer Soniox automatic-detection function, optional hints, manual fallback, and locale parity"
    )
    assert "does not approve 05d496c wording" in approved[7]["rationale"]
    assert approved[8]["surface"] == (
        "OpenRouter Gemma 4 and Cerebras Gemma fallback-picker availability"
    )
    assert approved[8]["classification"] == "preserved"
    pending_by_id = {entry["id"]: entry for entry in proposal["classified_behavior_rows"]}
    assert pending_by_id["P-042"]["surface"] == "Gemma 4 OpenRouter provider routing"
    assert "presence only" in pending_by_id["P-042"]["direct_authority"]
    assert proposal["metadata"]["current"]["commit"].startswith("b4ace26")


def test_classification_proposal_covers_mandatory_surfaces_and_baseline_failures() -> None:
    proposal = _proposal()
    mandatory = proposal["mandatory_preserved_surfaces"]
    failures = proposal["baseline_execution"]["failures"]

    assert [entry["id"] for entry in mandatory] == [f"M-{index:03d}" for index in range(1, 12)]
    assert all(entry["classification"] == "preserved" for entry in mandatory)
    assert mandatory[0]["surface"] == "historical settings loading, serialization, and migration"
    assert "defaults" not in mandatory[0]["surface"]
    assert mandatory[2]["surface"] == "authorized SecretStore keys and environment fallback"
    assert "encrypted-file format" not in mandatory[2]["surface"]
    assert mandatory[8]["surface"] == "installer production identity"
    assert mandatory[10]["surface"] == "isolated alternate-AppId installer smoke safety"
    assert [entry["id"] for entry in failures] == [f"B-{index:03d}" for index in range(1, 8)]
    assert all(entry["product_decision_required"] is False for entry in failures)
    assert "S1:R-002 and D-001" in failures[0]["technical_disposition"]
    assert "Internal test fixture defect" in failures[1]["technical_disposition"]
    assert "Technical unused-key drift" in failures[6]["technical_disposition"]
    _assert_required_direct_comparisons_and_atomic_splits()
    _assert_internal_overlay_ownership_exclusion()
    _assert_execution_counts_and_local_provenance()


def _assert_required_direct_comparisons_and_atomic_splits() -> None:
    proposal = _proposal()
    pending = {entry["id"]: entry for entry in proposal["classified_behavior_rows"]}
    expected_surfaces = {
        "P-003": "QQ credential format validation",
        "P-005": "Fallback trigger semantics after primary failure versus deadline",
        "P-007": "Qwen 3.5 Flash fallback choice",
        "P-010": "First-run primary provider and fallback defaults",
        "P-011": "Shared translation_prompt.md policy content",
        "P-012": "Cerebras provider-specific prompt routing",
        "P-036": "run-stdin and run-mic CLI commands",
        "P-037": "osc-send CLI command",
        "P-038": "puripuly_heart.app.headless_mic and headless_stdin import facades",
        "P-040": "05d496c Soniox labels, descriptions, and language-hint editor presentation",
        "P-047": "About and README Special Thanks addition for ~ eri ~",
    }

    assert {item_id: pending[item_id]["surface"] for item_id in expected_surfaces} == (
        expected_surfaces
    )
    assert pending["P-007"]["recommendation"] == "preserved"
    assert "explicitly retains qwen35_flash" in pending["P-007"]["direct_authority"]
    assert (
        "Managed OpenRouter Gemma 4 primary plus OpenRouter DeepSeek fallback"
        in pending["P-010"]["baseline_behavior"]
    )
    assert "OpenRouter DeepSeek V4 Flash fallback" in pending["P-010"]["current_behavior"]
    assert "OpenRouter Gemma fallback" in pending["P-010"]["current_behavior"]
    assert "remain unchanged" in pending["P-010"]["user_impact"]
    assert pending["P-005"]["recommendation"] == "preserved"
    assert "primary error or the timeout" in pending["P-005"]["current_behavior"]
    assert "understates the primary-error trigger" in pending["P-005"]["user_impact"]
    assert pending["P-004"]["surface"] == "Fallback selection labels and helper copy"
    assert "not themselves approved" in pending["P-040"]["user_impact"]
    assert pending["P-041"]["surface"] == "b4ace26 recent-language retention after hint removal"
    assert "P-008" not in pending
    assert "P-046" not in pending


def _assert_internal_overlay_ownership_exclusion() -> None:
    proposal = _proposal()
    exclusions = proposal["reviewed_non_observable_exclusions"]

    assert [entry["id"] for entry in exclusions] == ["X-001", "X-002", "X-003"]
    assert "P-027" in exclusions[0]["exclusion_rationale"]
    assert "A-003" in exclusions[0]["exclusion_rationale"]
    assert "A-004" in exclusions[0]["exclusion_rationale"]
    assert "no current observable timing difference" in exclusions[1]["exclusion_rationale"]
    assert (
        "Both fixed and current production paths render no watermark"
        in exclusions[2]["exclusion_rationale"]
    )
    accounting = proposal["completeness_accounting"]
    assert accounting == {
        "approved_external_classifications": 9,
        "mandatory_preserved_surface_groups": 11,
        "approved_atomic_external_decisions": 38,
        "reviewed_non_observable_exclusions": 3,
        "reviewed_authority_conflicts": 1,
        "baseline_execution_failures_dispositioned": 7,
        "scope_notes": accounting["scope_notes"],
    }
    assert len(accounting["scope_notes"]) == 3
    conflicts = proposal["reviewed_authority_conflicts"]
    assert [entry["id"] for entry in conflicts] == ["C-001"]
    assert "both fixed 957731aa and current b4ace26" in conflicts[0]["conflict"]
    assert "Not a fixed-to-current behavior difference" in conflicts[0]["compatibility_disposition"]


def _assert_execution_counts_and_local_provenance() -> None:
    proposal = _proposal()
    baseline = proposal["baseline_execution"]["result"]
    current = proposal["deterministic_current_evidence"]["result"]

    assert baseline == {"tests": 3180, "passed": 3164, "failed": 7, "skipped": 9}
    assert sum(baseline[key] for key in ("passed", "failed", "skipped")) == baseline["tests"]
    assert current == {"tests": 3992, "passed": 3959, "failed": 0, "skipped": 33}
    assert sum(current[key] for key in ("passed", "failed", "skipped")) == current["tests"]
    assert proposal["baseline_provenance"]["local_junit"] == (
        ".data/iw-evidence/baseline-junit.xml"
    )
    assert proposal["baseline_provenance"]["tracked_self_description"]


def test_approved_discord_copy_matches_released_semantics_in_every_locale() -> None:
    expected_fragments = {
        "en": "You can translate 700+ times.",
        "ko": "700회 이상 번역할 수 있어요.",
        "ja": "700回以上翻訳できます。",
        "zh-CN": "可翻译 700 次以上。",
    }

    for locale, fragment in expected_fragments.items():
        path = REPO_ROOT / "src" / "puripuly_heart" / "data" / "i18n" / f"{locale}.json"
        bundle = json.loads(path.read_text(encoding="utf-8"))
        assert fragment in bundle["discord_auth.body"]
        assert "600" not in bundle["discord_auth.body"]
