import { existsSync, readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import app from '../src/index';
import {
  BROKER_MIGRATION_FILENAMES,
  FIRST_BROKER_MIGRATION,
  LATEST_BROKER_MIGRATION,
  readBrokerMigrationSql,
} from './test-support/migrations';

describe('broker persistent state model', () => {
  it('defines the D1 table contract, runtime config keys, and minimal release-session state', async () => {
    const contract = await import('../src/contract');

    expect(contract).toHaveProperty('BROKER_RUNTIME_CONFIG_KEYS', {
      fingerprintSalt: 'fingerprint_salt',
      abuseControls: 'abuse_controls',
    });
    expect(contract).toHaveProperty('BROKER_RUNTIME_CONFIG_SCHEMA', {
      fingerprint_salt: ['current', 'previous', 'rotated_at'],
      abuse_controls: {
        trialChallenge: {
          endpoint: 'POST /v1/trial/challenge',
          scope: 'ip',
          maxRequests: 10,
          windowMinutes: 15,
        },
        trialChallengeVerify: {
          endpoint: 'POST /v1/trial/challenge/verify',
          scope: 'installation_id',
          maxRequests: 5,
          windowMinutes: 15,
        },
        openrouterIssue: {
          endpoint: 'POST /v1/providers/openrouter/issue',
          scope: 'installation_id',
          maxRequests: 3,
          windowMinutes: 15,
        },
        trialStatus: {
          endpoint: 'GET /v1/trial/status',
          scope: 'installation_id',
          maxRequests: 30,
          windowMinutes: 15,
        },
        newActiveEntitlementsPerDay: {
          endpoint: 'POST /v1/providers/openrouter/issue',
          scope: 'global',
          maxCount: null,
          windowDays: 1,
        },
      },
    });
    expect(contract).toHaveProperty('BROKER_PUBLIC_INPUT_BOUNDS', {
      installation_id: {
        minLength: 1,
        maxLength: 128,
        rejectWhitespaceOnly: true,
        rejectControlCharacters: true,
        rejectNewlines: true,
      },
      app_version: {
        minLength: 1,
        maxLength: 64,
        rejectWhitespaceOnly: true,
        rejectControlCharacters: true,
        rejectNewlines: true,
      },
      hardware_hash: {
        minLength: 1,
        maxLength: 128,
        nullable: true,
        rejectWhitespaceOnly: true,
        rejectControlCharacters: true,
        rejectNewlines: true,
      },
    });
    expect(contract).toHaveProperty('BROKER_PERSISTENCE_MODEL', {
      database: 'Cloudflare D1',
      tables: {
        brokerConfig: {
          name: 'broker_config',
          primaryKey: 'key',
          columns: ['key', 'value', 'updated_at'],
          valueEncoding: 'JSON',
          supportedKeys: ['fingerprint_salt', 'abuse_controls'],
          constraints: {
            key: 'supported-keys-only',
            value: 'valid-json',
          },
          seedRows: ['fingerprint_salt', 'abuse_controls'],
        },
        installations: {
          name: 'installations',
          primaryKey: 'installation_id',
          columns: [
            'installation_id',
            'device_public_key',
            'hardware_hash',
            'hardware_hash_salt_version',
            'app_version',
            'challenge',
            'challenge_expires_at',
            'challenge_salt_version',
            'created_at',
            'last_seen_at',
          ],
          unique: ['device_public_key'],
          indexed: [
            'hardware_hash',
            'hardware_hash_salt_version',
            'challenge_expires_at',
            'last_seen_at',
          ],
          textBounds: {
            installation_id: {
              minLength: 1,
              maxLength: 128,
              rejectWhitespaceOnly: true,
              rejectControlCharacters: true,
              rejectNewlines: true,
            },
            app_version: {
              minLength: 1,
              maxLength: 64,
              rejectWhitespaceOnly: true,
              rejectControlCharacters: true,
              rejectNewlines: true,
            },
            hardware_hash: {
              minLength: 1,
              maxLength: 128,
              nullable: true,
              rejectWhitespaceOnly: true,
              rejectControlCharacters: true,
              rejectNewlines: true,
            },
          },
          updateRules: {
            onChallenge: [
              'overwrite challenge',
              'overwrite challenge_expires_at',
              'overwrite challenge_salt_version',
              'overwrite app_version',
              'clear hardware_hash and hardware_hash_salt_version only when lifecycle is none or pending_release',
              'preserve hardware_hash state for active, expired, and revoked lifecycles',
              'touch last_seen_at',
            ],
            onVerify: [
              'clear challenge',
              'clear challenge_expires_at',
              'clear challenge_salt_version',
              'persist hardware_hash only after successful verify',
              'persist hardware_hash_salt_version with hardware_hash',
            ],
            beforeVerify: ['hardware_hash stays null until verify'],
          },
        },
        openrouterEntitlements: {
          name: 'openrouter_entitlements',
          provider: 'OpenRouter',
          rowCardinality: 'zero-or-one-row-per-installation',
          primaryKey: 'installation_id',
          absenceRepresents: 'none',
          storedStatuses: ['pending_release', 'active', 'expired', 'revoked'],
          columns: [
            'installation_id',
            'status',
            'budget_usd',
            'managed_credential_ref',
            'issued_at',
            'expires_at',
            'release_session_ref',
            'release_token_hash',
            'release_token_expires_at',
            'verified_hardware_hash',
            'verified_hardware_hash_salt_version',
          ],
          unique: ['managed_credential_ref'],
          indexed: ['status', 'expires_at'],
          partialUniqueIndexes: [
            {
              name: 'idx_openrouter_entitlements_release_token_hash',
              columns: ['release_token_hash'],
              predicate: 'release_token_hash IS NOT NULL',
            },
          ],
          updateStrategy: 'in-place',
          liveRemainingBudgetSource: 'OpenRouter metadata',
          releaseSessionState: {
            storage: 'ephemeral-columns-on-openrouter_entitlements',
            fields: [
              'release_session_ref',
              'release_token_hash',
              'release_token_expires_at',
            ],
            releaseToken: {
              binding: 'installation-bound',
              oneTimeUse: true,
              ttlMinutes: 15,
              issuanceIdempotencyKey: 'installation_identity + release_session_ref',
              verifyBehavior: 'rotate for existing pending_release row',
            },
          },
        },
        brokerRequestEvents: {
          name: 'broker_request_events',
          purpose: ['per-endpoint rate limits', 'cross-endpoint velocity hooks'],
          columns: ['id', 'endpoint', 'ip', 'installation_id', 'observed_at'],
          appendOnly: true,
          indexed: [
            'endpoint + ip + observed_at',
            'endpoint + installation_id + observed_at',
            'ip + observed_at',
            'installation_id + observed_at',
          ],
        },
        brokerVelocityCapHooks: {
          name: 'broker_velocity_cap_hooks',
          purpose: 'manual cross-endpoint velocity controls with observable outcomes',
          columns: [
            'id',
            'subject_type',
            'subject_value',
            'max_requests',
            'window_minutes',
            'outcome_code',
            'outcome_class',
            'outcome_subcode',
            'reason',
            'active',
            'created_at',
            'expires_at',
          ],
          supportedSubjects: ['ip', 'installation_id'],
          indexed: ['subject_type + subject_value + active + expires_at'],
        },
        brokerAbuseSubjectHooks: {
          name: 'broker_abuse_subject_hooks',
          purpose:
            'denylist, reputation, and fast-revocation controls with observable outcomes',
          columns: [
            'id',
            'hook_kind',
            'subject_type',
            'subject_value',
            'outcome_code',
            'outcome_class',
            'outcome_subcode',
            'reason',
            'active',
            'created_at',
            'expires_at',
          ],
          hookKinds: ['denylist', 'reputation', 'revocation'],
          supportedSubjects: ['ip', 'installation_id', 'hardware_hash'],
          indexed: ['subject_type + subject_value + hook_kind + active + expires_at'],
        },
      },
    });
  });

  it('keeps persistence details out of the public foundation response', async () => {
    const response = await app.request('http://broker.test/v1/foundation');
    expect(response.status).toBe(200);

    const payload = (await response.json()) as Record<string, unknown>;

    expect(payload).not.toHaveProperty('persistence');
    expect(payload).not.toHaveProperty('brokerPersistenceModel');
    expect(payload).not.toHaveProperty('runtimeConfig');
  });

  it('ships a first D1 migration that creates the documented tables and indexes', () => {
    expect(BROKER_MIGRATION_FILENAMES).toEqual([
      '0000_define_broker_persistent_state.sql',
      '0001_add_abuse_hook_state.sql',
      '0001_harden_installation_public_inputs.sql',
      '0002_add_entitlement_verified_hardware_snapshot.sql',
    ]);
    expect(existsSync(FIRST_BROKER_MIGRATION)).toBe(true);
    expect(existsSync(LATEST_BROKER_MIGRATION)).toBe(true);
    if (!existsSync(FIRST_BROKER_MIGRATION) || !existsSync(LATEST_BROKER_MIGRATION)) {
      return;
    }

    const migration = readFileSync(FIRST_BROKER_MIGRATION, 'utf8');
    const abuseHooksMigration = readBrokerMigrationSql(
      '0001_add_abuse_hook_state.sql',
    );
    const hardeningMigration = readBrokerMigrationSql(
      '0001_harden_installation_public_inputs.sql',
    );
    const latestMigration = readFileSync(LATEST_BROKER_MIGRATION, 'utf8');

    expect(migration).toContain('CREATE TABLE broker_config');
    expect(migration).toContain('CREATE TABLE installations');
    expect(migration).toContain('CREATE TABLE openrouter_entitlements');
    expect(migration).toContain('device_public_key TEXT NOT NULL UNIQUE');
    expect(migration).toContain('hardware_hash TEXT');
    expect(migration).toContain('hardware_hash_salt_version INTEGER');
    expect(migration).toContain('challenge TEXT');
    expect(migration).toContain('challenge_expires_at TEXT');
    expect(migration).toContain('challenge_salt_version INTEGER');
    expect(migration).toContain('CHECK (length(installation_id) BETWEEN 1 AND 128)');
    expect(migration).toContain('CHECK (length(app_version) BETWEEN 1 AND 64)');
    expect(migration).toContain(
      'CHECK (hardware_hash IS NULL OR length(hardware_hash) BETWEEN 1 AND 128)',
    );
    expect(migration).toContain("INSERT INTO broker_config (key, value)");
    expect(migration).toContain("'abuse_controls'");
    expect(migration).toContain("CHECK(status IN ('pending_release', 'active', 'expired', 'revoked'))");
    expect(migration).toContain('managed_credential_ref TEXT UNIQUE');
    expect(migration).toContain('release_session_ref TEXT');
    expect(migration).toContain('release_token_hash TEXT');
    expect(migration).toContain('release_token_expires_at TEXT');
    expect(migration).not.toContain('verified_hardware_hash TEXT');
    expect(migration).not.toContain('verified_hardware_hash_salt_version INTEGER');
    expect(migration).toContain('CREATE INDEX idx_installations_hardware_hash');
    expect(migration).toContain('CREATE INDEX idx_installations_hardware_hash_salt_version');
    expect(migration).toContain('CREATE INDEX idx_installations_challenge_expires_at');
    expect(migration).toContain('CREATE INDEX idx_installations_last_seen_at');
    expect(migration).toContain('CREATE INDEX idx_openrouter_entitlements_status');
    expect(migration).toContain('CREATE INDEX idx_openrouter_entitlements_expires_at');
    expect(abuseHooksMigration).toContain('CREATE TABLE broker_request_events');
    expect(abuseHooksMigration).toContain('CREATE TABLE broker_velocity_cap_hooks');
    expect(abuseHooksMigration).toContain('CREATE TABLE broker_abuse_subject_hooks');
    expect(hardeningMigration).toContain('PRAGMA defer_foreign_keys = on');
    expect(hardeningMigration).toContain('CREATE TABLE installations_hardened');
    expect(hardeningMigration).toContain('CREATE TABLE openrouter_entitlements_hardened');
    expect(hardeningMigration).toContain('INSERT INTO installations_hardened');
    expect(hardeningMigration).toContain('INSERT INTO openrouter_entitlements_hardened');
    expect(hardeningMigration).toContain('DROP TABLE openrouter_entitlements;');
    expect(hardeningMigration).toContain('ALTER TABLE installations_hardened RENAME TO installations');
    expect(hardeningMigration).toContain('PRAGMA foreign_key_check');
    expect(latestMigration).toContain('ALTER TABLE openrouter_entitlements');
    expect(latestMigration).toContain('verified_hardware_hash TEXT');
    expect(latestMigration).toContain('verified_hardware_hash_salt_version INTEGER');
    expect(latestMigration).not.toContain('legacy_installation_id_mapping');
    expect(latestMigration).not.toContain('legacy-invalid-app-version');
  });
});
