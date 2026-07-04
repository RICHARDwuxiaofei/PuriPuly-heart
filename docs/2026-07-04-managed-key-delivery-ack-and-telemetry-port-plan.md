# Managed Key Delivery ACK and vNext Telemetry Port Plan

Date: 2026-07-04

## Purpose

Prevent managed OpenRouter child keys from being marked delivered or referral-credited before the client has actually persisted the raw key locally. Apply the fix to both Discord-managed and QQ-managed issuance, and keep `dev` and `vnext` aligned. Also bring the `dev` broker telemetry ingestion/reporting work back into `vnext` before adding the delivery ACK migrations.

## Current Branch Reality

- Managed broker issuance core is effectively shared between `dev` and `vnext` for this work:
  - `broker/src/discord-managed-issue.ts`
  - `broker/src/qq-managed-issue.ts`
  - `broker/src/qq-auth.ts`
  - `broker/src/openrouter-management.ts`
  - `broker/src/managed-issuance.ts`
  - `broker/src/abuse-monitoring.ts`
- The broker is not byte-for-byte identical across branches. `vnext` differs from `dev` around telemetry/source-route removal and related tests/files:
  - `broker/src/app.ts`
  - `broker/src/persistence.ts`
  - `broker/src/scheduled.ts`
  - `broker/src/abuse-controls.ts`
  - telemetry migration/tests/files
- Python client architecture differs substantially:
  - `dev` persists managed keys mostly inside `ManagedOpenRouterReleaseService`.
  - `vnext` routes managed auth through app ports/services such as `ManagedConnectionAuthService` and `QqManagedAuthService`, plus `settings_vnext` schema/compat layers.

## Telemetry Port to vNext

### Cherry-pick guidance

Do not blindly cherry-pick telemetry. If telemetry landed in `dev` as clean, isolated commits, cherry-pick only those commits into `vnext` and resolve expected conflicts. If the commits also contain unrelated source-offer/link-header or release metadata changes, do a manual file-level port instead.

Recommended rule:

1. Identify the exact telemetry commits from `dev`.
2. Cherry-pick only those commits if isolated.
3. If mixed with unrelated changes, manually port telemetry files/sections.
4. Preserve `vnext` decisions unrelated to telemetry unless explicitly reverted.

### Telemetry pieces to port from `dev` to `vnext`

- `broker/src/telemetry.ts`
- `broker/migrations/0011_add_telemetry_active_days.sql`
- Route registration for `POST /v1/telemetry/translation-success-day` in `broker/src/app.ts`
- `telemetryTranslationSuccessDayIp` abuse-control config in:
  - `broker/src/persistence.ts`
  - `broker/src/abuse-controls.ts`
  - test-support config seeding as needed
- Scheduled retention and daily heartbeat summary integration in `broker/src/scheduled.ts`
- Persistence/types for telemetry active day records in `broker/src/persistence.ts`
- Tests:
  - `broker/tests/telemetry-ingest.spec.ts`
  - daily report telemetry assertions
  - retention tests
  - migration behavior tests
  - deploy automation expectations

Python/UI status:

- `vnext` already has telemetry consent/state concepts in `settings_vnext` and UI-facing settings paths.
- This telemetry port is primarily the missing broker ingestion/reporting side, plus any vNext wiring needed to call the broker endpoint from the existing telemetry success event path.

### Migration numbering

- `dev` already has `broker/migrations/0011_add_telemetry_active_days.sql`.
- `vnext` currently lacks that migration.
- Port telemetry to `vnext` first, keeping `0011_add_telemetry_active_days.sql`.
- Use `0012_add_managed_key_delivery_ack.sql` for delivery ACK in both branches to avoid future migration-number conflicts.

## Managed Key Delivery ACK: Product Meaning

Current behavior treats broker-side key creation as delivery success:

```text
Broker creates child key
-> Broker marks entitlement active/delivered and credits referral
-> Broker returns raw key
-> Client attempts local secret/settings persistence
```

Target behavior treats client persistence as delivery success:

```text
Broker creates child key
-> Broker marks delivery_pending
-> Broker returns raw key + ACK token
-> Client stores raw key in SecretStore and commits settings/state
-> Client sends delivery ACK
-> Broker marks entitlement active/delivered and applies final rewards/monitoring
```

The raw OpenRouter key must never be stored in D1. The ACK token must also not be stored in plaintext; store only a hash.

## Common Broker ACK Design for dev and vNext

### New migration

Add `broker/migrations/0012_add_managed_key_delivery_ack.sql` in both branches.

Add status values:

