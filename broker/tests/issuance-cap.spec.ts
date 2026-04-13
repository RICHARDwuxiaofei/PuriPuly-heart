import { afterEach, describe, expect, it, vi } from 'vitest';

import app from '../src/index';
import {
  createDeviceKeyPair,
  signCanonicalIssueRequest,
  signCanonicalStatusRequest,
  signCanonicalVerifyRequest,
} from './test-support/ed25519';
import {
  activatePendingReleaseSession,
  createPendingReleaseSession,
} from './test-support/openrouter-issue';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { getTrialStatus, issueChallenge, postIssue, postVerify } from './test-support/trial-api';
import { updateAbuseControls } from './test-support/abuse-controls';

describe('broker daily issuance cap enforcement', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('returns issuance_suspended from challenge once the daily new-active cap is reached while active status remains available', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    updateAbuseControls(env, (controls) => {
      controls.newActiveEntitlementsPerDay.maxCount = 1;
    });

    const active = await activatePendingReleaseSession({
      env,
      installationId: 'install-cap-active',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-cap-active',
    });
    expect(active.response.status).toBe(200);

    const blockedKeyPair = await createDeviceKeyPair();
    const blockedResponse = await app.request(
      'http://broker.test/v1/trial/challenge',
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'cf-connecting-ip': '203.0.113.30',
        },
        body: JSON.stringify({
          installation_id: 'install-cap-blocked-challenge',
          device_public_key: blockedKeyPair.devicePublicKey,
          app_version: '1.2.3',
        }),
      },
      env,
    );

    expect(blockedResponse.status).toBe(503);
    await expect(blockedResponse.json()).resolves.toEqual({
      error: {
        code: 'issuance_suspended',
        class: 'retryable',
        subcode: 'global_cap_reached',
        retry_after_ms: 64800000,
        message: 'new entitlement issuance is temporarily suspended',
      },
      managed_state: {
        lifecycle: 'none',
        managed_availability: true,
      },
      current_entitlement: null,
    });

    const signedStatus = await signCanonicalStatusRequest(active.keyPair.privateKey, {
      installation_id: 'install-cap-active',
      timestamp: '2026-04-08T06:00:30.000Z',
    });
    const statusResponse = await getTrialStatus({
      env,
      installationId: 'install-cap-active',
      headers: {
        'X-Puripuly-Timestamp': signedStatus.timestamp,
        'X-Puripuly-Signature': signedStatus.signature,
      },
    });

    expect(statusResponse.status).toBe(200);
    await expect(statusResponse.json()).resolves.toEqual({
      managed_state: {
        lifecycle: 'active',
        managed_availability: true,
      },
      current_entitlement: {
        provider: 'OpenRouter',
        budget_usd: 0.08,
        issued_at: '2026-04-08T06:00:00.000Z',
        expires_at: '2026-10-08T06:00:00.000Z',
      },
      onboarding_eligibility: {
        eligible: false,
        reason: 'active',
      },
    });
  });

  it('rechecks the cap at verify time but still allows issue for already-verified pending_release installations', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    updateAbuseControls(env, (controls) => {
      controls.newActiveEntitlementsPerDay.maxCount = 1;
    });

    const waitingKeyPair = await createDeviceKeyPair();
    const waitingChallenge = await issueChallenge({
      env,
      installationId: 'install-cap-waiting',
      devicePublicKey: waitingKeyPair.devicePublicKey,
      appVersion: '1.2.3',
    });

    const pendingRelease = await createPendingReleaseSession({
      env,
      installationId: 'install-cap-pending',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-cap-pending',
    });
    const active = await activatePendingReleaseSession({
      env,
      installationId: 'install-cap-active-second',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-cap-active-second',
    });
    expect(active.response.status).toBe(200);

    const waitingVerify = await signCanonicalVerifyRequest(waitingKeyPair.privateKey, {
      installation_id: 'install-cap-waiting',
      device_public_key: waitingKeyPair.devicePublicKey,
      challenge: waitingChallenge.challenge,
      challenge_expires_at: waitingChallenge.challenge_expires_at,
      hardware_hash: 'hardware-hash-cap-waiting',
      app_version: '1.2.3',
      signed_at: '2026-04-08T06:00:30.000Z',
    });
    const blockedVerifyResponse = await postVerify(env, waitingVerify);

    expect(blockedVerifyResponse.status).toBe(503);
    await expect(blockedVerifyResponse.json()).resolves.toEqual({
      error: {
        code: 'issuance_suspended',
        class: 'retryable',
        subcode: 'global_cap_reached',
        retry_after_ms: 64800000,
        message: 'new entitlement issuance is temporarily suspended',
      },
      managed_state: {
        lifecycle: 'none',
        managed_availability: true,
      },
      current_entitlement: null,
    });

    const persistedWaitingInstallation = env.__db
      .prepare(
        `SELECT challenge, challenge_expires_at, hardware_hash
           FROM installations
          WHERE installation_id = ?`,
      )
      .get('install-cap-waiting') as Record<string, unknown>;
    expect(persistedWaitingInstallation).toEqual({
      challenge: waitingChallenge.challenge,
      challenge_expires_at: waitingChallenge.challenge_expires_at,
      hardware_hash: null,
    });

    const pendingIssueRequest = await signCanonicalIssueRequest(
      pendingRelease.keyPair.privateKey,
      {
        installation_id: 'install-cap-pending',
        device_public_key: pendingRelease.keyPair.devicePublicKey,
        release_token: pendingRelease.releaseToken,
        hardware_hash: pendingRelease.hardwareHash,
        reason: 'llm_start',
        budget_usd: 0.08,
        model: 'google/gemma-4-26b-a4b-it',
        signed_at: '2026-04-08T06:00:45.000Z',
      },
    );
    const pendingIssueResponse = await postIssue(env, pendingIssueRequest);

    expect(pendingIssueResponse.status).toBe(200);
    await expect(pendingIssueResponse.json()).resolves.toEqual(
      expect.objectContaining({
        managed_state: {
          lifecycle: 'active',
          managed_availability: true,
        },
        expires_at: '2026-10-08T06:00:00.000Z',
        budget_usd: 0.08,
      }),
    );
  });

  it('still counts same-day issuances after the entitlement is later revoked', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    updateAbuseControls(env, (controls) => {
      controls.newActiveEntitlementsPerDay.maxCount = 1;
    });

    const active = await activatePendingReleaseSession({
      env,
      installationId: 'install-cap-revoked-source',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-cap-revoked-source',
    });
    expect(active.response.status).toBe(200);

    env.__db
      .prepare(
        `UPDATE openrouter_entitlements
            SET status = 'revoked',
                release_session_ref = NULL,
                release_token_hash = NULL,
                release_token_expires_at = NULL
          WHERE installation_id = ?`,
      )
      .run('install-cap-revoked-source');

    const blockedKeyPair = await createDeviceKeyPair();
    const blockedResponse = await app.request(
      'http://broker.test/v1/trial/challenge',
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'cf-connecting-ip': '203.0.113.32',
        },
        body: JSON.stringify({
          installation_id: 'install-cap-revoked-target',
          device_public_key: blockedKeyPair.devicePublicKey,
          app_version: '1.2.3',
        }),
      },
      env,
    );

    expect(blockedResponse.status).toBe(503);
    await expect(blockedResponse.json()).resolves.toEqual({
      error: {
        code: 'issuance_suspended',
        class: 'retryable',
        subcode: 'global_cap_reached',
        retry_after_ms: 64800000,
        message: 'new entitlement issuance is temporarily suspended',
      },
      managed_state: {
        lifecycle: 'none',
        managed_availability: true,
      },
      current_entitlement: null,
    });
  });

  it('resets the cap at the next UTC day boundary instead of using a rolling 24-hour window', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T23:55:00Z'));

    const env = createTestBrokerEnv();
    updateAbuseControls(env, (controls) => {
      controls.newActiveEntitlementsPerDay.maxCount = 1;
    });

    const active = await activatePendingReleaseSession({
      env,
      installationId: 'install-cap-utc-reset-source',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-cap-utc-reset-source',
      verifySignedAt: '2026-04-08T23:55:30.000Z',
      issueSignedAt: '2026-04-08T23:55:45.000Z',
    });
    expect(active.response.status).toBe(200);

    vi.setSystemTime(new Date('2026-04-09T00:05:00Z'));

    const nextDayKeyPair = await createDeviceKeyPair();
    const nextDayResponse = await app.request(
      'http://broker.test/v1/trial/challenge',
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'cf-connecting-ip': '203.0.113.33',
        },
        body: JSON.stringify({
          installation_id: 'install-cap-utc-reset-target',
          device_public_key: nextDayKeyPair.devicePublicKey,
          app_version: '1.2.3',
        }),
      },
      env,
    );

    expect(nextDayResponse.status).toBe(200);
    await expect(nextDayResponse.json()).resolves.toEqual(
      expect.objectContaining({
        challenge: expect.any(String),
        managed_state: {
          lifecycle: 'none',
          managed_availability: true,
        },
        current_entitlement: null,
      }),
    );
  });
});
