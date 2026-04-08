import { afterEach, describe, expect, it, vi } from 'vitest';

import { createDeviceKeyPair, signCanonicalStatusRequest } from './test-support/ed25519';
import { createTestBrokerEnv, insertEntitlement } from './test-support/sqlite-d1';
import { getTrialStatus, issueChallenge } from './test-support/trial-api';

interface LifecycleCase {
  lifecycle: 'expired' | 'revoked';
  budgetUsd: number;
  expiresAt: string;
}

describe('GET /v1/trial/status lifecycle data', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    {
      lifecycle: 'expired',
      budgetUsd: 0.03,
      expiresAt: '2026-04-02T00:00:00Z',
    },
    {
      lifecycle: 'revoked',
      budgetUsd: 0.02,
      expiresAt: '2026-04-03T00:00:00Z',
    },
  ] satisfies LifecycleCase[])('returns $lifecycle as lifecycle data instead of a public error', async (testCase: LifecycleCase) => {
    const { lifecycle, budgetUsd, expiresAt } = testCase;
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    await issueChallenge({
      env,
      installationId: `install-status-${lifecycle}`,
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });
    insertEntitlement(env, {
      installation_id: `install-status-${lifecycle}`,
      status: lifecycle,
      budget_usd: budgetUsd,
      issued_at: '2026-04-01T00:00:00Z',
      expires_at: expiresAt,
    });
    const signedRequest = await signCanonicalStatusRequest(keyPair.privateKey, {
      installation_id: `install-status-${lifecycle}`,
      timestamp: '2026-04-08T06:00:30.000Z',
    });

    const response = await getTrialStatus({
      env,
      installationId: `install-status-${lifecycle}`,
      headers: {
        'X-Puripuly-Timestamp': signedRequest.timestamp,
        'X-Puripuly-Signature': signedRequest.signature,
      },
    });

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      managed_state: {
        lifecycle,
        managed_availability: false,
      },
      current_entitlement: {
        provider: 'OpenRouter',
        budget_usd: budgetUsd,
        issued_at: '2026-04-01T00:00:00Z',
        expires_at: expiresAt,
      },
      onboarding_eligibility: {
        eligible: false,
        reason: lifecycle,
      },
    });
  });
});
