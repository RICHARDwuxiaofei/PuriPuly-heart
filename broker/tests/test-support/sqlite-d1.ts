import { DatabaseSync } from 'node:sqlite';

import { applyBrokerMigrations } from './migrations';

type BindValue = string | number | bigint | null;

interface SqliteD1Hooks {
  beforeFirst?: (input: { sql: string; params: BindValue[] }) => Promise<void> | void;
  beforeRun?: (input: { sql: string; params: BindValue[] }) => Promise<void> | void;
}

class SqliteD1PreparedStatement {
  constructor(
    private readonly db: DatabaseSync,
    private readonly sql: string,
    private readonly params: BindValue[] = [],
    private readonly hooks: SqliteD1Hooks = {},
  ) {}

  bind(...params: BindValue[]): SqliteD1PreparedStatement {
    return new SqliteD1PreparedStatement(this.db, this.sql, params, this.hooks);
  }

  async first<T = Record<string, unknown>>(
    columnName?: string,
  ): Promise<T | null> {
    await this.hooks.beforeFirst?.({
      sql: this.sql,
      params: this.params,
    });

    const row = this.db.prepare(this.sql).get(...this.params) as
      | Record<string, unknown>
      | undefined;

    if (!row) {
      return null;
    }

    if (columnName) {
      return ((row[columnName] as T | undefined) ?? null) as T | null;
    }

    return row as T;
  }

  async run(): Promise<D1Result> {
    await this.hooks.beforeRun?.({
      sql: this.sql,
      params: this.params,
    });

    const result = this.db.prepare(this.sql).run(...this.params);

    return {
      results: [],
      success: true,
      meta: {
        changed_db: false,
        changes: Number(result.changes),
        duration: 0,
        last_row_id: Number(result.lastInsertRowid),
        rows_read: 0,
        rows_written: Number(result.changes),
        served_by: 'sqlite-test-double',
        size_after: 0,
      },
    };
  }

  async all<T = Record<string, unknown>>(): Promise<D1Result<T>> {
    const results = this.db.prepare(this.sql).all(...this.params) as T[];

    return {
      success: true,
      results,
      meta: {
        changed_db: false,
        changes: 0,
        duration: 0,
        last_row_id: 0,
        rows_read: results.length,
        rows_written: 0,
        served_by: 'sqlite-test-double',
        size_after: 0,
      },
    };
  }
}

class SqliteD1Database {
  constructor(
    private readonly db: DatabaseSync,
    private readonly hooks: SqliteD1Hooks = {},
  ) {}

  prepare(sql: string): SqliteD1PreparedStatement {
    return new SqliteD1PreparedStatement(this.db, sql, [], this.hooks);
  }
}

export interface TestBrokerEnv extends Record<string, unknown> {
  BROKER_DB: D1Database;
  OPENROUTER_MANAGEMENT_API_KEY: string;
  OPENROUTER_MANAGED_GUARDRAIL_ID: string;
  OPENROUTER_MANAGED_API_KEY: string;
  __db: DatabaseSync;
}

export function createTestBrokerEnv(options: SqliteD1Hooks = {}): TestBrokerEnv {
  const db = new DatabaseSync(':memory:');
  applyBrokerMigrations(db);
  db.prepare('UPDATE broker_config SET value = ? WHERE key = ?').run(
    JSON.stringify({
      current: {
        version: 7,
        salt: 'shared-server-fingerprint-salt',
      },
      previous: null,
      rotated_at: null,
    }),
    'fingerprint_salt',
  );

  return {
    BROKER_DB: new SqliteD1Database(db, options) as unknown as D1Database,
    OPENROUTER_MANAGEMENT_API_KEY: 'test-management-api-key',
    OPENROUTER_MANAGED_GUARDRAIL_ID: 'test-managed-guardrail-id',
    OPENROUTER_MANAGED_API_KEY: 'test-managed-api-key',
    __db: db,
  };
}

export function insertEntitlement(
  env: TestBrokerEnv,
  input: {
    installation_id: string;
    status: 'pending_release' | 'active' | 'expired' | 'revoked';
    budget_usd: number;
    managed_credential_ref?: string | null;
    issued_at?: string | null;
    expires_at?: string | null;
    release_session_ref?: string | null;
    release_token_hash?: string | null;
    release_token_expires_at?: string | null;
    verified_hardware_hash?: string | null;
    verified_hardware_hash_salt_version?: number | null;
  },
): void {
  env.__db
    .prepare(
      `INSERT INTO openrouter_entitlements (
          installation_id,
          status,
          budget_usd,
          managed_credential_ref,
          issued_at,
          expires_at,
          release_session_ref,
          release_token_hash,
          release_token_expires_at,
          verified_hardware_hash,
          verified_hardware_hash_salt_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      input.installation_id,
      input.status,
      input.budget_usd,
      input.managed_credential_ref ?? null,
      input.issued_at ?? null,
      input.expires_at ?? null,
      input.release_session_ref ?? null,
      input.release_token_hash ?? null,
      input.release_token_expires_at ?? null,
      input.verified_hardware_hash ?? null,
      input.verified_hardware_hash_salt_version ?? null,
    );
}
