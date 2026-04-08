import { afterEach, describe, expect, it, vi } from 'vitest';

import { signCanonicalIssueRequest } from './test-support/ed25519';
import { createPendingReleaseSession } from './test-support/openrouter-issue';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { postIssue } from './test-support/trial-api';

describe('POST /v1/providers/openrouter/issue response payload', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns the managed OpenRouter key and a distinct managed_credential_ref without leaking release-session fields', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-response',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-issue-response',
    });
    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-response',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:00:45.000Z',
    });

    const response = await postIssue(env, requestBody);
    expect(response.status).toBe(200);

    const payload = (await response.json()) as Record<string, unknown>;
    expect(payload).toEqual({
      openrouter_api_key: 'test-managed-api-key',
      managed_credential_ref: expect.any(String),
      managed_state: {
        lifecycle: 'active',
        managed_availability: true,
      },
      expires_at: '2026-10-08T06:00:00.000Z',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
    });
    expect(payload.openrouter_api_key).not.toBe(payload.managed_credential_ref);
    expect(payload).not.toHaveProperty('release_token');
    expect(payload).not.toHaveProperty('release_session_ref');
    expect(payload).not.toHaveProperty('release_token_hash');
    expect(payload).not.toHaveProperty('release_token_expires_at');
  });
});
