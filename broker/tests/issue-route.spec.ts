import { afterEach, describe, expect, it, vi } from 'vitest';

import { signCanonicalIssueRequest } from './test-support/ed25519';
import { normalizedErrorEnvelope } from './test-support/errors';
import {
  createPendingReleaseSession,
  mockOpenRouterManagementApi,
} from './test-support/openrouter-issue';
import { sha256Base64Url } from './test-support/hash';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { postIssue } from './test-support/trial-api';

describe('POST /v1/providers/openrouter/issue route contract', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('consumes a pending_release token and activates the entitlement', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const managementApi = mockOpenRouterManagementApi();
    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-route',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-issue-route',
    });
    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-route',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      hardware_hash: release.hardwareHash,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:00:45.000Z',
    });

    const response = await postIssue(env, requestBody);
    expect(response.status).toBe(200);

    const payload = (await response.json()) as {
      openrouter_api_key: string;
      managed_credential_ref: string;
      expires_at: string;
      budget_usd: number;
      model: string;
      managed_state: {
        lifecycle: string;
        managed_availability: boolean;
      };
    };
    const entitlement = env.__db
      .prepare(
        `SELECT status, budget_usd, managed_credential_ref, issued_at, expires_at,
                release_session_ref, release_token_hash, release_token_expires_at
           FROM openrouter_entitlements
          WHERE installation_id = ?`,
      )
      .get('install-issue-route') as Record<string, unknown>;

    expect(payload.openrouter_api_key).toBe(managementApi.childKey.rawKey);
    expect(payload.managed_credential_ref).toBe(managementApi.childKey.hash);
    expect(payload.managed_state).toEqual({
      lifecycle: 'active',
      managed_availability: true,
    });
    expect(payload.expires_at).toBe('2026-10-08T06:00:00.000Z');
    expect(payload.budget_usd).toBe(0.07);
    expect(payload.model).toBe('google/gemma-4-26b-a4b-it');
    expect(entitlement).toEqual({
      status: 'active',
      budget_usd: 0.07,
      managed_credential_ref: managementApi.childKey.hash,
      issued_at: '2026-04-08T06:00:00.000Z',
      expires_at: '2026-10-08T06:00:00.000Z',
      release_session_ref: expect.any(String),
      release_token_hash: expect.any(String),
      release_token_expires_at: release.releaseTokenExpiresAt,
    });
    expect(payload.managed_credential_ref).not.toBe(payload.openrouter_api_key);
    expect(payload.openrouter_api_key).not.toBe(env.OPENROUTER_MANAGED_API_KEY);
    await expect(sha256Base64Url(release.releaseToken)).resolves.toBe(
      entitlement.release_token_hash,
    );
    expect(managementApi.fetchMock).toHaveBeenCalledTimes(2);
  });

  it('rejects issue when the request hardware_hash does not match the verified snapshot', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const managementApi = mockOpenRouterManagementApi();
    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-hardware-mismatch',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-verified',
    });
    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-hardware-mismatch',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      hardware_hash: 'hardware-hash-different',
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:00:45.000Z',
    });

    const response = await postIssue(env, requestBody);

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'hardware_duplicate',
        message: 'hardware_hash no longer matches the verified release session',
        managedState: {
          lifecycle: 'pending_release',
          managed_availability: true,
        },
        currentEntitlement: {
          provider: 'OpenRouter',
          budget_usd: 0.07,
          issued_at: null,
          expires_at: null,
        },
      }),
    );
    expect(managementApi.fetchMock).not.toHaveBeenCalled();
  });

  it('rejects expired release tokens without mutating pending_release state', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-expired',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-issue-expired',
    });

    vi.setSystemTime(new Date('2026-04-08T06:15:01Z'));

    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-expired',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      hardware_hash: release.hardwareHash,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:15:00.000Z',
    });

    const response = await postIssue(env, requestBody);
    expect(response.status).toBe(410);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'challenge_expired',
        class: 'retryable',
        subcode: 'release_token_expired',
        retryAfterMs: 0,
        message: 'release_token has expired and must be reissued',
        managedState: {
          lifecycle: 'pending_release',
          managed_availability: true,
        },
        currentEntitlement: {
          provider: 'OpenRouter',
          budget_usd: 0.07,
          issued_at: null,
          expires_at: null,
        },
      }),
    );

    const entitlement = env.__db
      .prepare(
        `SELECT status, managed_credential_ref, issued_at, expires_at,
                release_token_hash, release_token_expires_at
           FROM openrouter_entitlements
          WHERE installation_id = ?`,
      )
      .get('install-issue-expired') as Record<string, unknown>;

    expect(entitlement).toEqual({
      status: 'pending_release',
      managed_credential_ref: null,
      issued_at: null,
      expires_at: null,
      release_token_hash: expect.any(String),
      release_token_expires_at: release.releaseTokenExpiresAt,
    });
  });

  it('rejects non-object JSON bodies with invalid_request', async () => {
    const env = createTestBrokerEnv();
    const response = await postIssue(env, 'null');

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'invalid_request',
        class: 'terminal',
        message: 'request body must be a JSON object',
      }),
    );
  });
});
