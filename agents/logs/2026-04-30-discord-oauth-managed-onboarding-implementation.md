# Discord OAuth managed onboarding implementation verification

- Verification level: L2 for broker/app OAuth issuance changes.
- Workspace: `/home/salee/worktrees/puripuly_heart/discord-oauth-managed-onboarding` on branch `feature/discord-oauth-managed-onboarding`.
- Note: Python verification was run from the isolated WSL worktree using `.venv-wsl` because this implementation worktree is on the Linux filesystem. Broker Node verification was also run from WSL as required by `AGENTS.md`.

## Python commands

- `UV_PROJECT_ENVIRONMENT=.venv-wsl ./.venv-wsl/bin/python -m pytest tests/core/test_discord_oauth_loopback.py tests/core/test_discord_managed_oauth.py tests/core/test_managed_openrouter_broker_client.py tests/core/test_managed_openrouter_release.py tests/core/test_managed_identity.py tests/config/test_first_run_locale.py tests/ui/test_discord_managed_auth_dialog.py tests/ui/test_discord_auth_i18n.py tests/ui/test_debug_preview_panel.py tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_dashboard_view_branches.py -q`
  - Outcome: PASS. Quiet pytest output reported dot progress only.
- `UV_PROJECT_ENVIRONMENT=.venv-wsl ./.venv-wsl/bin/python -m pytest tests/config tests/core/test_managed_openrouter_release.py tests/core/test_managed_openrouter_broker_client.py tests/core/test_managed_identity.py tests/ui/test_debug_preview_panel.py tests/ui/test_app_branches.py tests/ui/test_controller_branch_paths.py tests/ui/test_dashboard_view_branches.py -q`
  - Outcome: PASS. Quiet pytest output reported dot progress only.

## Broker commands

- `pnpm vitest run broker/tests/discord-oauth.spec.ts broker/tests/discord-start-route.spec.ts broker/tests/discord-issue-route.spec.ts broker/tests/discord-issue-concurrency.spec.ts broker/tests/discord-schema.spec.ts broker/tests/network-metadata.spec.ts broker/tests/issuance-cap.spec.ts broker/tests/duplicate-suppression.spec.ts broker/tests/status-response.spec.ts broker/tests/status-route.spec.ts broker/tests/status-signature.spec.ts broker/tests/status-lifecycle-data.spec.ts broker/tests/abuse-config.spec.ts`
  - Outcome: PASS — 13 files, 91 tests.
- `pnpm typecheck`
  - Outcome: PASS — `tsc --noEmit` completed successfully.
- `pnpm --filter @puripuly-heart/broker run verify:config`
  - Outcome: PASS — `wrangler types --config wrangler.jsonc` generated runtime types successfully.

## Manual rollout checks

- Discord Developer Portal redirect URI exact registration:
  - Outcome: PASS by user confirmation.
  - Exact URI set:
    - `http://127.0.0.1:62187/discord/callback`
    - `http://127.0.0.1:62188/discord/callback`
    - `http://127.0.0.1:62189/discord/callback`
- Local `.env.local` secret names configured without printing values:
  - Outcome: PASS.
  - Verified names only:
    - `DISCORD_CLIENT_ID`
    - `DISCORD_CLIENT_SECRET`
    - `DISCORD_REDIRECT_URI_ALLOWLIST`
    - `DISCORD_USER_REF_SECRET`
  - `DISCORD_REDIRECT_URI_ALLOWLIST` and `DISCORD_USER_REF_SECRET` were added to the ignored local `.env.local`; secret values were not printed or committed.
- Production Worker secret values:
  - Outcome: SKIPPED in this local session; Cloudflare production secret state was not inspected.

## Skips

- Windows Rust overlay rebuild not required because no Rust code changed.
- Windows `.venv` verification was not run from the Linux-filesystem worktree; equivalent targeted and broader Python checks were run with `.venv-wsl` in the isolated worktree.
