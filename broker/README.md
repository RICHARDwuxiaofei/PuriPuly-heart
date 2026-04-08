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

## Trial challenge + verify handshake

- `POST /v1/trial/challenge`
  - request: `installation_id`, base64url `device_public_key`, `app_version`
  - public input bounds: `installation_id` `1-128` chars, `app_version` `1-64` chars
  - rejects client-supplied `hardware_hash`, `signed_at`, and `signature`
  - response: `challenge`, `challenge_expires_at`, `fingerprint_salt`, normalized `managed_state`, and `current_entitlement`
  - challenge TTL: `5` minutes
  - never returns `release_token`, release-session state, or raw managed credentials
- `POST /v1/trial/challenge/verify`
  - request: `installation_id`, base64url `device_public_key`, `challenge`, `challenge_expires_at`, `hardware_hash`, `app_version`, `signed_at`, base64url `signature`
  - public input bounds: `installation_id` `1-128` chars, `app_version` `1-64` chars, `hardware_hash` `1-128` chars
  - supported timestamp subset for `challenge_expires_at` and `signed_at`: `YYYY-MM-DDTHH:MM:SS(.mmm)?(Z|±HH:MM)` with a real calendar date/time
  - Ed25519 signature payload is canonical UTF-8 text joined by newlines in this order:
    1. `installation_id`
    2. `device_public_key`
    3. `challenge`
    4. `challenge_expires_at`
    5. `hardware_hash`
    6. `app_version`
    7. `signed_at`
  - enforces signed clock skew within `±60` seconds
  - uses the already registered `device_public_key`; verify does not rebind installation identity
  - successful verify consumes the active challenge, persists `hardware_hash` with the issued challenge salt version, and returns `release_token`, `release_token_expires_at`, normalized `managed_state`, and `current_entitlement`
  - release token TTL: `15` minutes
- `GET /v1/trial/status`
  - query: `installation_id`
  - headers: `X-Puripuly-Timestamp`, `X-Puripuly-Signature`
  - `installation_id` keeps the same public bound: `1-128` chars
  - `X-Puripuly-Timestamp` must be a valid ISO-8601 timestamp in the same strict subset used by verify
  - `X-Puripuly-Signature` must transport a base64url Ed25519 signature
  - canonical status-signing payload is UTF-8 text joined by newlines in this order:
    1. `installation_id`
    2. `timestamp`
  - enforces signed clock skew within `±60` seconds
  - status requests are verified against the already registered `device_public_key` for the installation; unknown `installation_id` values return `installation_not_found`
  - response: normalized `managed_state`, `current_entitlement`, and lifecycle-derived `onboarding_eligibility`
  - onboarding eligibility is broker-side metadata only: `none` => eligible, `pending_release` => eligible continuation, `active` / `expired` / `revoked` => ineligible
  - `expired` and `revoked` are returned as `200` lifecycle data, not public error codes
  - live remaining budget stays upstream in OpenRouter metadata instead of being mirrored into broker status
- `POST /v1/providers/openrouter/issue`
  - request: `installation_id`, base64url `device_public_key`, base64url `release_token`, `reason`, `budget_usd`, `model`, `signed_at`, base64url `signature`
  - activation reason is fixed to `llm_start`
  - `budget_usd` must match the managed-trial hard limit and `model` must match the pinned managed OpenRouter model
  - supported timestamp subset for `signed_at`: `YYYY-MM-DDTHH:MM:SS(.mmm)?(Z|±HH:MM)` with a real calendar date/time
  - Ed25519 signature payload is canonical UTF-8 text joined by newlines in this order:
    1. `installation_id`
    2. `device_public_key`
    3. `release_token`
    4. `reason`
    5. `budget_usd`
    6. `model`
    7. `signed_at`
  - enforces signed clock skew within `±60` seconds
  - consumes the `pending_release` token, upgrades the entitlement to `active`, and reuses the same `active` entitlement for same-session retries
  - success response returns `openrouter_api_key`, distinct `managed_credential_ref`, normalized `managed_state`, `expires_at`, `budget_usd`, and `model`
  - live remaining budget and usage stay upstream in OpenRouter metadata and are not mirrored into the issue response

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
  - constraints: `installation_id` primary key, `device_public_key` unique, `hardware_hash` indexed, and bounded persisted public text (`installation_id <= 128`, `app_version <= 64`, `hardware_hash <= 128` when present)
  - update rules: each challenge overwrites `challenge`, `challenge_expires_at`, `challenge_salt_version`, and `app_version`; it clears stored `hardware_hash` / `hardware_hash_salt_version` only when lifecycle is `none` or `pending_release`, and preserves fingerprint state for `active`, `expired`, and `revoked`; verify clears the challenge fields; `hardware_hash` stays `NULL` until verify succeeds
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
- Rotation keeps one current salt and one previous salt version. Duplicate matching only uses `hardware_hash` values tagged with the current version. In-flight challenges may complete on the previous version until their existing `challenge_expires_at`, after which stale hashes are refreshed in place on successful verify or cleared when the broker reissues a challenge for `none` / `pending_release` state.
