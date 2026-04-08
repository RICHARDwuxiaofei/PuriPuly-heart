import { afterEach, describe, expect, it, vi } from 'vitest';

import { signCanonicalIssueRequest } from './test-support/ed25519';
import { createPendingReleaseSession } from './test-support/openrouter-issue';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { postIssue } from './test-support/trial-api';

interface PolicyViolationCase {
  name: string;
  overrides: Partial<{
    reason: string;
    budget_usd: number;
    model: string;
  }>;
  message: string;
}

describe('POST /v1/providers/openrouter/issue policy enforcement', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    {
      name: 'reason',
      overrides: {
        reason: 'prewarm',
      },
      message: 'reason must be llm_start',
    },
    {
      name: 'budget_usd',
      overrides: {
        budget_usd: 0.06,
      },
      message: 'budget_usd must equal 0.07',
    },
    {
      name: 'model',
      overrides: {
        model: 'openai/gpt-4.1-mini',
      },
      message: 'model must equal google/gemma-4-26b-a4b-it',
    },
  ] satisfies PolicyViolationCase[])(
    'rejects invalid $name values without consuming the pending_release entitlement',
    async (testCase: PolicyViolationCase) => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

      const env = createTestBrokerEnv();
      const release = await createPendingReleaseSession({
        env,
        installationId: `install-issue-policy-${testCase.name}`,
        appVersion: '1.2.3',
        hardwareHash: `hardware-hash-issue-policy-${testCase.name}`,
      });
      const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
        installation_id: `install-issue-policy-${testCase.name}`,
        device_public_key: release.keyPair.devicePublicKey,
        release_token: release.releaseToken,
        reason: 'llm_start',
        budget_usd: 0.07,
        model: 'google/gemma-4-26b-a4b-it',
        signed_at: '2026-04-08T06:00:45.000Z',
        ...testCase.overrides,
      });

      const response = await postIssue(env, requestBody);

      expect(response.status).toBe(400);
      await expect(response.json()).resolves.toEqual({
        error: {
          code: 'invalid_request',
          message: testCase.message,
        },
      });

      const entitlement = env.__db
        .prepare(
          `SELECT status, managed_credential_ref, issued_at, expires_at,
                  release_token_hash, release_token_expires_at
             FROM openrouter_entitlements
            WHERE installation_id = ?`,
        )
        .get(`install-issue-policy-${testCase.name}`) as Record<string, unknown>;

      expect(entitlement).toEqual({
        status: 'pending_release',
        managed_credential_ref: null,
        issued_at: null,
        expires_at: null,
        release_token_hash: expect.any(String),
        release_token_expires_at: release.releaseTokenExpiresAt,
      });
    },
  );
});
