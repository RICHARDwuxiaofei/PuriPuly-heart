from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BASELINE_NODES = {
    "self_peer_utterance_segment_flow": [
        "tests/core/test_hub_branch_coverage.py::test_handle_stt_event_routes_non_low_latency_events",
        "tests/core/test_peer_channel_routing.py::test_peer_desktop_transcripts_are_routed_to_peer_runtime_and_never_sent_to_chatbox",
    ],
    "translation_disabled_transcript_fallback_completion": [
        "tests/core/test_peer_channel_routing.py::test_peer_translation_respects_master_translation_toggle",
        "tests/core/test_hub_branch_coverage.py::test_translate_and_enqueue_emits_error_and_fallback_transcript",
        "tests/core/test_hub_branch_coverage.py::test_submit_text_validates_input_and_enqueues_without_llm",
    ],
    "provider_timeout_non_success_invalid_response": [
        "tests/providers/test_deepgram_session.py::test_deepgram_session_start_timeout",
        "tests/providers/test_cerebras_provider.py::test_httpx_cerebras_client_translate_raises_on_non_200",
        "tests/providers/test_cerebras_provider.py::test_httpx_cerebras_client_translate_raises_on_length_finish_reason",
    ],
    "stt_toggle_off_restart": [
        "tests/core/test_stt_controller.py::test_stt_controller_finalize_on_close_while_speaking"
    ],
    "osc_typing_enqueue_send": [
        "tests/ui/test_controller_manual_typing.py::test_manual_submit_keeps_typing_visible_until_submit_finishes",
        "tests/core/test_chatbox_paginator.py::test_short_message_sends_immediately_without_cooldown",
    ],
    "output_routes": [
        "tests/core/test_peer_channel_routing.py::test_peer_desktop_transcripts_are_routed_to_peer_runtime_and_never_sent_to_chatbox",
        "tests/core/test_overlay_presenter.py::test_presenter_peer_translation_final_with_source_text_publishes_paired_row",
        "tests/core/test_hub_branch_coverage.py::test_peer_translation_disclosure_enqueues_chatbox_notice_without_context_history",
    ],
    "ui_projection": [
        "tests/core/test_peer_channel_routing.py::test_peer_desktop_transcripts_are_routed_to_peer_runtime_and_never_sent_to_chatbox",
        "tests/ui/test_event_bridge.py::test_event_bridge_routes_translation_and_osc_history_by_language_mode",
        "tests/ui/test_event_bridge.py::test_event_bridge_handles_error_and_soniox_shutdown_suppression",
    ],
    "safe_diagnostics": [
        "tests/ui/test_event_bridge.py::test_event_bridge_error_without_runtime_logging_uses_standard_logger_only"
    ],
    "settings_persistence": [
        "tests/config/test_config_and_secrets.py::test_load_settings_schema_migration_writes_pre_migration_backup",
        "tests/config/test_first_run_locale.py::test_first_run_settings_roundtrip_through_dict_serialization",
        "tests/config/test_config_and_secrets.py::test_telemetry_roundtrip_and_malformed_values_repair",
        "tests/config/test_config_and_secrets.py::test_encrypted_file_secret_store_does_not_store_plaintext",
    ],
    "provider_replacement_close": [
        "tests/core/test_hub_branch_coverage.py::test_replace_stt_provider_running_restarts_event_loop_and_clears_runtime_state"
    ],
    "lifecycle_races_stale_result": [
        "tests/core/test_hub_branch_coverage.py::test_hub_drops_stale_partial_and_keeps_final_order",
        "tests/core/test_hub_branch_coverage.py::test_stop_cancels_pending_tasks_and_closes_providers",
    ],
    "managed_auth_ack_retry": [
        "tests/core/test_managed_openrouter_release.py::test_discord_issue_delivery_ack_failure_keeps_pending_retry_state"
    ],
    "overlay_disconnect_reconnect_target": [
        "tests/core/test_overlay_bridge.py::test_overlay_bridge_swallows_authenticated_disconnect_without_close_frame",
        "tests/core/test_overlay_bridge.py::test_overlay_bridge_sends_authenticated_initial_snapshot",
        "tests/core/test_overlay_bridge.py::test_overlay_bridge_desktop_runtime_control_is_target_gated_from_steamvr_path",
        "tests/ui/test_controller_branch_paths.py::test_overlay_target_routing_apply_settings_stops_before_switching_running_target",
        "tests/core/test_overlay_bridge.py::test_overlay_bridge_resets_one_time_token_after_stop_and_restart",
    ],
    "prompt_fallback": [
        "tests/config/test_prompt_loader.py::test_load_prompt_falls_back_to_default",
        "tests/config/test_prompt_loader.py::test_load_prompt_for_llm_providers_uses_shared_translation_prompt",
    ],
    "context_memory": [
        "tests/core/test_context_memory.py::TestContextFiltering::test_context_filters_by_time_window",
        "tests/core/test_context_memory.py::TestContextFiltering::test_context_filters_by_max_entries",
        "tests/core/test_peer_channel_routing.py::test_integrated_context_always_includes_peer_entries",
    ],
}

