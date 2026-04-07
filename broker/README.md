# Broker service foundation

This directory establishes the managed-trial broker as a separate deployable service in the monorepo.

## Explicit rollout boundary

- Runtime stack: TypeScript + Hono on Cloudflare Workers with native D1 and Worker secrets.
- Hosting scope: single-region rollout assumption for the initial Worker deployment, with D1 `location_hint` set to `apac`.
- Managed free-trial path: `OpenRouter` + `google/gemma-4-26b-a4b-it`.
- Inference boundary: the app talks to OpenRouter directly; the broker remains a trial and credential broker.
- Out of scope in this foundation: translation proxying, multi-region deployment, KV, R2, and admin dashboard work.

## Deploy note

`broker/wrangler.jsonc` intentionally uses a non-secret placeholder `database_id`. A real Cloudflare D1 identifier must be supplied in deployment-specific configuration before the service is deployed.

Use `pnpm --filter @puripuly-heart/broker run verify:config` to exercise the pinned Wrangler CLI against `broker/wrangler.jsonc` without requiring cloud credentials.

## Verification environment

Broker verification is Linux-only. Run `pnpm install`, Vitest, and Wrangler from a Linux-native workspace (for example, a WSL-internal path or a regular Linux checkout), not from Windows or shared `/mnt/c/...` `node_modules`.

## Persistence model

`broker/src/persistence.ts` and `broker/migrations/0000_define_broker_persistent_state.sql` define the initial D1-backed state contract.

- `broker_config`
  - columns: `key`, `value`, `updated_at`
  - bootstrap rows: `fingerprint_salt`, `abuse_controls`
  - runtime-tunable non-secret operational controls live here as JSON rows so operators do not need code changes for threshold updates
  - constraints: keys are limited to the supported config rows for this rollout and `value` must be valid JSON
  - `abuse_controls` fixes the settled endpoint/dimension layout:
    - `POST /v1/trial/challenge`: per IP, `10` requests / `15` minutes
    - `POST /v1/trial/challenge/verify`: per `installation_id`, `5` requests / `15` minutes
    - `POST /v1/providers/openrouter/issue`: per `installation_id`, `3` requests / `15` minutes
    - `GET /v1/trial/status`: per `installation_id`, `30` requests / `15` minutes
    - global daily cap on new active entitlements, stored as a runtime-configurable broker value
- `installations`
  - columns: `installation_id`, `device_public_key`, `hardware_hash`, `hardware_hash_salt_version`, `app_version`, `challenge`, `challenge_expires_at`, `challenge_salt_version`, `created_at`, `last_seen_at`
  - constraints: `installation_id` primary key, `device_public_key` unique, `hardware_hash` indexed
  - update rules: each challenge overwrites `challenge`, `challenge_expires_at`, `challenge_salt_version`, and `app_version`; verify clears the challenge fields; `hardware_hash` stays `NULL` until verify succeeds
- `openrouter_entitlements`
  - zero or one row per installation, keyed by `installation_id` when present
  - columns: `installation_id`, `status`, `budget_usd`, `managed_credential_ref`, `issued_at`, `expires_at`, plus minimal release-session columns `release_session_ref`, `release_token_hash`, `release_token_expires_at`
  - constraints: `managed_credential_ref` unique, `status` indexed, `expires_at` indexed
  - `release_token_hash` is protected by a partial unique index when non-`NULL`
  - stored `status` values are `pending_release`, `active`, `expired`, and `revoked`; `none` is represented by the absence of a row
  - update rules: entitlement status and credential metadata are updated in place; append-only entitlement history is intentionally out of scope for the initial rollout
  - remaining live budget stays upstream in OpenRouter metadata instead of being mirrored into broker storage; the release token remains installation-bound, one-time, and `15` minutes TTL

## Retention and salt rotation

- Inactive `pending_release` installations may be deleted after `30` days from `last_seen_at`.
- Terminal `expired` or `revoked` installations may be deleted after `90` days from `max(last_seen_at, expires_at)`.
- Retention cleanup deletes from `installations`; the entitlement row is removed by `ON DELETE CASCADE`.
- `fingerprint_salt` remains one server-managed global salt shared across clients for duplicate detection.
- Rotation keeps one current salt and one previous salt version. Duplicate matching only uses `hardware_hash` values tagged with the current version. In-flight challenges may complete on the previous version until their existing `challenge_expires_at`, after which stale hashes are refreshed in place on successful verify or cleared when the broker reissues a challenge.
