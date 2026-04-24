# Verification: OpenRouter PKCE exchanged key verification

- Verification level: L1
- Scope:
  - Confirm the OpenRouter PKCE-exchanged API key is actually verified before success UI.
  - Prevent storing/applying an exchanged key if verification fails.
  - Rebuild the OpenRouter LLM provider with the new secret even when the provider signature is unchanged.
  - Persist `api_key_verified.openrouter = True` after real PKCE apply so Settings/Dashboard do not require manual focus/blur verification.
  - Preserve existing secret/settings rollback after later apply failure.

## Root cause

- `GuiController.connect_openrouter_via_pkce()` treated a successful PKCE key exchange as equivalent to an API-key verification.
- It stored the returned key, set `api_key_verified.openrouter = True`, applied OpenRouter BYOK settings, and allowed the app success snackbar without calling `OpenRouterLLMProvider.verify_api_key()`.
- If settings were already on the same OpenRouter BYOK provider signature, `apply_providers()` skipped `_rebuild_llm_provider()` because the secret changed but the provider signature did not.
- `apply_providers()` merges provider/prompt fields and did not persist the PKCE-updated `api_key_verified.openrouter` flag, so later UI sync could still look unverified.

## TDD evidence

- RED: `test_connect_openrouter_via_pkce_stores_key_sets_alias_and_marks_verified`
  - Failed because no verification call was made for the exchanged key.
- RED: `test_connect_openrouter_via_pkce_rejects_unverified_exchanged_key`
  - Failed because verification failure was not possible; the method returned success and applied settings.
- RED: `test_connect_openrouter_via_pkce_rebuilds_llm_when_signature_is_unchanged`
  - Failed because same-signature PKCE success did not rebuild the LLM provider with the newly stored key.
  - After review, also covered persisting `controller.settings.api_key_verified.openrouter is True` after a real apply.

## Commands run

```powershell
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_stores_key_sets_alias_and_marks_verified tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rejects_unverified_exchanged_key -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/ui/test_controller_branch_paths.py::test_create_openrouter_pkce_client_uses_openrouter_documented_localhost_port tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_stores_key_sets_alias_and_marks_verified tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rejects_unverified_exchanged_key tests/ui/test_controller_branch_paths.py::test_reopen_openrouter_pkce_authorization_url_delegates_to_active_client tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_leaves_settings_unchanged_on_failure tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_reopens_letter_context_on_letter_failure tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rolls_back_secret_and_settings_on_apply_failure -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rebuilds_llm_when_signature_is_unchanged -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_stores_key_sets_alias_and_marks_verified tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rejects_unverified_exchanged_key tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rebuilds_llm_when_signature_is_unchanged tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rolls_back_secret_and_settings_on_apply_failure -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/core/test_openrouter_pkce.py tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_uses_settings_mutation_queue tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_reopens_existing_auth_url_while_flow_active tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_ignores_duplicate_while_flow_active tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_uses_draft_preserving_refresh_on_success tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_does_not_refresh_settings_view_on_failure tests/ui/test_controller_branch_paths.py::test_create_openrouter_pkce_client_uses_openrouter_documented_localhost_port tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_stores_key_sets_alias_and_marks_verified tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rejects_unverified_exchanged_key tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rebuilds_llm_when_signature_is_unchanged tests/ui/test_controller_branch_paths.py::test_reopen_openrouter_pkce_authorization_url_delegates_to_active_client tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_leaves_settings_unchanged_on_failure tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_reopens_letter_context_on_letter_failure tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rolls_back_secret_and_settings_on_apply_failure tests/ui/test_settings_view_branches.py -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m ruff check src/puripuly_heart/ui/controller.py tests/ui/test_controller_branch_paths.py
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m black --check src/puripuly_heart/ui/controller.py tests/ui/test_controller_branch_paths.py
```

## Outcome

- Focused RED tests passed after implementation.
- Same-signature forced rebuild and verified-state persistence regression passed after implementation.
- Targeted PKCE/Auth/Settings suite: PASS.
- Ruff: PASS.
- Black check: PASS.
- Independent final review: no Critical/Important/Minor issues; ready.

## Skips

- No live OpenRouter browser PKCE run was performed.
