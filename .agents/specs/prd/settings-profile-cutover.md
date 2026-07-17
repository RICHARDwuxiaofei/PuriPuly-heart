---
id: PRD-SETTINGS-PROFILE-CUTOVER-001
status: reviewed
source: .agents/specs/prd/drafts/settings-profile-cutover.source.r3.md
baseline_ref: vnext@f8b4ad469b35d11bfd29ab62830d8a00ea339811
integration_target: vnext
document_review_verdict: ready
blocking_open_decisions: 0
---

# Outcome

When an existing public user upgrades from the final dev-based release to the first vnext-based release, PuriPuly starts with that user's valid settings and credentials preserved, automatically converts persisted settings to the canonical format, and continues using the established `puripuly-heart` profile without creating a second profile or requiring user action.

# Established Baseline

## Code baseline

- The normative code baseline is `vnext@f8b4ad469b35d11bfd29ab62830d8a00ea339811`.
- The released-settings comparison baseline is `dev@50e0fd2a9fb6eb6063e8850feccb18b07585466a`, whose persisted legacy settings schema is version 30.
- vnext already owns a canonical persisted schema separating `intent` and `state`, plus compatibility conversion to and from the legacy `AppSettings` surface.

## User-visible surfaces

- First launch after installing the vnext-based update.
- All settings shown in the Flet settings UI and their subsequent persistence.
- Provider and credential availability derived from SecretStore.
- Local model, VAD asset, and log locations reached through the default user-data root.
- Existing startup error reporting when exceptional migration failure prevents safe loading.

## Actual product entrypoints

- Packaged Windows executable `PuriPulyHeart.exe`, which enters through `puripuly_heart.main` and starts the Flet GUI by default.
- Source entrypoint `python -m puripuly_heart.main` with the same default-profile behavior.
- The existing explicit `--config` entrypoint remains a caller-selected settings file rather than a trigger for default-profile discovery.

## Platform and environment

- Public production composition is the packaged Windows application and installer on supported Windows x64 systems.
- Automated Python evidence uses the repository `.venv` on Windows.
- Installer migration evidence uses an alternate AppId and isolated installation directory while preserving the production profile and upgrade semantics under test.
- SecretStore evidence covers Windows Keyring and encrypted-file storage; encrypted-file access requires `PURIPULY_HEART_SECRETS_PASSPHRASE`.

## Compatibility baseline

- Existing public users persist settings, secrets, and user assets under the stable `puripuly-heart` profile.
- Existing settings files and keys are compatibility inputs and must remain loadable through forward migration, including the final dev verification booleans.
- Established SecretStore key names remain compatible.
- Provider aliases, prompt fallback behavior, output-channel separation, and explicit `--config` behavior remain compatible.
- The private `puripuly-heart-vnext` profile has not been publicly released and is not a public migration source.

# Scope

## Included

- One-way automatic migration from the released dev legacy settings shape to the vnext canonical settings shape.
- Preservation of valid user intent, persisted operational state, SecretStore access, and user assets required for continuity.
- Cutover of all future default user-data writes to the stable `puripuly-heart` profile.
- Existing backup and atomic-replacement safety, diagnostics, and upgrade evidence.
- Compatibility fixtures covering the final dev settings baseline and older established inputs.

## Non-goals

### NG-001 — Reverse downgrade compatibility

An older dev executable does not need to read settings after canonical migration.

### NG-002 — Concurrent dev and vnext profiles

The product does not support alternating between dev and vnext executables or synchronizing two live profiles.

### NG-003 — Internal settings-model retirement

Removing the legacy `AppSettings` compatibility projection from UI and runtime code is not required by this PRD.

### NG-004 — Private preview profile import

Automatically preferring or merging the repository owner's private `puripuly-heart-vnext` profile is not part of public startup behavior.

### NG-005 — Historical data deletion

The migration does not automatically delete old profile directories, SecretStore namespaces, backups, or historical logs.

### NG-006 — Unrelated product behavior

Provider behavior, prompts, Broker APIs, overlay protocol, output routing, installer identity, and feature defaults do not change except where necessary to preserve existing persisted meaning.

### NG-007 — Migration-specific recovery product

A new recovery mode, reset workflow, settings repair UI, or SecretStore migration workflow is not required.

