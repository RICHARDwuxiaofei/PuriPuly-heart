import { describe, expect, it } from 'vitest';

import { BROKER_PERSISTENCE_MODEL } from '../src/contract';
import { readBrokerMigrationSql } from './test-support/migrations';
import { createTestBrokerEnv, insertEntitlement } from './test-support/sqlite-d1';

const OPENROUTER_ENTITLEMENT_COLUMNS = [
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
];

describe('openrouter entitlement schema', () => {
  it('documents verified hardware snapshot columns in the persistence contract', () => {
    expect(BROKER_PERSISTENCE_MODEL.tables.openrouterEntitlements.columns).toEqual(
      OPENROUTER_ENTITLEMENT_COLUMNS,
    );
  });

  it('ships verified hardware snapshot columns in a forward entitlement migration', () => {
    expect(
      readBrokerMigrationSql('0000_define_broker_persistent_state.sql'),
    ).not.toContain('verified_hardware_hash TEXT');
    expect(
      readBrokerMigrationSql('0001_harden_installation_public_inputs.sql'),
    ).not.toContain('verified_hardware_hash TEXT');

    const migration = readBrokerMigrationSql(
      '0002_add_entitlement_verified_hardware_snapshot.sql',
    );

    expect(migration).toContain('ALTER TABLE openrouter_entitlements');
    expect(migration).toContain('verified_hardware_hash TEXT');
    expect(migration).toContain('verified_hardware_hash_salt_version INTEGER');
  });

  it('applies the verified hardware snapshot columns to migrated test databases', () => {
    const env = createTestBrokerEnv();
    const columns = env.__db
      .prepare("SELECT name FROM pragma_table_info('openrouter_entitlements') ORDER BY cid")
      .all() as Array<{ name: string }>;

    expect(columns.map((column) => column.name)).toEqual(OPENROUTER_ENTITLEMENT_COLUMNS);
  });

  it('seeds child-key management bindings in the sqlite D1 test env', () => {
    const env = createTestBrokerEnv() as Record<string, unknown>;

    expect(env.OPENROUTER_MANAGED_API_KEY).toBe('test-managed-api-key');
    expect(env.OPENROUTER_MANAGEMENT_API_KEY).toBe('test-management-api-key');
    expect(env.OPENROUTER_MANAGED_GUARDRAIL_ID).toBe('test-managed-guardrail-id');
  });

  it('lets test helpers persist verified hardware snapshots on entitlement rows', () => {
    const env = createTestBrokerEnv();
    env.__db
      .prepare(
        `INSERT INTO installations (installation_id, device_public_key, app_version)
         VALUES (?, ?, ?)`,
      )
      .run('install-snapshot', 'device-public-key-snapshot', '1.0.0');

    insertEntitlement(env, {
      installation_id: 'install-snapshot',
      status: 'active',
      budget_usd: 0.05,
      managed_credential_ref: 'managed-credential-snapshot',
      verified_hardware_hash: 'verified-hardware-hash',
      verified_hardware_hash_salt_version: 7,
    });

    const row = env.__db
      .prepare(
        `SELECT verified_hardware_hash, verified_hardware_hash_salt_version
           FROM openrouter_entitlements
          WHERE installation_id = ?`,
      )
      .get('install-snapshot') as {
      verified_hardware_hash: string | null;
      verified_hardware_hash_salt_version: number | null;
    };

    expect(row).toEqual({
      verified_hardware_hash: 'verified-hardware-hash',
      verified_hardware_hash_salt_version: 7,
    });
  });
});