- Discord `openrouter_entitlements.discord_issue_status` gains `delivery_pending`.
- QQ `qq_managed_entitlements.status` gains `delivery_pending`.

Add shared delivery table:

```sql
CREATE TABLE managed_key_deliveries (
  delivery_id TEXT PRIMARY KEY,
  issue_source TEXT NOT NULL CHECK (issue_source IN ('discord', 'qq')),
  subject_ref TEXT,
  installation_id TEXT,
  managed_credential_ref TEXT NOT NULL,
  ack_token_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'acknowledged', 'expired', 'cleanup_required')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  acknowledged_at TEXT,
  failed_at TEXT,
  failure_reason TEXT
) STRICT;
```

Indexes should support stale pending cleanup by `status, expires_at`, lookup by `managed_credential_ref`, and source-specific queries by `issue_source, created_at`.

### New broker module

Add `broker/src/managed-key-delivery.ts`.

Responsibilities:

- Generate `delivery_id` and `delivery_ack_token`.
- Hash ACK token before D1 persistence.
- Insert pending delivery rows.
- Validate ACK attempts.
- Make ACK idempotent.
- List and reconcile stale pending deliveries.

### New ACK endpoint

Add:

```text
POST /v1/providers/openrouter/managed-key-delivery/ack
```

Request:

```json
{
  "delivery_id": "...",
  "managed_credential_ref": "...",
  "delivery_ack_token": "..."
}
```

Success response:

```json
{
  "ok": true,
  "status": "acknowledged"
}
```

Duplicate ACK response for the same valid delivery/token:

```json
{
  "ok": true,
  "status": "already_acknowledged"
}
```

Route insertion is branch-sensitive:

- In `dev`, preserve telemetry/source routes and add only the ACK route.
- In `vnext`, preserve current vNext route decisions unless telemetry port explicitly reintroduces only telemetry.

### Compatibility flag

New clients send:

```json
{
  "delivery_ack_supported": true
}
```

If absent, broker keeps legacy behavior to preserve `/v1` compatibility during staged rollout.

## Discord Broker Flow

Target file: `broker/src/discord-managed-issue.ts`

Legacy path: no flag means keep current behavior.

ACK-supported issue request:

1. Validate Discord OAuth, device binding, signature, eligibility, referral reservation.
2. Create OpenRouter child key.
3. Assign guardrail.
4. Store entitlement as `pending_release + discord_issue_status = 'delivery_pending'` with `managed_credential_ref`, `issued_at`, and `expires_at`, but no `discord_issue_delivered_at`.
5. Insert `managed_key_deliveries` pending row.
6. Return raw key plus delivery fields:

```json
{
  "openrouter_api_key": "...",
  "managed_credential_ref": "...",
  "expires_at": "...",
  "delivery_ack_required": true,
  "delivery_id": "...",
  "delivery_ack_token": "...",
  "delivery_ack_expires_at": "..."
}
```

ACK request:

1. Validate delivery row and token.
2. Activate entitlement and set `discord_issue_delivered_at`.
3. Record issue success monitoring.
4. Credit reserved referred reward.
5. Apply referrer reward limit update.
6. Mark delivery acknowledged.
7. Return referral result data if applicable.

Referral rule: no referred/referrer credit before ACK.

## QQ Broker Flow

Target files:

- `broker/src/qq-auth.ts`
- `broker/src/qq-managed-issue.ts`

Legacy path: no flag means keep current behavior.

ACK-supported QQ assert request:

1. Validate QQ credential.
2. Reserve QQ managed entitlement.
3. Create OpenRouter child key.
4. Attach managed credential ref.
5. Assign guardrail.
6. Mark QQ entitlement as `delivery_pending`, not `active`, and leave `delivered_at = NULL`.
7. Insert `managed_key_deliveries` pending row.
8. Return raw key plus delivery fields.

ACK request:

1. Validate delivery row and token.
2. Activate QQ entitlement and set `delivered_at`.
3. Run issue success monitoring.
4. Mark delivery acknowledged.

QQ has no referral credit path, so ACK finalization is simpler than Discord.

## Stale Delivery Cleanup

Add scheduled cleanup in `broker/src/scheduled.ts`:

```text
reconcileStaleManagedKeyDeliveries()
```

Recommended TTL: 15 minutes.

Cleanup behavior:

- If pending delivery expires before ACK:
  - best-effort disable/delete child key
  - mark delivery `expired` if cleanup succeeds
  - mark delivery and entitlement `cleanup_required` if cleanup fails
- Discord-specific cleanup:
  - release or fail pending Discord entitlement/reservation
  - fail reserved referral rows without crediting
