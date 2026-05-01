# Verification: OpenRouter PKCE repeated click and success feedback

- Verification level: L1
- Scope:
  - Repeated clicks during an active PKCE flow reopen the active authorization URL instead of silently doing nothing.
  - Successful PKCE connection shows a visible success snackbar.
  - OpenRouter auth button uses brown default/disabled text, primary hover text, and authenticated/disabled state after verified BYOK key.
  - Clearing or failed manual verification of the OpenRouter BYOK secret returns the auth button to authenticate/clickable state.
  - Existing PKCE failure, rollback, and duplicate-listener prevention paths remain covered.

## Root cause

- `_openrouter_pkce_request_active` was set before the browser flow started and remained true until success/failure/timeout.
- If the user closed the browser page before the localhost callback completed, the app continued waiting for the callback and ignored later button clicks.

## TDD evidence

- RED: `test_reopen_authorization_url_reopens_current_pkce_session`
  - Failed because `OpenRouterPKCEClient` had no URL reopen API.
- RED: `test_reopen_openrouter_pkce_authorization_url_delegates_to_active_client`
  - Failed because `GuiController` had no active PKCE client reference/reopen method.
- RED: `test_on_request_openrouter_pkce_reopens_existing_auth_url_while_flow_active`
  - Failed because active clicks were silently ignored.
- RED: `test_on_request_openrouter_pkce_uses_draft_preserving_refresh_on_success`
  - Failed because success snackbar was not emitted.
- RED: `test_openrouter_key_field_and_pkce_button_are_visible_for_byok_without_break_glass`
  - Failed because the PKCE auth button default color was primary, then neutral, instead of brown.
- RED: `test_openrouter_pkce_button_shows_authenticated_state_after_verified_key`
  - Failed because a verified OpenRouter BYOK key did not disable/relabel the auth button.
- RED: `test_openrouter_pkce_button_returns_to_authenticate_when_key_is_cleared`
  - Covered clearing `openrouter_api_key` and reverting the auth button state.
- RED: `test_openrouter_pkce_button_disables_after_manual_key_verification`
  - Failed because manual verification success did not live-sync the auth button state.
- RED: `test_openrouter_pkce_button_reenables_after_manual_key_verification_failure`
  - Failed because manual verification failure did not live-sync the auth button state.

## Commands run

```powershell
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/core/test_openrouter_pkce.py::test_reopen_authorization_url_reopens_current_pkce_session tests/ui/test_controller_branch_paths.py::test_reopen_openrouter_pkce_authorization_url_delegates_to_active_client tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_reopens_existing_auth_url_while_flow_active tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_uses_draft_preserving_refresh_on_success -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/ui/test_settings_view_branches.py::test_openrouter_key_field_and_pkce_button_are_visible_for_byok_without_break_glass tests/ui/test_settings_view_branches.py::test_openrouter_pkce_button_shows_authenticated_state_after_verified_key tests/ui/test_settings_view_branches.py::test_openrouter_pkce_button_returns_to_authenticate_when_key_is_cleared tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_uses_draft_preserving_refresh_on_success -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/ui/test_settings_view_branches.py::test_openrouter_pkce_button_disables_after_manual_key_verification tests/ui/test_settings_view_branches.py::test_openrouter_pkce_button_reenables_after_manual_key_verification_failure -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/ui/test_settings_view_branches.py::test_openrouter_key_field_and_pkce_button_are_visible_for_byok_without_break_glass tests/ui/test_settings_view_branches.py::test_openrouter_pkce_button_shows_authenticated_state_after_verified_key tests/ui/test_settings_view_branches.py::test_openrouter_pkce_button_returns_to_authenticate_when_key_is_cleared -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/core/test_openrouter_pkce.py tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_uses_settings_mutation_queue tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_reopens_existing_auth_url_while_flow_active tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_ignores_duplicate_while_flow_active tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_uses_draft_preserving_refresh_on_success tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_does_not_refresh_settings_view_on_failure tests/ui/test_controller_branch_paths.py::test_create_openrouter_pkce_client_uses_openrouter_documented_localhost_port tests/ui/test_controller_branch_paths.py::test_reopen_openrouter_pkce_authorization_url_delegates_to_active_client tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_stores_key_sets_alias_and_marks_verified tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_leaves_settings_unchanged_on_failure tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_reopens_letter_context_on_letter_failure tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rolls_back_secret_and_settings_on_apply_failure tests/ui/test_settings_view_branches.py::test_openrouter_pkce_button_requests_auth_for_current_byok_selection -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/core/test_openrouter_pkce.py tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_uses_settings_mutation_queue tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_reopens_existing_auth_url_while_flow_active tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_ignores_duplicate_while_flow_active tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_uses_draft_preserving_refresh_on_success tests/ui/test_app_branches.py::test_on_request_openrouter_pkce_does_not_refresh_settings_view_on_failure tests/ui/test_controller_branch_paths.py::test_create_openrouter_pkce_client_uses_openrouter_documented_localhost_port tests/ui/test_controller_branch_paths.py::test_reopen_openrouter_pkce_authorization_url_delegates_to_active_client tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_stores_key_sets_alias_and_marks_verified tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_leaves_settings_unchanged_on_failure tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_reopens_letter_context_on_letter_failure tests/ui/test_controller_branch_paths.py::test_connect_openrouter_via_pkce_rolls_back_secret_and_settings_on_apply_failure tests/ui/test_settings_view_branches.py -q
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m ruff check src/puripuly_heart/core/openrouter_pkce.py src/puripuly_heart/ui/app.py src/puripuly_heart/ui/controller.py src/puripuly_heart/ui/views/settings.py src/puripuly_heart/data/i18n/en.json src/puripuly_heart/data/i18n/ko.json src/puripuly_heart/data/i18n/zh-CN.json tests/core/test_openrouter_pkce.py tests/ui/test_app_branches.py tests/ui/test_controller_branch_paths.py tests/ui/test_settings_view_branches.py
C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m black --check src/puripuly_heart/core/openrouter_pkce.py src/puripuly_heart/ui/app.py src/puripuly_heart/ui/controller.py src/puripuly_heart/ui/views/settings.py tests/core/test_openrouter_pkce.py tests/ui/test_app_branches.py tests/ui/test_controller_branch_paths.py tests/ui/test_settings_view_branches.py
```

## Outcome

- Focused RED tests passed after implementation.
- Focused button-state regression tests passed after implementation.
- Focused brown default/disabled auth button color tests passed after implementation.
- Targeted PKCE/auth/settings suite: PASS.
- Ruff: PASS.
- Black check: PASS.
- Independent review after final fix: no Critical/Important/Minor issues; ready from code-review perspective.

## Review note

- Review flagged that PKCE failure can leave the staged BYOK provider draft in Settings.
- This is intentional for the new UX: BYOK model selection is a separate user choice, and failed browser auth should still leave the manual API key input visible as the fallback path.

## Skips

- No live OpenRouter browser PKCE run was performed.