# Requirements

## REQ-001 — Stable profile authority

The default public application must use `puripuly-heart` as the sole active user-data and SecretStore profile before, during, and after migration. It must not create or require `puripuly-heart-vnext` during normal startup.

## REQ-002 — Automatic one-way migration

On first launch with an existing supported legacy settings file, the application must detect its shape, convert it to the current canonical shape, and continue startup without prompting the user when conversion succeeds.

## REQ-003 — Semantic settings preservation

Every valid persisted dev setting must have an explicit canonical destination or an explicitly approved meaning-preserving normalization. No valid user choice may be retired. User intent and persisted operational state must retain their observable meaning after migration. New settings absent from the legacy input receive current safe defaults.

A valid legacy `api_key_verified: true` value must remain verified after migration. Migration alone must not downgrade it to unknown, require manual verification, or trigger provider revalidation. Established invalidation behavior still applies after a credential is subsequently changed. Malformed values may normalize to safe values and must not weaken validation.

## REQ-004 — Credential continuity

Existing stable SecretStore keys and values must remain authoritative and available after upgrade without cross-namespace migration. The release must preserve the established SecretStore key registry and encrypted-file compatibility, must not serialize raw secrets into settings, and must not require users with valid existing credentials to re-enter them.

## REQ-005 — Durable safety

Before any forward rewrite, the exact pre-migration settings bytes must be backed up without overwriting an existing backup. The canonical replacement must be atomic and must be reloaded and validated before migration is considered successful.

## REQ-006 — Failure containment

If required parsing, conversion, backup, write, replacement, or validation fails, the original persisted settings must not be replaced with defaults or an unvalidated canonical file. Existing startup error handling may report the failure; no new migration-specific recovery workflow is required.

## REQ-007 — Idempotent startup

After successful migration, every later launch must load the canonical stable profile without repeating the migration, changing preserved values, recreating backups, or accessing a private vnext profile.

## REQ-008 — Unified future asset root

Future default locations for settings, encrypted secrets, local models, VAD assets, and logs must resolve under the stable `puripuly-heart` root. Existing valid stable assets remain in place and no second active profile is introduced.

## REQ-009 — Explicit config compatibility

An explicitly supplied `--config` path remains authoritative for that invocation. It may be forward-migrated safely in place but must not cause discovery, merge, or replacement of the default stable profile.

## REQ-010 — Silent success and safe diagnostics

Successful migration produces no consent prompt, choice dialog, or reconfiguration workflow. Migration diagnostics may record source shape, destination shape, status, and safe failure category, but not setting values, secret values, credentials, prompts, transcripts, translations, or personal identifiers.

# Protected Invariants

## Product invariants

### INV-P-001 — User continuity

A valid existing user's observable configuration and credential-backed feature availability continue across the update without manual reconfiguration.

### INV-P-002 — Output-channel isolation

Peer utterances never route to the VRChat chatbox, and self, peer, and system outputs remain separate product channels.

### INV-P-003 — No successful-migration interruption

A successful migration is invisible to the user apart from preserved behavior after startup.

### INV-P-004 — Verification-state continuity

An unchanged credential that was persisted as verified by dev remains verified after migration without migration-triggered user action or provider revalidation.

## Durable architecture invariants

### INV-A-001 — Canonical intent/state separation

Persisted user intent, persisted operational state, resolved runtime configuration, runtime-only state, and secrets remain distinct boundaries.

### INV-A-002 — SecretStore compatibility

Secrets are loaded through SecretStore using established keys. Encrypted-file storage continues to require `PURIPULY_HEART_SECRETS_PASSPHRASE`.

### INV-A-003 — Backup before forward migration

No supported existing settings file is forward-rewritten before a recoverable exact backup exists.

### INV-A-004 — Validated settings before runtime

Runtime composition receives only settings that were loaded or migrated into a validated supported shape.

### INV-A-005 — Compatibility-preserving normalization

Provider aliases and prompt fallback contracts remain accepted at the migration boundary even when canonical persistence no longer emits their legacy representation.

# Approved Decisions

