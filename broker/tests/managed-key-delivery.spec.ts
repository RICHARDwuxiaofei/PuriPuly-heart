import { describe, expect, it, vi, afterEach } from 'vitest';

import app from '../src/index';
import {
  acknowledgeManagedKeyDelivery,
  createManagedKeyDelivery,
  hashDeliveryAckToken,
  listStalePendingManagedKeyDeliveries,
} from '../src/managed-key-delivery';
import { normalizedErrorEnvelope } from './test-support/errors';
import { BROKER_MIGRATION_FILENAMES } from './test-support/migrations';
import { createTestBrokerEnv } from './test-support/sqlite-d1';

describe('managed key delivery ACK foundation', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('orders migration 0012 after telemetry 0011 and creates delivery ACK schema', () => {
    const env = createTestBrokerEnv();

    expect(BROKER_MIGRATION_FILENAMES.at(-2)).toBe('0011_add_telemetry_active_days.sql');
    expect(BROKER_MIGRATION_FILENAMES.at(-1)).toBe('0012_add_managed_key_delivery_ack.sql');

    env.__db
      .prepare(
        `INSERT INTO installations (installation_id, device_public_key, app_version)
         VALUES (?, ?, ?)`,
      )
      .run('discord-installation', 'device-public-key', '1.0.0');

    const discordDeliveryPending = env.__db
      .prepare(
        `INSERT INTO openrouter_entitlements (
            installation_id,
            status,
            budget_usd,
            discord_issue_status
          ) VALUES (?, 'pending_release', 0, 'delivery_pending')`,
      )
      .run('discord-installation');
    expect(Number(discordDeliveryPending.changes)).toBe(1);

    const qqDeliveryPending = env.__db
      .prepare(
        `INSERT INTO qq_managed_entitlements (
            qq_subject_ref,
            status,
            issue_ref,
            managed_credential_ref,
            budget_usd,
            reserved_at,
            issued_at,
            expires_at
          ) VALUES (?, 'delivery_pending', ?, ?, 0, ?, ?, ?)`,
      )
      .run(
        'ph-qq-subject-v1_schema',
        'qq-issue-schema',
        'managed-credential-schema',
        '2026-07-05T00:00:00.000Z',
        '2026-07-05T00:00:00.000Z',
        '2026-08-05T00:00:00.000Z',
      );
    expect(Number(qqDeliveryPending.changes)).toBe(1);

    const deliveryColumns = env.__db
      .prepare("SELECT name FROM pragma_table_info('managed_key_deliveries') ORDER BY cid")
      .all() as Array<{ name: string }>;
    expect(deliveryColumns.map(({ name }) => name)).toEqual([
      'delivery_id',
      'issue_source',
      'subject_ref',
      'installation_id',
      'managed_credential_ref',
      'ack_token_hash',
      'status',
      'created_at',
      'expires_at',
      'acknowledged_at',
      'failed_at',
      'failure_reason',
    ]);

    const indexes = env.__db
      .prepare("SELECT name FROM pragma_index_list('managed_key_deliveries') ORDER BY name")
      .all() as Array<{ name: string }>;
    expect(indexes.map(({ name }) => name)).toEqual(
      expect.arrayContaining([
        'idx_managed_key_deliveries_issue_source_created_at',
        'idx_managed_key_deliveries_managed_credential_ref',
        'idx_managed_key_deliveries_status_expires_at',
      ]),
    );
  });

  it('creates pending delivery rows with hashed ACK tokens only', async () => {
    const env = createTestBrokerEnv();
    const createdAt = new Date('2026-07-05T00:00:00.000Z');
    const expiresAt = new Date('2026-07-05T00:10:00.000Z');

    const delivery = await createManagedKeyDelivery(env.BROKER_DB, {
      issueSource: 'discord',
      subjectRef: 'ph-discord-user-v1_subject',
      installationId: 'installation-1',
      managedCredentialRef: 'managed-credential-1',
      createdAt,
      expiresAt,
    });

    const row = env.__db
      .prepare('SELECT * FROM managed_key_deliveries WHERE delivery_id = ?')
      .get(delivery.deliveryId) as { ack_token_hash: string; status: string; created_at: string; expires_at: string };

    expect(row.status).toBe('pending');
    expect(row.created_at).toBe(createdAt.toISOString());
    expect(row.expires_at).toBe(expiresAt.toISOString());
    expect(row.ack_token_hash).toBe(await hashDeliveryAckToken(delivery.deliveryAckToken));
    expect(row.ack_token_hash).not.toBe(delivery.deliveryAckToken);
    expect(JSON.stringify(row)).not.toContain(delivery.deliveryAckToken);
  });

  it('acknowledges once and treats duplicate valid ACK as idempotent', async () => {
    const env = createTestBrokerEnv();
    const delivery = await createManagedKeyDelivery(env.BROKER_DB, {
      issueSource: 'qq',
      subjectRef: 'ph-qq-subject-v1_subject',
      managedCredentialRef: 'managed-credential-2',
      createdAt: new Date('2026-07-05T00:00:00.000Z'),
      expiresAt: new Date('2026-07-05T00:10:00.000Z'),
    });

    const first = await acknowledgeManagedKeyDelivery(env.BROKER_DB, {
      deliveryId: delivery.deliveryId,
      managedCredentialRef: 'managed-credential-2',
      deliveryAckToken: delivery.deliveryAckToken,
      now: new Date('2026-07-05T00:01:00.000Z'),
    });
    const second = await acknowledgeManagedKeyDelivery(env.BROKER_DB, {
      deliveryId: delivery.deliveryId,
      managedCredentialRef: 'managed-credential-2',
      deliveryAckToken: delivery.deliveryAckToken,
      now: new Date('2026-07-05T00:02:00.000Z'),
    });

    expect(first).toEqual({ ok: true, status: 'acknowledged' });
    expect(second).toEqual({ ok: true, status: 'already_acknowledged' });
    expect(
      env.__db
        .prepare("SELECT COUNT(*) AS count FROM managed_key_deliveries WHERE status = 'acknowledged'")
        .get(),
    ).toEqual({ count: 1 });
  });

  it('lists stale pending deliveries for cleanup helpers', async () => {
    const env = createTestBrokerEnv();
    const stale = await createManagedKeyDelivery(env.BROKER_DB, {
      issueSource: 'discord',
      managedCredentialRef: 'managed-credential-stale',
      createdAt: new Date('2026-07-05T00:00:00.000Z'),
      expiresAt: new Date('2026-07-05T00:05:00.000Z'),
    });
    await createManagedKeyDelivery(env.BROKER_DB, {
      issueSource: 'discord',
      managedCredentialRef: 'managed-credential-fresh',
      createdAt: new Date('2026-07-05T00:00:00.000Z'),
      expiresAt: new Date('2026-07-05T00:30:00.000Z'),
    });

    const staleRows = await listStalePendingManagedKeyDeliveries(env.BROKER_DB, {
      now: new Date('2026-07-05T00:10:00.000Z'),
      limit: 10,
    });

    expect(staleRows.map((row) => row.delivery_id)).toEqual([stale.deliveryId]);
  });

  it('serves ACK route success, duplicate, and safe public errors', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-05T00:01:00.000Z'));
    const env = createTestBrokerEnv();
    const delivery = await createManagedKeyDelivery(env.BROKER_DB, {
      issueSource: 'discord',
      managedCredentialRef: 'managed-credential-route',
      createdAt: new Date('2026-07-05T00:00:00.000Z'),
      expiresAt: new Date('2026-07-05T00:10:00.000Z'),
    });

    const payload = {
      delivery_id: delivery.deliveryId,
      managed_credential_ref: 'managed-credential-route',
      delivery_ack_token: delivery.deliveryAckToken,
    };
    const first = await postAck(env, payload);
    const second = await postAck(env, payload);
    const invalid = await postAck(env, { ...payload, delivery_ack_token: 'wrong-token' });
    const mismatched = await postAck(env, { ...payload, managed_credential_ref: 'other-credential' });
    const malformed = await postAck(env, { ...payload, delivery_ack_token: '' });

    expect(first.status).toBe(200);
    await expect(first.json()).resolves.toEqual({ ok: true, status: 'acknowledged' });
    expect(second.status).toBe(200);
    await expect(second.json()).resolves.toEqual({ ok: true, status: 'already_acknowledged' });
    expect(invalid.status).toBe(404);
    const invalidBody = await invalid.json();
    expect(invalidBody).toEqual(
      normalizedErrorEnvelope({
        code: 'invalid_request',
        class: 'terminal',
        subcode: 'delivery_ack_invalid',
        message: 'delivery acknowledgement is invalid',
      }),
    );
    expect(mismatched.status).toBe(409);
    await expect(mismatched.json()).resolves.toMatchObject({
      error: { subcode: 'delivery_ack_mismatched' },
    });
    expect(malformed.status).toBe(400);
    await expect(malformed.json()).resolves.toMatchObject({
      error: { subcode: 'delivery_ack_malformed' },
    });
    expect(JSON.stringify(invalidBody)).not.toContain('wrong-token');
  });

  it('rejects expired ACK route attempts while leaving delivery pending for cleanup', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-05T00:11:00.000Z'));
    const env = createTestBrokerEnv();
    const delivery = await createManagedKeyDelivery(env.BROKER_DB, {
      issueSource: 'qq',
      managedCredentialRef: 'managed-credential-expired',
      createdAt: new Date('2026-07-05T00:00:00.000Z'),
      expiresAt: new Date('2026-07-05T00:10:00.000Z'),
    });

    const response = await postAck(env, {
      delivery_id: delivery.deliveryId,
      managed_credential_ref: 'managed-credential-expired',
      delivery_ack_token: delivery.deliveryAckToken,
    });

    expect(response.status).toBe(410);
    await expect(response.json()).resolves.toMatchObject({
      error: { subcode: 'delivery_ack_expired' },
    });
    expect(
      env.__db
        .prepare('SELECT status, failure_reason FROM managed_key_deliveries WHERE delivery_id = ?')
        .get(delivery.deliveryId),
    ).toEqual({ status: 'pending', failure_reason: null });

    const staleRows = await listStalePendingManagedKeyDeliveries(env.BROKER_DB, {
      now: new Date('2026-07-05T00:11:00.000Z'),
      limit: 10,
    });
    expect(staleRows.map((row) => row.delivery_id)).toEqual([delivery.deliveryId]);
  });
});

async function postAck(env: ReturnType<typeof createTestBrokerEnv>, payload: unknown): Promise<Response> {
  return app.request(
    'http://broker.test/v1/providers/openrouter/managed-key-delivery/ack',
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    },
    env,
  );
}