- QQ-specific cleanup:
  - reclaim/delete reusable QQ issuing row when cleanup succeeds
  - block automatic reissue with `cleanup_required` when cleanup fails

Do not silently overwrite a pending delivery with a managed credential ref unless old child key cleanup succeeded.

## Python Client Plan for dev

`dev` has managed auth persistence mostly inside `ManagedOpenRouterReleaseService`.

Main files:

- `src/puripuly_heart/core/managed_openrouter_release.py`
- `src/puripuly_heart/core/managed_openrouter_broker_client.py`
- `src/puripuly_heart/core/openrouter_credentials.py`
- `src/puripuly_heart/config/settings.py`
- `src/puripuly_heart/ui/controller.py`

Add DTO:

```python
@dataclass(frozen=True, slots=True)
class ManagedKeyDeliveryAck:
    issue_source: str
    delivery_id: str
    managed_credential_ref: str
    ack_token: str
    expires_at: str
```

Add `delivery_ack: ManagedKeyDeliveryAck | None = None` to managed issue success DTOs.

Broker client:

- Add `delivery_ack_supported=True` to Discord issue requests.
- Add `delivery_ack_supported=True` to QQ assert requests.
- Parse optional delivery fields.
- Add `acknowledge_managed_key_delivery(...)`.

Settings/secrets:

- Add pending ACK metadata to `ManagedIdentitySettings` with defaults and synchronized `to_dict`/`from_dict`:
  - `pending_delivery_ack_source`
  - `pending_delivery_ack_id`
  - `pending_delivery_ack_managed_credential_ref`
  - `pending_delivery_ack_expires_at`
- Store ACK tokens in SecretStore, not settings:
  - `openrouter_managed_delivery_ack_token`
  - `openrouter_managed_qq_delivery_ack_token`

Persistence sequence for Discord and QQ:

1. Write managed OpenRouter key to SecretStore.
2. Store entitlement/user snapshot.
3. Store pending ACK metadata and ACK token if required.
4. Persist settings.
5. Send ACK.
6. On ACK success, clear pending metadata/token and persist settings again.
7. Return ready only after ACK success.

If ACK fails after local persistence, keep local key and pending ACK metadata/token, return a retry/pending result, and do not log raw key or ACK token.

Retry pending ACK before managed-key readiness paths such as `ensure_key_for_llm_start`, `prepare_from_qq_assertion`, startup checks, and translation-enable checks.

## Python Client Plan for vNext

`vnext` should use explicit ports/services rather than concentrating ACK behavior in the release service.

Main files:

- `src/puripuly_heart/app/ports/broker_client.py`
- `src/puripuly_heart/app/services/managed_connection_auth.py`
- `src/puripuly_heart/app/services/qq_managed_auth.py`
- `src/puripuly_heart/app/wiring_managed_auth_factory.py`
- `src/puripuly_heart/core/managed_openrouter_broker_client.py`
- `src/puripuly_heart/core/managed_openrouter_release.py`
- `src/puripuly_heart/config/settings.py`
- `src/puripuly_heart/config/settings_vnext/schema.py`
- `src/puripuly_heart/config/settings_vnext/migration.py`
- `src/puripuly_heart/config/settings_vnext/serialization.py`
- `src/puripuly_heart/config/settings_vnext/compat.py`

Ports and DTOs:

```python
@dataclass(frozen=True, slots=True)
class ManagedKeyDeliveryAckRequest:
    delivery_id: str
    managed_credential_ref: str
    delivery_ack_token: str = field(repr=False)

@dataclass(frozen=True, slots=True)
class ManagedKeyDeliveryAckResult:
    succeeded: bool
    status: str
    message: UserMessageRef | None = None
    diagnostics: ErrorDiagnostics | None = None
    referral_bonus_applied: bool = False
    referral_id: str | None = None
    pass_status: object | None = field(default=None, repr=False)
```

Extend `BrokerClientPort` with:

```python
async def acknowledge_managed_key_delivery(
    self,
    request: ManagedKeyDeliveryAckRequest,
) -> ManagedKeyDeliveryAckResult: ...
```

Extend issue/assert result DTOs with optional delivery ACK metadata.

Broker HTTP client:

- Add `delivery_ack_supported=True` to Discord issue and QQ assert requests.
- Parse delivery ACK metadata.
- Implement `acknowledge_managed_key_delivery`.
- Keep ACK token `repr=False` and out of diagnostics.

vNext settings schema:

- Add pending delivery ACK operational state to managed connection state:
  - source
  - delivery id
  - managed credential ref
  - expires at
- Do not store ACK token in settings. Store token in SecretStore with source-specific keys.
- Update schema, serialization, migration, compat, and legacy bridge fields if compatibility still projects through legacy settings.

