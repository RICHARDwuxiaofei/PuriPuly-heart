import { existsSync, readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const FIRST_MIGRATION = new URL(
  '../migrations/0000_define_broker_persistent_state.sql',
  import.meta.url,
);

describe('broker persistence retention model', () => {
  it('retains inactive pending_release installations for 30 days before installation-level cleanup', async () => {
    const contract = await import('../src/contract');

    expect(contract).toHaveProperty('BROKER_RETENTION_POLICY', {
      pendingRelease: {
        statuses: ['pending_release'],
        inactiveDays: 30,
        reference: 'installations.last_seen_at',
        deleteFrom: 'installations',
        cascadesTo: ['openrouter_entitlements'],
      },
      terminal: {
        statuses: ['expired', 'revoked'],
        inactiveDays: 90,
        reference: 'max(installations.last_seen_at, openrouter_entitlements.expires_at)',
        deleteFrom: 'installations',
        cascadesTo: ['openrouter_entitlements'],
      },
    });
  });

  it('keeps entitlement state as one in-place row per installation instead of append-only history', async () => {
    const contract = await import('../src/contract');

    expect(contract).toHaveProperty(
      'BROKER_PERSISTENCE_MODEL.tables.openrouterEntitlements.updateStrategy',
      'in-place',
    );
    expect(contract).toHaveProperty(
      'BROKER_PERSISTENCE_MODEL.tables.openrouterEntitlements.rowCardinality',
      'zero-or-one-row-per-installation',
    );
    expect(contract).toHaveProperty(
      'BROKER_PERSISTENCE_MODEL.tables.openrouterEntitlements.liveRemainingBudgetSource',
      'OpenRouter metadata',
    );
  });

  it('uses cascading delete from installations so retention cleanup removes entitlement rows too', () => {
    expect(existsSync(FIRST_MIGRATION)).toBe(true);
    if (!existsSync(FIRST_MIGRATION)) {
      return;
    }

    const migration = readFileSync(FIRST_MIGRATION, 'utf8');

    expect(migration).toContain(
      'installation_id TEXT PRIMARY KEY REFERENCES installations(installation_id) ON DELETE CASCADE',
    );
  });
});