CURRENT_NODES = {
    **BASELINE_NODES,
    "self_peer_utterance_segment_flow": [
        "tests/core/test_channel_runtime.py::test_peer_transcript_stays_in_peer_runtime",
        "tests/core/test_hub_overlay_streaming.py::test_peer_no_chatbox_terminal_path_clears_latency_bookkeeping",
        "tests/core/test_hub_branch_coverage.py::test_hub_drops_stale_partial_and_keeps_final_order",
    ],
    "translation_disabled_transcript_fallback_completion": [
        "tests/core/test_hub_overlay_streaming.py::test_peer_translation_disabled_finalizes_source_only_turn",
        "tests/core/test_hub_branch_coverage.py::test_translate_and_enqueue_emits_error_and_fallback_transcript",
        "tests/core/test_hub_branch_coverage.py::test_submit_text_validates_input_and_enqueues_without_llm",
    ],
    "provider_timeout_non_success_invalid_response": [
        "tests/providers/test_deepgram_session.py::test_deepgram_session_start_timeout",
        "tests/providers/test_cerebras_provider.py::test_httpx_cerebras_client_translate_raises_safely_on_non_200",
        "tests/providers/test_cerebras_provider.py::test_httpx_cerebras_client_translate_raises_on_empty_response",
    ],
    "stt_toggle_off_restart": [
        "tests/core/test_stt_controller.py::test_toggle_off_cancels_real_controller_without_finalization_and_restarts"
    ],
    "osc_typing_enqueue_send": [
        "tests/ui/test_controller_manual_typing.py::test_submit_typing_is_generation_safe_and_clears_after_success",
        "tests/core/test_chatbox_paginator.py::test_short_message_sends_immediately_without_cooldown",
    ],
    "output_routes": [
        "tests/core/output/test_router.py::test_router_routes_peer_subtitles_to_overlay_and_denies_chatbox_attempt_safely",
        "tests/core/output/test_router.py::test_router_publishes_system_disclosures_without_transcript_fields",
    ],
    "ui_projection": [
        "tests/core/test_channel_contracts.py::test_ui_and_stt_events_can_reference_self_or_peer_channels",
        "tests/ui/test_event_bridge.py::test_event_bridge_routes_translation_and_osc_history_by_language_mode",
        "tests/ui/test_event_bridge.py::test_event_bridge_handles_error_and_soniox_shutdown_suppression",
    ],
    "safe_diagnostics": [
        "tests/core/test_diagnostic_validator_contract.py::test_text_redactor_removes_raw_transcript_translation_source_assignments"
    ],
    "settings_persistence": [
        "tests/config/test_settings_vnext_migration_serialization.py::test_migration_on_load_creates_byte_identical_backup_and_writes_vnext_with_collision",
        "tests/config/test_settings_migration_fixtures.py::test_maximal_v24_fixture_round_trip_retains_explicit_stable_fields",
        "tests/app/test_openrouter_pkce_handoff.py::test_success_preserves_existing_nested_operational_state_payloads",
        "tests/config/test_config_and_secrets.py::test_local_llm_api_key_is_not_serialized_in_settings",
    ],
    "provider_replacement_close": [
        "tests/core/test_provider_runtime_handle.py::test_replace_provider_starts_new_ingress_when_old_close_fails"
    ],
    "lifecycle_races_stale_result": [
        "tests/core/test_provider_runtime_handle.py::test_replace_provider_starts_new_ingress_when_old_close_fails",
        "tests/core/test_hub_branch_coverage.py::test_stop_cancels_pending_tasks_and_closes_providers",
    ],
    "managed_auth_ack_retry": [
        "tests/app/test_managed_connection_auth.py::test_delivery_ack_pending_metadata_persists_before_ack_and_clears_after_success",
        "tests/app/test_qq_managed_auth.py::test_qq_managed_auth_ack_failure_leaves_pending_metadata_and_token",
    ],
    "overlay_disconnect_reconnect_target": [
        "tests/core/test_overlay_bridge.py::test_overlay_bridge_swallows_authenticated_disconnect_without_close_frame",
        "tests/core/test_overlay_bridge.py::test_overlay_bridge_sends_authenticated_initial_snapshot",
        "tests/core/test_overlay_bridge.py::test_overlay_bridge_desktop_runtime_control_is_target_gated_from_steamvr_path",
        "tests/app/services/test_overlay_lifecycle_engine.py::test_overlay_target_routing_apply_settings_stops_before_switching_running_target",
        "tests/core/test_overlay_bridge.py::test_overlay_bridge_resets_one_time_token_after_stop_and_restart",
    ],
    "prompt_fallback": [
        "tests/config/test_prompt_loader.py::test_load_prompt_falls_back_to_default",
        "tests/config/test_prompt_loader.py::test_load_prompt_for_llm_providers_uses_shared_translation_prompt",
    ],
    "context_memory": [
        "tests/core/test_context_memory.py::TestContextFiltering::test_context_filters_by_time_window",
        "tests/core/test_context_memory.py::TestContextFiltering::test_context_filters_by_max_entries",
        "tests/core/test_peer_channel_routing.py::test_integrated_context_always_includes_peer_entries",
    ],
}

