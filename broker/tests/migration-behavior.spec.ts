import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

import { describe, expect, it } from 'vitest';

const FIRST_MIGRATION = new URL(
  '../migrations/0000_define_broker_persistent_state.sql',
  import.meta.url,
);

const MIGRATION_SQL = readFileSync(FIRST_MIGRATION, 'utf8');

function withMigratedDatabase(run: (db: DatabaseSync) => void): void {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec(MIGRATION_SQL);
    run(db);
  } finally {
    db.close();
  }
}

describe('broker migration behavior', () => {
  it('seeds the expected broker_config rows and enforces supported keys with valid JSON', () => {
    withMigratedDatabase((db) => {
      const rows = db
        .prepare('SELECT key, value FROM broker_config ORDER BY key')
        .all() as Array<{ key: string; value: string }>;

      expect(rows.map(({ key }) => key)).toEqual([
        'abuse_controls',
        'fingerprint_salt',
      ]);
      expect(rows.map(({ value }) => JSON.parse(value))).toEqual([
        {
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
        {
          current: {
            version: 1,
            salt: '__BOOTSTRAP_REQUIRED__',
          },
          previous: null,
          rotated_at: null,
        },
      ]);

      const insertConfig = db.prepare(
        'INSERT INTO broker_config (key, value) VALUES (?, ?)',
      );
      const updateConfig = db.prepare(
        'UPDATE broker_config SET value = ? WHERE key = ?',
      );

      expect(() => insertConfig.run('unsupported_key', '{}')).toThrow(/constraint/i);
      expect(() => updateConfig.run('not-json', 'abuse_controls')).toThrow(
        /constraint|json/i,
      );
    });
  });

  it('enforces paired NULL rules on installation challenge and hardware hash fields', () => {
    withMigratedDatabase((db) => {
      const insertInstallation = db.prepare(
        `INSERT INTO installations (
          installation_id,
          device_public_key,
          hardware_hash,
          hardware_hash_salt_version,
          app_version,
          challenge,
          challenge_expires_at,
          challenge_salt_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      );

      expect(() =>
        insertInstallation.run(
          'install-challenge-invalid',
          'device-public-key-challenge-invalid',
          null,
          null,
          '1.0.0',
          'challenge-token',
          null,
          null,
        ),
      ).toThrow(/constraint/i);

      expect(() =>
        insertInstallation.run(
          'install-hash-invalid',
          'device-public-key-hash-invalid',
          'hardware-hash',
          null,
          '1.0.0',
          null,
          null,
          null,
        ),
      ).toThrow(/constraint/i);

      expect(() =>
        insertInstallation.run(
          'install-valid',
          'device-public-key-valid',
          null,
          null,
          '1.0.0',
          'challenge-token',
          '2026-04-08T06:00:00Z',
          2,
        ),
      ).not.toThrow();
    });
  });

  it('enforces release-session all-or-none fields, unique release token hashes, and cascades entitlement deletion', () => {
    withMigratedDatabase((db) => {
      db.prepare(
        'INSERT INTO installations (installation_id, device_public_key, app_version) VALUES (?, ?, ?)',
      ).run('install-a', 'device-public-key-a', '1.0.0');
      db.prepare(
        'INSERT INTO installations (installation_id, device_public_key, app_version) VALUES (?, ?, ?)',
      ).run('install-b', 'device-public-key-b', '1.0.0');

      const insertEntitlement = db.prepare(
        `INSERT INTO openrouter_entitlements (
          installation_id,
          status,
          budget_usd,
          managed_credential_ref,
          issued_at,
          expires_at,
          release_session_ref,
          release_token_hash,
          release_token_expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      );

      expect(() =>
        insertEntitlement.run(
          'install-a',
          'pending_release',
          0.07,
          null,
          null,
          null,
          'release-session-a',
          null,
          null,
        ),
      ).toThrow(/constraint/i);

      insertEntitlement.run(
        'install-a',
        'pending_release',
        0.07,
        null,
        null,
        null,
        'release-session-a',
        'token-hash-1',
        '2026-04-08T06:15:00Z',
      );

      expect(() =>
        insertEntitlement.run(
          'install-b',
          'pending_release',
          0.07,
          null,
          null,
          null,
          'release-session-b',
          'token-hash-1',
          '2026-04-08T06:15:00Z',
        ),
      ).toThrow(/unique|constraint/i);

      db.prepare('DELETE FROM installations WHERE installation_id = ?').run(
        'install-a',
      );

      const entitlementCount = db
        .prepare('SELECT COUNT(*) AS count FROM openrouter_entitlements')
        .get() as { count: number };

      expect(entitlementCount.count).toBe(0);
    });
  });
});
