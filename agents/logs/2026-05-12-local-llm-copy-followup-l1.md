# Local LLM Copy Follow-up Verification

## Verification level

L1 - focused i18n/UI copy regression checks plus lint for touched Python tests/UI files.

## Commands run

- `.venv\Scripts\python.exe -m pytest tests/ui/test_i18n_key_usage.py::test_local_llm_keys_are_localized tests/ui/test_settings_view_branches.py::test_local_llm_connection_card_matches_api_field_scale_and_copy tests/ui/test_settings_view_branches.py::test_apply_locale_refreshes_local_llm_api_key_copy -q`
- `.venv\Scripts\python.exe -m pytest tests/ui/test_i18n_key_usage.py tests/ui/test_settings_view_branches.py::test_local_llm_connection_card_matches_api_field_scale_and_copy tests/ui/test_settings_view_branches.py::test_apply_locale_refreshes_local_llm_api_key_copy -q`
- `.venv\Scripts\python.exe -m ruff check src/puripuly_heart/ui/views/settings.py tests/ui/test_i18n_key_usage.py tests/ui/test_settings_view_branches.py`

## Outcome

- PASS: focused copy tests exited 0.
- PASS: broader i18n copy tests exited 0.
- PASS: ruff exited 0 with `All checks passed!`.

## Skipped items

- Full app/provider suites were not rerun because this follow-up only changes Local LLM UI copy and helper visibility.

## Notes

- Local LLM API-key helper copy is intentionally blank and hidden.
- Korean Local LLM extra-body helper now uses `낮은 지연시간` wording.
- Local LLM default model ID remains `llama3.1:8b` from `LocalLLMSettings.model`.