- The migration is one-way because dev is retired when the vnext-based release ships.
- The stable `puripuly-heart` profile is authoritative for public users.
- Canonical `intent/state` persistence is the post-migration format.
- Users carry forward valid settings and credentials; they do not choose between old and new profiles.
- Successful migration is silent; an exceptional unsafe migration does not overwrite the source or fall back to persisted defaults.
- Legacy dev `api_key_verified: true` remains verified without migration-triggered revalidation; later credential changes retain established invalidation behavior.
- Historical private vnext data is not deleted automatically and does not affect public startup.
- Internal compatibility projections may remain until a separately authorized refactor.

# Open Product Decisions

None.

# Acceptance Criteria

| AC | Verifies | Evidence class | Required environment | Pass condition |
|---|---|---|---|---|
| AC-001 | REQ-002, REQ-003, INV-P-004, INV-A-001, INV-A-005 | automated + comparative | Windows `.venv`; frozen final-dev v30 fixtures plus every established older migration fixture | Every valid legacy leaf reaches its required canonical meaning or approved meaning-preserving normalization, every valid legacy `api_key_verified: true` remains verified, and canonical reload is semantically equal. |
| AC-002 | REQ-003, REQ-004, INV-P-001, INV-P-004, INV-A-002 | automated + production-composition | Windows Keyring and encrypted-file test profiles; packaged application for production composition | All established stable secret keys remain readable, verified credential-backed selections remain verified and usable without re-entry or migration-triggered provider verification calls, and settings contain no raw secret values. |
| AC-003 | REQ-001, REQ-007, REQ-008 | automated + production-composition + temporal | Isolated Windows user profile with packaged executable; two consecutive post-upgrade launches | Both launches use the stable root, the second launch performs no migration or new backup, and no `puripuly-heart-vnext` path or namespace is created or accessed. |
| AC-004 | REQ-005, INV-A-003 | automated + fault-injection | Windows `.venv` on NTFS | For backup-name collision and failures before atomic replacement, the source bytes remain unchanged; on success a collision-safe byte-identical backup exists and the replacement reloads successfully. |
| AC-005 | REQ-006, INV-A-004 | automated + fault-injection | Windows `.venv` with induced parse, backup, write, replacement, and validation failures | The original is not replaced by defaults or an unvalidated file, and runtime composition receives no invalid settings. |
| AC-006 | REQ-002, REQ-003, REQ-004, INV-P-001, INV-P-003 | production-composition + comparative + manual | Upgrade from a packaged final-dev fixture installation to the candidate vnext installer using an alternate AppId and isolated installation directory | First launch requires no migration interaction; a before/after comparison confirms preserved valid settings, operational state, and credential-backed availability. |
| AC-007 | REQ-009 | automated + production-composition | Windows `.venv` and packaged executable with an isolated explicit config path | Only the supplied file is read or migrated; the default stable profile is neither discovered nor modified. |
| AC-008 | INV-P-002, INV-A-005 | automated regression | Windows `.venv` focused output-routing and provider/prompt compatibility suites | Existing channel-isolation, provider-alias, and prompt-fallback guards continue to pass without changed observable routing. |
| AC-009 | REQ-003, REQ-007, INV-P-001 | automated + temporal | Windows `.venv` with a migrated final-dev fixture exercised through the compatibility projection and settings save path | After a UI-equivalent settings save and relaunch, preserved migrated values and newly changed values remain semantically correct. |
| AC-010 | REQ-010 | automated + diagnostics inspection | Windows `.venv` success and injected-failure migration paths | Captured migration diagnostics contain only approved metadata and none of the prohibited user or secret values. |

# Decision Authority

## Executor may decide

- reversible implementation details
- private types and internal APIs
- file and helper placement
- implementation sequence
- tests and diagnostics

## Independent review required

- durable boundary reliance
- production cutover
- legacy, compatibility, migration, rollback, or fallback path removal
- persistence, security, lifecycle, concurrency, or public API change
- material strategy pivot
- terminal completion

## User decision required

- observable product behavior
- scope or non-goal
- compatibility break
- irreversible migration
- security posture
- supported platform or provider
- required evidence weakening

# Completion Rule

Every acceptance criterion must be directly proven in its required environment and evidence class. Automated tests alone cannot replace platform, production-composition, comparative, temporal, or manual evidence.
