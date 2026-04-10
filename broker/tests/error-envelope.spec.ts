import { afterEach, describe, expect, it, vi } from 'vitest';

import { signCanonicalIssueRequest } from './test-support/ed25519';
import {
  activatePendingReleaseSession,
  createPendingReleaseSession,
  mockOpenRouterManagementApi,
} from './test-support/openrouter-issue';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { postIssue } from './test-support/trial-api';
import { normalizedErrorEnvelope } from './test-support/errors';

describe('broker public error envelope', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('normalizes release-token expiry into challenge_expired with bounded code/class/subcode fields', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-error-envelope-expired-release',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-error-envelope-expired-release',
    });

    vi.setSystemTime(new Date('2026-04-08T06:15:01Z'));

    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-error-envelope-expired-release',
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
    await expect(response.json()).resolves.toEqual({
      error: {
        code: 'challenge_expired',
        class: 'retryable',
        subcode: 'release_token_expired',
        retry_after_ms: 0,
        message: 'release_token has expired and must be reissued',
      },
      managed_state: {
        lifecycle: 'pending_release',
        managed_availability: true,
      },
      current_entitlement: {
        provider: 'OpenRouter',
        budget_usd: 0.07,
        issued_at: null,
        expires_at: null,
      },
    });
  });

  it('normalizes active reissue into trial_not_eligible with managed_key_unrecoverable', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const active = await activatePendingReleaseSession({
      env,
      installationId: 'install-error-envelope-active-reissue',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-error-envelope-active-reissue',
    });

    const response = await postIssue(
      env,
      await signCanonicalIssueRequest(active.keyPair.privateKey, {
        installation_id: 'install-error-envelope-active-reissue',
        device_public_key: active.keyPair.devicePublicKey,
        release_token: active.releaseToken,
        hardware_hash: active.hardwareHash,
        reason: 'llm_start',
        budget_usd: 0.07,
        model: 'google/gemma-4-26b-a4b-it',
        signed_at: '2026-04-08T06:00:45.000Z',
      }),
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'managed_key_unrecoverable',
        message: 'managed key was already issued and cannot be recovered',
        managedState: {
          lifecycle: 'active',
          managed_availability: true,
        },
        currentEntitlement: {
          provider: 'OpenRouter',
          budget_usd: 0.07,
          issued_at: '2026-04-08T06:00:00.000Z',
          expires_at: '2026-10-08T06:00:00.000Z',
        },
      }),
    );
  });

  it('returns internal_error and leaves a structured orphan-audit trail when activation cleanup fails', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const env = createTestBrokerEnv({
      beforeRun({ sql }) {
        if (sql.includes('SET status = ?')) {
          throw new Error('simulated activation failure');
        }
      },
    });
    const managementApi = mockOpenRouterManagementApi({ mode: 'cleanup_failure' });
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-error-envelope-cleanup-failure',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-error-envelope-cleanup-failure',
    });
    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-error-envelope-cleanup-failure',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      hardware_hash: release.hardwareHash,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:00:45.000Z',
    });

    const response = await postIssue(env, requestBody);

    expect(response.status).toBe(500);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'internal_error',
        class: 'retryable',
        message: 'broker encountered an unexpected internal error',
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
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      'managed_child_key_orphan_audit',
      expect.objectContaining({
        installation_id: 'install-error-envelope-cleanup-failure',
        release_session_ref: expect.any(String),
        managed_credential_ref: managementApi.childKey.hash,
      }),
    );
  });

  it('fails closed and logs orphan-audit detail when child-key creation is ambiguous', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const env = createTestBrokerEnv();
    const managementApi = mockOpenRouterManagementApi({ mode: 'malformed_create' });
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-error-envelope-ambiguous-create',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-error-envelope-ambiguous-create',
    });
    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-error-envelope-ambiguous-create',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      hardware_hash: release.hardwareHash,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:00:45.000Z',
    });

    const firstResponse = await postIssue(env, requestBody);
    const secondResponse = await postIssue(env, requestBody);

    expect(firstResponse.status).toBe(500);
    expect(secondResponse.status).toBe(401);
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      'managed_child_key_orphan_audit',
      expect.objectContaining({
        installation_id: 'install-error-envelope-ambiguous-create',
        release_session_ref: expect.any(String),
        managed_credential_ref: null,
        creation_failure: expect.objectContaining({
          operation: 'create_key',
          code: 'malformed_upstream',
        }),
      }),
    );
    expect(
      managementApi.fetchMock.mock.calls.filter(
        ([url]) => String(url).includes('/keys/') && String(url) !== 'https://openrouter.ai/api/v1/keys',
      ),
    ).toHaveLength(0);

    const entitlement = env.__db
      .prepare(
        `SELECT status, managed_credential_ref, release_session_ref, release_token_hash, release_token_expires_at
           FROM openrouter_entitlements
          WHERE installation_id = ?`,
      )
      .get('install-error-envelope-ambiguous-create') as Record<string, unknown>;

    expect(entitlement).toEqual({
      status: 'pending_release',
      managed_credential_ref: null,
      release_session_ref: null,
      release_token_hash: null,
      release_token_expires_at: null,
    });
  });
});