BASELINE_OUTCOMES = {
    "self_peer_utterance_segment_flow": {
        "self_terminal": "single",
        "peer_terminal": "single",
        "self_peer_mixed": False,
    },
    "translation_disabled_transcript_fallback_completion": {
        "translation_disabled": "source_only_terminal",
        "transcript_only_fallback": "source_enqueued",
        "completion": "single_terminal",
    },
    "provider_timeout_non_success_invalid_response": {
        "timeout": "safe_failure",
        "non_success": "safe_failure",
        "invalid_response": "rejected",
    },
    "stt_toggle_off_restart": {"close": "awaited_drain", "drain": True, "finalization": True},
    "osc_typing_enqueue_send": {
        "typing": "cleared_after_completion",
        "enqueue": "accepted",
        "send": "terminal_sent",
    },
    "output_routes": {
        "peer_chatbox": "implicit_not_published",
        "peer_overlay": "published",
        "system_disclosure": "general_route",
    },
    "ui_projection": {"translation": "projected", "error": "projected", "channel": "preserved"},
    "safe_diagnostics": {"raw_error_visible": True},
    "settings_persistence": {
        "historical_settings": "migrated_with_backup",
        "operational_state": "roundtrip_preserved",
        "secret_material": False,
    },
    "provider_replacement_close": {
        "replacement": "active",
        "old_close": "awaited",
        "old_resource_alive": False,
    },
    "lifecycle_races_stale_result": {
        "after_replacement": "rejected",
        "after_shutdown": "rejected",
        "final_order": "preserved",
    },
    "managed_auth_ack_retry": {"ack": "retryable", "pending_state": "preserved"},
    "overlay_disconnect_reconnect_target": {
        "disconnect": "clean",
        "restored_snapshot": "replayed",
        "target_transition": "steamvr_to_desktop_gated",
    },
    "prompt_fallback": {
        "fallback_order": "name_md_name_txt_default_md_default_txt",
        "cerebras": "shared_translation_prompt",
    },
    "context_memory": {"bounded_context": "passed", "channel_mode": "resolved"},
}

