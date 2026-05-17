import { DatabaseSync } from 'node:sqlite';

import { describe, expect, it } from 'vitest';

import { applyBrokerMigrations } from './test-support/migrations';

const VALID_REFERRAL_ID = '7KQ9M2';
const SECOND_VALID_REFERRAL_ID = 'ABCDEF';

function withMigratedDatabase(run: (db: DatabaseSync) => void): void {
  const db = new DatabaseSync(':memory:');
  try {
    applyBrokerMigrations(db);
    run(db);
  } finally {
    db.close();
  }
}

describe('broker referral persistence foundation', () => {
  it('migrates strict referral tables, nullable OAuth session referral input, and referral lookup indexes', () => {
    withMigratedDatabase((db) => {
      const tableStrictness = db
        .prepare(
          `SELECT name, strict
             FROM pragma_table_list
            WHERE name IN ('referral_codes', 'referral_rewards')
            ORDER BY name`,
        )
        .all() as Array<{ name: string; strict: number }>;
      expect(tableStrictness).toEqual([
        { name: 'referral_codes', strict: 1 },
        { name: 'referral_rewards', strict: 1 },
      ]);

      const referralCodeColumns = columnNames(db, 'referral_codes');
      expect(referralCodeColumns).toEqual([
        'referral_id',
        'owner_discord_user_ref',
        'owner_installation_id',
        'status',
        'created_at',
        'updated_at',
        'disabled_reason',
        'disabled_by',
        'disabled_at',
      ]);

      const referralRewardColumns = columnNames(db, 'referral_rewards');
      expect(referralRewardColumns).toEqual([
        'id',
        'referral_id',
        'referrer_discord_user_ref',
        'referrer_installation_id',
        'referred_discord_user_ref',
        'referred_installation_id',
        'referred_hardware_hash',
        'referred_hardware_hash_salt_version',
        'referred_bonus_status',
        'referrer_bonus_status',
        'skip_reason',
        'failure_reason',
        'referred_managed_credential_ref',
        'referrer_managed_credential_ref',
        'created_at',
        'updated_at',
        'credited_at',
        'attempt_ip_hash',
      ]);

      expect(columnNames(db, 'discord_oauth_sessions')).toContain('referral_id');

      const indexes = db
        .prepare(
          `SELECT name, sql
             FROM sqlite_schema
            WHERE type = 'index'
              AND tbl_name IN ('referral_codes', 'referral_rewards', 'discord_oauth_sessions')
            ORDER BY name`,
        )
        .all() as Array<{ name: string; sql: string | null }>;

      expect(indexes.map((index) => index.name)).toEqual(
        expect.arrayContaining([
          'idx_referral_codes_owner_discord_user_ref',
          'idx_referral_codes_owner_installation_id',
          'idx_referral_codes_status',
          'idx_referral_rewards_referral_id',
          'idx_referral_rewards_referrer_cap',
          'idx_referral_rewards_counted_referred_discord_user',
          'idx_referral_rewards_counted_referred_installation',
          'idx_referral_rewards_attempt_installation_time',
          'idx_referral_rewards_attempt_ip_hash_time',
          'idx_referral_rewards_referral_velocity',
          'idx_referral_rewards_referrer_velocity',
          'idx_discord_oauth_sessions_referral_id',
        ]),
      );
      expect(indexSql(indexes, 'idx_referral_rewards_counted_referred_discord_user')).toContain(
        "WHERE referred_bonus_status IN ('reserved', 'credited')",
      );
      expect(indexSql(indexes, 'idx_referral_rewards_counted_referred_installation')).toContain(
        "WHERE referred_bonus_status IN ('reserved', 'credited')",
      );
    });
  });

  it('enforces Referral ID shape, owned-code status values, and nullable OAuth session referral input', () => {
    withMigratedDatabase((db) => {
      const insertReferralCode = db.prepare(
        `INSERT INTO referral_codes (
          referral_id,
          owner_discord_user_ref,
          owner_installation_id,
          status
        ) VALUES (?, ?, ?, ?)`,
      );

      expect(() =>
        insertReferralCode.run(
          VALID_REFERRAL_ID,
          'ph-discord-user-v1_owner-valid',
          'owner-installation-valid',
          'active',
        ),
      ).not.toThrow();
      expect(() =>
        insertReferralCode.run(
          '7KO9M2',
          'ph-discord-user-v1_owner-confusing-o',
          null,
          'active',
        ),
      ).toThrow(/constraint/i);
      expect(() =>
        insertReferralCode.run(
          '7KQ9M',
          'ph-discord-user-v1_owner-short',
          null,
          'active',
        ),
      ).toThrow(/constraint/i);
      expect(() =>
        insertReferralCode.run(
          '7kq9m2',
          'ph-discord-user-v1_owner-lowercase',
          null,
          'active',
        ),
      ).toThrow(/constraint/i);
      expect(() =>
        insertReferralCode.run(
          SECOND_VALID_REFERRAL_ID,
          'ph-discord-user-v1_owner-invalid-status',
          null,
          'archived',
        ),
      ).toThrow(/constraint/i);

      const insertSession = db.prepare(
        `INSERT INTO discord_oauth_sessions (
          state_hash,
          installation_id,
          device_public_key,
          redirect_uri,
          pkce_code_verifier,
          issue_nonce_hash,
          fingerprint_salt_version,
          referral_id,
          status,
          created_at,
          expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)`,
      );

      expect(() =>
        insertSession.run(
          'state-hash-valid-referral',
          'install-session-valid-referral',
          'device-public-key-valid-referral',
          'http://127.0.0.1:62187/discord/callback',
          'pkce-code-verifier-valid-referral',
          'issue-nonce-valid-referral',
          7,
          VALID_REFERRAL_ID,
          '2026-05-14T06:00:00.000Z',
          '2026-05-14T06:05:00.000Z',
        ),
      ).not.toThrow();
      expect(() =>
        insertSession.run(
          'state-hash-null-referral',
          'install-session-null-referral',
          'device-public-key-null-referral',
          'http://127.0.0.1:62187/discord/callback',
          'pkce-code-verifier-null-referral',
          'issue-nonce-null-referral',
          7,
          null,
          '2026-05-14T06:00:00.000Z',
          '2026-05-14T06:05:00.000Z',
        ),
      ).not.toThrow();
      expect(() =>
        insertSession.run(
          'state-hash-invalid-referral',
          'install-session-invalid-referral',
          'device-public-key-invalid-referral',
          'http://127.0.0.1:62187/discord/callback',
          'pkce-code-verifier-invalid-referral',
          'issue-nonce-invalid-referral',
          7,
          '7KO9M2',
          '2026-05-14T06:00:00.000Z',
          '2026-05-14T06:05:00.000Z',
        ),
      ).toThrow(/constraint/i);
    });
  });

  it('enforces reward status/reason bounds and partial unique counted-referral constraints', () => {
    withMigratedDatabase((db) => {
      const insertReward = db.prepare(
        `INSERT INTO referral_rewards (
          referral_id,
          referrer_discord_user_ref,
          referrer_installation_id,
          referred_discord_user_ref,
          referred_installation_id,
          referred_hardware_hash,
          referred_hardware_hash_salt_version,
          referred_bonus_status,
          referrer_bonus_status,
          skip_reason,
          failure_reason,
          referred_managed_credential_ref,
          referrer_managed_credential_ref
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      );

      expect(() =>
        insertReward.run(
          VALID_REFERRAL_ID,
          'ph-discord-user-v1_referrer-a',
          'install-referrer-a',
          'ph-discord-user-v1_referred-a',
          'install-referred-a',
          'hardware-hash-a',
          7,
          'reserved',
          'pending',
          null,
          null,
          'managed-credential-referred-a',
          'managed-credential-referrer-a',
        ),
      ).not.toThrow();

      expect(() =>
        insertReward.run(
          '7KO9M2',
          'ph-discord-user-v1_referrer-invalid-id',
          'install-referrer-invalid-id',
          'ph-discord-user-v1_referred-invalid-id',
          'install-referred-invalid-id',
          'hardware-hash-invalid-id',
          7,
          'skipped',
          'skipped',
          'unknown_referral_id',
          null,
          null,
          null,
        ),
      ).toThrow(/constraint/i);
      expect(() =>
        insertReward.run(
          VALID_REFERRAL_ID,
          'ph-discord-user-v1_referrer-invalid-status',
          'install-referrer-invalid-status',
          'ph-discord-user-v1_referred-invalid-status',
          'install-referred-invalid-status',
          'hardware-hash-invalid-status',
          7,
          'queued',
          'pending',
          null,
          null,
          null,
          null,
        ),
      ).toThrow(/constraint/i);
      expect(() =>
        insertReward.run(
          VALID_REFERRAL_ID,
          'ph-discord-user-v1_referrer-long-reason',
          'install-referrer-long-reason',
          'ph-discord-user-v1_referred-long-reason',
          'install-referred-long-reason',
          'hardware-hash-long-reason',
          7,
          'skipped',
          'skipped',
          'x'.repeat(65),
          null,
          null,
          null,
        ),
      ).toThrow(/constraint/i);

      expect(() =>
        insertReward.run(
          VALID_REFERRAL_ID,
          'ph-discord-user-v1_referrer-duplicate-discord',
          'install-referrer-duplicate-discord',
          'ph-discord-user-v1_referred-a',
          'install-referred-b',
          'hardware-hash-b',
          7,
          'credited',
          'credited',
          null,
          null,
          'managed-credential-referred-b',
          'managed-credential-referrer-duplicate-discord',
        ),
      ).toThrow(/unique|constraint/i);
      expect(() =>
        insertReward.run(
          VALID_REFERRAL_ID,
          'ph-discord-user-v1_referrer-duplicate-installation',
          'install-referrer-duplicate-installation',
          'ph-discord-user-v1_referred-b',
          'install-referred-a',
          'hardware-hash-c',
          7,
          'reserved',
          'pending',
          null,
          null,
          'managed-credential-referred-c',
          'managed-credential-referrer-duplicate-installation',
        ),
      ).toThrow(/unique|constraint/i);

      expect(() =>
        insertReward.run(
          VALID_REFERRAL_ID,
          null,
          null,
          'ph-discord-user-v1_referred-a',
          'install-referred-a',
          'hardware-hash-skipped',
          7,
          'skipped',
          'skipped',
          'unknown_referral_id',
          null,
          null,
          null,
        ),
      ).not.toThrow();
    });
  });

  it('does not cascade-delete referral code or reward ledger history when installations age out', () => {
    withMigratedDatabase((db) => {
      db.prepare(
        'INSERT INTO installations (installation_id, device_public_key, app_version) VALUES (?, ?, ?)',
      ).run('install-owner-aging-out', 'device-owner-aging-out', '1.0.0');

      db.prepare(
        `INSERT INTO referral_codes (
          referral_id,
          owner_discord_user_ref,
          owner_installation_id,
          status
        ) VALUES (?, ?, ?, 'active')`,
      ).run(VALID_REFERRAL_ID, 'ph-discord-user-v1_owner-aging-out', 'install-owner-aging-out');

      db.prepare(
        `INSERT INTO referral_rewards (
          referral_id,
          referrer_discord_user_ref,
          referrer_installation_id,
          referred_discord_user_ref,
          referred_installation_id,
          referred_hardware_hash,
          referred_hardware_hash_salt_version,
          referred_bonus_status,
          referrer_bonus_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'credited', 'credited')`,
      ).run(
        VALID_REFERRAL_ID,
        'ph-discord-user-v1_owner-aging-out',
        'install-owner-aging-out',
        'ph-discord-user-v1_referred-aging-out',
        'install-referred-aging-out',
        'hardware-hash-aging-out',
        7,
      );

      const cascadingForeignKeys = db
        .prepare(
          `SELECT table_name, on_delete
             FROM (
               SELECT 'referral_codes' AS table_name, upper(on_delete) AS on_delete
                 FROM pragma_foreign_key_list('referral_codes')
               UNION ALL
               SELECT 'referral_rewards' AS table_name, upper(on_delete) AS on_delete
                 FROM pragma_foreign_key_list('referral_rewards')
             )
            WHERE on_delete = 'CASCADE'`,
        )
        .all();
      expect(cascadingForeignKeys).toEqual([]);

      db.prepare('DELETE FROM installations WHERE installation_id = ?').run(
        'install-owner-aging-out',
      );

      expect(countRows(db, 'referral_codes')).toBe(1);
      expect(countRows(db, 'referral_rewards')).toBe(1);
    });
  });
});

function columnNames(db: DatabaseSync, tableName: string): string[] {
  return (db
    .prepare(`SELECT name FROM pragma_table_info('${tableName}') ORDER BY cid`)
    .all() as Array<{ name: string }>).map((column) => column.name);
}

function indexSql(
  indexes: Array<{ name: string; sql: string | null }>,
  indexName: string,
): string {
  const sql = indexes.find((index) => index.name === indexName)?.sql;
  if (!sql) {
    throw new Error(`missing index SQL for ${indexName}`);
  }
  return sql;
}

function countRows(db: DatabaseSync, tableName: string): number {
  const row = db
    .prepare(`SELECT COUNT(*) AS count FROM ${tableName}`)
    .get() as { count: number };
  return Number(row.count);
}
