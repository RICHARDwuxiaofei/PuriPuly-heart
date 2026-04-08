import { afterEach, describe, expect, it, vi } from 'vitest';

import { signCanonicalIssueRequest } from './test-support/ed25519';
import { createPendingReleaseSession } from './test-support/openrouter-issue';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { postIssue } from './test-support/trial-api';

describe('broker public error envelope', () => {
  afterEach(() => {
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
});
