import { afterEach, describe, expect, it, vi } from 'vitest';

import { signCanonicalIssueRequest } from './test-support/ed25519';
import { createPendingReleaseSession } from './test-support/openrouter-issue';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { postIssue } from './test-support/trial-api';

describe('POST /v1/providers/openrouter/issue idempotency', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('reuses the same active entitlement for repeated same-session issue requests', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-idempotent',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-issue-idempotent',
    });
    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-idempotent',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:00:45.000Z',
    });

    const firstResponse = await postIssue(env, requestBody);
    const secondResponse = await postIssue(env, requestBody);

    expect(firstResponse.status).toBe(200);
    expect(secondResponse.status).toBe(200);

    const firstPayload = (await firstResponse.json()) as Record<string, unknown>;
    const secondPayload = (await secondResponse.json()) as Record<string, unknown>;

    expect(secondPayload).toEqual(firstPayload);

    const entitlement = env.__db
      .prepare(
        `SELECT status, managed_credential_ref, issued_at, expires_at,
                release_session_ref, release_token_hash, release_token_expires_at
           FROM openrouter_entitlements
          WHERE installation_id = ?`,
      )
      .get('install-issue-idempotent') as Record<string, unknown>;

    expect(entitlement).toEqual({
      status: 'active',
      managed_credential_ref: firstPayload.managed_credential_ref,
      issued_at: '2026-04-08T06:00:00.000Z',
      expires_at: '2026-10-08T06:00:00.000Z',
      release_session_ref: expect.any(String),
      release_token_hash: expect.any(String),
      release_token_expires_at: release.releaseTokenExpiresAt,
    });
  });

  it('rejects replaying the same signed request after the release token expires', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-expired-replay',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-issue-expired-replay',
    });

    vi.setSystemTime(new Date('2026-04-08T06:14:45Z'));

    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-expired-replay',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:14:45.000Z',
    });

    const firstResponse = await postIssue(env, requestBody);
    expect(firstResponse.status).toBe(200);

    vi.setSystemTime(new Date('2026-04-08T06:15:01Z'));

    const replayResponse = await postIssue(env, requestBody);

    expect(replayResponse.status).toBe(410);
    await expect(replayResponse.json()).resolves.toEqual({
      error: {
        code: 'release_token_expired',
        message: 'release_token has expired and must be reissued',
      },
    });
  });
});
