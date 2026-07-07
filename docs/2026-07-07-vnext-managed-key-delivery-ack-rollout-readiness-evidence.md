# vNext Managed Key Delivery ACK Rollout Readiness Evidence

Date: 2026-07-07

Scope: vNext telemetry broker port and managed-key delivery ACK rollout readiness. This note records redacted verification evidence. It does not claim production deployment.

## Automated verification

Broker verification ran from the Linux-native WSL workspace `/home/salee/dev/puripuly_heart_iw_verify`, synced from the assigned implementation worktree.

- `pnpm vitest broker/tests/discord-issue-route.spec.ts broker/tests/referral-reward-flow.spec.ts broker/tests/qq-auth-route.spec.ts broker/tests/telemetry-ingest.spec.ts broker/tests/managed-key-delivery.spec.ts --reporter=dot`
  - Result: passed, 5 files, 123 tests.
- `pnpm vitest broker/tests --reporter=dot`
  - Result: passed, 65 files, 504 tests passed, 1 skipped.
- `pnpm typecheck`
  - Result: passed.

The source plan lists `pnpm test`, but the repository root `package.json` has no `test` script. The closest executable whole-broker test evidence is the full `pnpm vitest broker/tests --reporter=dot` run above plus `pnpm typecheck`.

Python verification ran from the assigned worktree with the project virtual environment at `C:\Users\salee\Documents\dev\puripuly_heart\.venv`.

- `git diff --check`
  - Result: passed.
- `python -m pytest tests/app/test_managed_connection_auth.py tests/app/test_qq_managed_auth.py tests/config/test_settings_vnext_schema.py tests/config/test_settings_vnext_migration_serialization.py tests/ui/test_controller_branch_paths.py tests/core/test_managed_openrouter_release.py::test_llm_start_retries_pending_delivery_ack_before_ready tests/core/test_managed_openrouter_release.py::test_llm_start_with_pending_delivery_ack_failure_does_not_return_ready tests/core/test_managed_openrouter_broker_client.py`
  - Result: passed, 668 tests.
- `python -m pytest -p no:cacheprovider tests/core/test_managed_openrouter_broker_client.py tests/app/test_managed_connection_auth.py tests/app/test_qq_managed_auth.py tests/core/test_managed_openrouter_release.py::test_llm_start_retries_pending_delivery_ack_before_ready tests/core/test_managed_openrouter_release.py::test_llm_start_with_pending_delivery_ack_failure_does_not_return_ready`
  - Result: passed, 125 tests. This focused no-cache run rechecked the vNext client ACK send/receive contract, ACK endpoint request/result mapping, delivery ACK metadata parsing, app transaction ordering, QQ ACK transaction, and pending ACK readiness retry after the user clarified that client ACK behavior is the release-readiness smoke focus.

## Migration and rollout order

Local migration ordering is correct for the vNext rollout:

1. `broker/migrations/0011_add_telemetry_active_days.sql`
2. `broker/migrations/0012_add_managed_key_delivery_ack.sql`

Rollout order remains broker first, then clients that send `delivery_ack_supported=true`. Legacy clients that do not send `delivery_ack_supported` remain supported by the broker compatibility paths covered by broker route tests.

Remote D1 migration list evidence is complete. The read-only check used the broker workspace package, a rendered production Wrangler config with the deployment database id injected from the local environment, and the source D1 binding:

- `pnpm --filter @puripuly-heart/broker exec wrangler d1 migrations list BROKER_DB --remote --config <rendered-production-config>`
  - Result: passed, `No migrations to apply!`

Earlier attempts before rendering the production config showed why the adjusted command was required:

- `pnpm exec wrangler d1 migrations list --remote`
  - Blocked because `wrangler` is not exposed from the workspace root.
- `pnpm --filter @puripuly-heart/broker exec wrangler d1 migrations list --remote`
  - Blocked because current Wrangler CLI requires the database argument omitted by the source-plan command.
- `pnpm --filter @puripuly-heart/broker exec wrangler d1 migrations list BROKER_DB --remote --config wrangler.jsonc`
  - Blocked because the checked-in config intentionally keeps `database_id` as the `REQUIRED_AT_DEPLOY_TIME` placeholder.

The successful remote result means the target production D1 database has no unapplied migrations from the current `broker/migrations` directory. This is a read-only verification result, not a migration apply.

## Smoke evidence status

No raw keys, ACK tokens, credentials, broker payloads, provider payloads, or unredacted stack traces are recorded in this note.

User smoke-scope clarification: broker-side Discord, QQ, and migration behavior was already exercised from the dev rollout path. For this vNext run, the release-readiness smoke focus is whether the vNext client receives ACK metadata, persists local managed state before ACK, sends the ACK request, handles ACK results, and retries pending ACK before readiness.

Automated tests provide the vNext client ACK smoke evidence for that clarified focus:

- Broker managed-key delivery tests cover ACK token hashing, idempotent ACK handling, source finalization ordering, pending-before-ACK activation, referral credit after ACK-supported finalization, stale cleanup, retry-safe finalization, and cleanup failure handling.
- Discord and QQ route tests cover route compatibility and selected ACK-supported cleanup/failure paths, but they are not a substitute for real route smoke evidence that captures delivery fields and post-ACK provider usage.
- Python app ACK tests cover local pending metadata and SecretStore token storage before usable local key writes, ACK-after-persistence behavior, retry on readiness, and no false-ready results after ACK failure.
- Broker client tests cover `delivery_ack_supported=true`, ACK metadata parsing, bounded ACK endpoint result mapping, and redaction of ACK tokens in request representation.

This run does not repeat live external Discord, QQ, OpenRouter usage, app-exit, or response-loss smoke against production. Those scenarios require external accounts, a production-like broker/D1 environment, OpenRouter usage that mutates provider state, and explicit evidence-handling authorization. The broker-side parts are treated as covered by the dev rollout smoke path; this vNext evidence covers the client ACK send/receive and transaction behavior.

If fresh live external smoke is required again before release, the remaining prerequisites are:

1. Provide a non-production or explicitly approved production-like broker/D1 target with `CLOUDFLARE_API_TOKEN` available only in the execution environment.
2. Provide Discord and QQ test identities/accounts authorized for managed auth smoke.
3. Provide OpenRouter smoke authorization and a redacted evidence template that records state transitions without raw keys, ACK tokens, credentials, raw broker/provider payloads, or secret material.
4. Confirm whether response-loss and stale-cleanup smoke should use controlled synthetic fault injection, a staging worker, or production-like manual interruption.

## Readiness conclusion

Automated vNext broker and Python evidence passed, local migration ordering is correct, remote D1 has no unapplied migrations, and rollout order remains broker-first/client-after-broker. Under the user clarification that dev already covered broker-side smoke and this vNext gate should focus on client ACK send/receive, rollout-readiness evidence is complete for the vNext client ACK scope without repeating live external production smoke in this run.
