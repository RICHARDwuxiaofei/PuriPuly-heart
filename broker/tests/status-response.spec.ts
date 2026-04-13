import { afterEach, describe, expect, it, vi } from 'vitest';

import { createDeviceKeyPair, signCanonicalStatusRequest } from './test-support/ed25519';
import { createTestBrokerEnv, insertEntitlement } from './test-support/sqlite-d1';
import { getTrialStatus, issueChallenge } from './test-support/trial-api';

describe('GET /v1/trial/status response contract', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns normalized active managed state with broker-side eligibility metadata', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    await issueChallenge({
      env,
      installationId: 'install-status-active',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });
    insertEntitlement(env, {
      installation_id: 'install-status-active',
      status: 'active',
      budget_usd: 0.04,
      managed_credential_ref: 'internal-active-ref',
      issued_at: '2026-04-01T00:00:00Z',
      expires_at: '2026-10-01T00:00:00Z',
    });
    const signedRequest = await signCanonicalStatusRequest(keyPair.privateKey, {
      installation_id: 'install-status-active',
      timestamp: '2026-04-08T06:00:30.000Z',
    });

    const response = await getTrialStatus({
      env,
      installationId: 'install-status-active',
      headers: {
        'X-Puripuly-Timestamp': signedRequest.timestamp,
        'X-Puripuly-Signature': signedRequest.signature,
      },
    });

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      managed_state: {
        lifecycle: 'active',
        managed_availability: true,
      },
      current_entitlement: {
        provider: 'OpenRouter',
        budget_usd: 0.04,
        issued_at: '2026-04-01T00:00:00Z',
        expires_at: '2026-10-01T00:00:00Z',
      },
      onboarding_eligibility: {
        eligible: false,
        reason: 'active',
      },
    });
  });

  it('keeps status responses free of challenge, release-session, and credential storage fields', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    await issueChallenge({
      env,
      installationId: 'install-status-hidden-fields',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });
    insertEntitlement(env, {
      installation_id: 'install-status-hidden-fields',
      status: 'pending_release',
      budget_usd: 0.08,
      managed_credential_ref: 'internal-pending-ref',
      release_session_ref: 'release-session',
      release_token_hash: 'release-token-hash',
      release_token_expires_at: '2026-04-08T06:15:00Z',
    });
    const signedRequest = await signCanonicalStatusRequest(keyPair.privateKey, {
      installation_id: 'install-status-hidden-fields',
      timestamp: '2026-04-08T06:00:30.000Z',
    });

    const response = await getTrialStatus({
      env,
      installationId: 'install-status-hidden-fields',
      headers: {
        'X-Puripuly-Timestamp': signedRequest.timestamp,
        'X-Puripuly-Signature': signedRequest.signature,
      },
    });
    expect(response.status).toBe(200);

    const payload = (await response.json()) as Record<string, unknown>;
    expect(payload).not.toHaveProperty('challenge');
    expect(payload).not.toHaveProperty('challenge_expires_at');
    expect(payload).not.toHaveProperty('fingerprint_salt');
    expect(payload).not.toHaveProperty('release_token');
    expect(payload).not.toHaveProperty('release_session_ref');
    expect(payload).not.toHaveProperty('release_token_hash');
    expect(payload).not.toHaveProperty('managed_credential_ref');
  });

  it('treats pending_release as broker-side onboarding continuation rather than a terminal state', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    await issueChallenge({
      env,
      installationId: 'install-status-pending-release',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });
    insertEntitlement(env, {
      installation_id: 'install-status-pending-release',
      status: 'pending_release',
      budget_usd: 0.08,
    });
    const signedRequest = await signCanonicalStatusRequest(keyPair.privateKey, {
      installation_id: 'install-status-pending-release',
      timestamp: '2026-04-08T06:00:30.000Z',
    });

    const response = await getTrialStatus({
      env,
      installationId: 'install-status-pending-release',
      headers: {
        'X-Puripuly-Timestamp': signedRequest.timestamp,
        'X-Puripuly-Signature': signedRequest.signature,
      },
    });

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      managed_state: {
        lifecycle: 'pending_release',
        managed_availability: true,
      },
      onboarding_eligibility: {
        eligible: true,
        reason: 'pending_release',
      },
    });
  });
});