CURRENT_OUTCOMES = {
    **BASELINE_OUTCOMES,
    "stt_toggle_off_restart": {"close": "awaited_immediate", "drain": False, "finalization": False},
    "output_routes": {
        "peer_chatbox": "explicitly_denied",
        "peer_overlay": "published",
        "system_disclosure": "safe_dedicated_route",
    },
    "safe_diagnostics": {"raw_error_visible": False},
}


def _environment() -> dict[str, str]:
    return {
        **{
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
        },
        "PYTHONNOUSERSITE": "1",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source", choices=("baseline", "current"), required=True)
    args = parser.parse_args()
    nodes = BASELINE_NODES if args.source == "baseline" else CURRENT_NODES
    ordered_nodes = [node for scenario_nodes in nodes.values() for node in scenario_nodes]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *ordered_nodes],
        cwd=args.source_root,
        capture_output=True,
        text=True,
        env=_environment(),
    )
    if completed.returncode:
        print(completed.stdout + completed.stderr, file=sys.stderr)
        return completed.returncode
    outcomes = BASELINE_OUTCOMES if args.source == "baseline" else CURRENT_OUTCOMES
    structured = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("structured_scenario_probe.py")),
            "--source-root",
            str(args.source_root),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_environment(),
    )
    structured_values = json.loads(structured.stdout)
    outcomes = json.loads(json.dumps(outcomes))
    outcomes["lifecycle_races_stale_result"].update(
        structured_values["lifecycle_races_stale_result"]
    )
    outcomes["prompt_fallback"].update(structured_values["prompt_fallback"])
    index_fixture = json.loads(
        (Path(__file__).with_name("fixtures") / "observable_field_node_indexes.json").read_text(
            encoding="utf-8"
        )
    )[args.source]
    field_evidence = {
        scenario: {
            field: [scenario_nodes[index] for index in index_fixture[scenario][field]]
            for field in outcomes[scenario]
        }
        for scenario, scenario_nodes in nodes.items()
    }
    structured_probe_id = (
        "structured_scenario_probe.py::stale_completion_after_replacement_and_shutdown"
    )
    prompt_order_probe_id = "structured_scenario_probe.py::prompt_fallback_order"
    cerebras_probe_id = "structured_scenario_probe.py::cerebras_shared_routing"
    field_evidence["lifecycle_races_stale_result"]["after_replacement"] = [structured_probe_id]
    field_evidence["lifecycle_races_stale_result"]["after_shutdown"] = [structured_probe_id]
    field_evidence["prompt_fallback"]["fallback_order"] = [prompt_order_probe_id]
    field_evidence["prompt_fallback"]["cerebras"] = [cerebras_probe_id]
    if any(
        node not in {*ordered_nodes, structured_probe_id, prompt_order_probe_id, cerebras_probe_id}
        for scenario in field_evidence.values()
        for field_nodes in scenario.values()
        for node in field_nodes
    ):
        raise AssertionError("field evidence references an unexecuted source node")
    print(
        json.dumps(
            {
                "executed_nodes": nodes,
                "executed_structured_probes": [
                    structured_probe_id,
                    prompt_order_probe_id,
                    cerebras_probe_id,
                ],
                "field_evidence": field_evidence,
                "scenarios": outcomes,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