Add `src/puripuly_heart/app/services/managed_key_delivery_ack.py` to manage pending ACK metadata/token, retry ACK using the broker client, clear state after ACK success, and return safe diagnostics on failure.

Discord `ManagedConnectionAuthService.authorize` sequence:

1. Broker issue.
2. Write local managed secret.
3. Store managed user id.
4. Store pending ACK metadata/token if required.
5. Commit settings.
6. Send delivery ACK.
7. On ACK success, clear pending ACK metadata/token and persist clear.
8. Return success only after ACK success.

QQ `QqManagedAuthService.authenticate` sequence:

1. Broker assert.
2. Snapshot secret/state.
3. Write QQ managed secret.
4. Apply entitlement snapshot.
5. Store pending ACK metadata/token if required.
6. Persist managed state.
7. Send delivery ACK.
8. On ACK success, clear pending ACK metadata/token and persist clear.
9. Return success only after ACK success.

If ACK fails after local persistence, return a distinct pending/retryable transaction result such as `TRANSACTION_STATUS_REMOTE_DELIVERY_ACK_PENDING`.

Retry pending ACK before treating a managed key as fully ready:

- app startup after settings/secrets load
- translation enable before enabling managed connection
- QQ managed auth dialog start
- Discord managed auth dialog start
- managed trial usage refresh if lifecycle-safe

## Recommended Implementation Order

1. Port telemetry broker ingestion/reporting from `dev` to `vnext`.
   - Prefer cherry-pick only if telemetry commits are isolated.
   - Otherwise manually port the listed telemetry files/sections.
2. Add broker ACK foundation on `dev`.
   - Migration `0012`.
   - Shared delivery table/module.
   - ACK endpoint.
   - No Python behavior change yet.
3. Apply the same broker ACK foundation to `vnext`.
4. Add Discord and QQ broker ACK-supported flows on `dev`, then port same broker changes to `vnext`.
5. Implement dev Python client ACK.
6. Implement vNext Python client ACK using ports/services/settings_vnext.
7. Run branch-specific verification.
8. Deploy broker first with legacy compatibility enabled.
9. Release clients that send `delivery_ack_supported=true`.

## Verification Commands

Broker verification must run from Linux/WSL, not Windows shell:

```text
pnpm test
pnpm vitest broker/tests/discord-issue-route.spec.ts broker/tests/referral-reward-flow.spec.ts broker/tests/qq-auth-route.spec.ts broker/tests/telemetry-ingest.spec.ts
wrangler d1 migrations list --remote
```

Python `dev` focused verification:

```text
.venv\Scripts\python -m pytest tests/core/test_managed_openrouter_broker_client.py tests/core/test_managed_openrouter_release.py tests/config/test_managed_identity_settings.py tests/ui/test_controller_branch_paths.py
```

Python `vnext` focused verification:

```text
.venv\Scripts\python -m pytest tests/app/test_managed_connection_auth.py tests/app/test_qq_managed_auth.py tests/config/test_settings_vnext_schema.py tests/config/test_settings_vnext_migration_serialization.py tests/ui/test_controller_branch_paths.py
```

## Smoke Tests

Discord:

1. Start Discord managed auth with referral.
2. Confirm issue response includes delivery fields.
3. Confirm local secret/settings persisted.
4. Confirm ACK succeeds.
5. Confirm D1 has active/delivered only after ACK.
6. Confirm referral is credited only after ACK.
7. Trigger a real translation and confirm OpenRouter usage changes.

QQ:

1. Submit QQ assertion.
2. Confirm issue response includes delivery fields.
3. Confirm local QQ managed secret/settings persisted.
4. Confirm ACK succeeds.
5. Confirm D1 has active/delivered only after ACK.
6. Trigger a real translation and confirm OpenRouter usage changes.

Failure:

1. Simulate app exit before ACK.
2. Confirm D1 remains delivery pending, not delivered/credited.
3. Restart app and confirm pending ACK retry succeeds if the client had persisted the key.
4. Simulate response loss before client persistence and confirm stale cleanup does not credit referral.

## Non-Negotiable Rules

- Never persist raw OpenRouter keys in D1.
- Never persist ACK token plaintext in D1 or settings.
- Never credit Discord referral rewards before delivery ACK.
- ACK endpoint must be idempotent.
- Cleanup failure must become `cleanup_required`, not silent overwrite.
- Legacy clients without `delivery_ack_supported` must keep working until explicitly deprecated.
- User-facing messages need i18n updates in all locale bundles.
- Diagnostics must not contain raw keys, ACK tokens, provider payloads, or unredacted stack traces.
