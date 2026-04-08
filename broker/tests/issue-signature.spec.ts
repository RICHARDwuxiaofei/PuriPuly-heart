import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  signCanonicalIssueRequest,
  signNonCanonicalIssueRequest,
} from './test-support/ed25519';
import { createPendingReleaseSession } from './test-support/openrouter-issue';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { postIssue } from './test-support/trial-api';

describe('POST /v1/providers/openrouter/issue signing contract', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('accepts a canonical Ed25519-signed request', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-signed',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-issue-signed',
    });
    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-signed',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:00:45.000Z',
    });

    const response = await postIssue(env, requestBody);

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual(
      expect.objectContaining({
        openrouter_api_key: 'test-managed-api-key',
        managed_credential_ref: expect.any(String),
        expires_at: '2026-10-08T06:00:00.000Z',
        budget_usd: 0.07,
        model: 'google/gemma-4-26b-a4b-it',
        managed_state: {
          lifecycle: 'active',
          managed_availability: true,
        },
      }),
    );
  });

  it('rejects signatures that do not use the canonical newline-delimited payload order', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-wrong-order',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-issue-wrong-order',
    });
    const requestBody = await signNonCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-wrong-order',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:00:45.000Z',
    });

    const response = await postIssue(env, requestBody);

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      error: {
        code: 'signature_invalid',
        message: 'signature verification failed for the registered device_public_key',
      },
    });
  });

  it('rejects signatures outside the ±60 second skew window', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-skew',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-issue-skew',
    });
    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-skew',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:01:01.000Z',
    });

    const response = await postIssue(env, requestBody);

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      error: {
        code: 'signature_skew',
        message: 'signed_at must be within ±60 seconds of broker time',
      },
    });
  });
});
