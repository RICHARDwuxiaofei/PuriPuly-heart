import { afterEach, describe, expect, it, vi } from 'vitest';

import { createDeviceKeyPair, signCanonicalVerifyRequest } from './test-support/ed25519';
import { activatePendingReleaseSession } from './test-support/openrouter-issue';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { issueChallenge, postVerify } from './test-support/trial-api';

describe('broker duplicate hardware suppression', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns trial_not_eligible when verify sees a hardware hash already bound to an active entitlement on a different installation', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const duplicateHardwareHash = 'hardware-hash-duplicate-active';

    const active = await activatePendingReleaseSession({
      env,
      installationId: 'install-duplicate-source',
      appVersion: '1.2.3',
      hardwareHash: duplicateHardwareHash,
    });
    expect(active.response.status).toBe(200);

    const duplicateKeyPair = await createDeviceKeyPair();
    const duplicateChallenge = await issueChallenge({
      env,
      installationId: 'install-duplicate-target',
      devicePublicKey: duplicateKeyPair.devicePublicKey,
      appVersion: '1.2.3',
    });
    const duplicateVerify = await signCanonicalVerifyRequest(duplicateKeyPair.privateKey, {
      installation_id: 'install-duplicate-target',
      device_public_key: duplicateKeyPair.devicePublicKey,
      challenge: duplicateChallenge.challenge,
      challenge_expires_at: duplicateChallenge.challenge_expires_at,
      hardware_hash: duplicateHardwareHash,
      app_version: '1.2.3',
      signed_at: '2026-04-08T06:00:30.000Z',
    });

    const response = await postVerify(env, duplicateVerify);

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual({
      error: {
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'hardware_duplicate',
        retry_after_ms: null,
        message: 'hardware_hash is already associated with an active entitlement',
      },
      managed_state: {
        lifecycle: 'none',
        managed_availability: true,
      },
      current_entitlement: null,
    });

    const duplicateInstallation = env.__db
      .prepare(
        `SELECT challenge, challenge_expires_at, hardware_hash
           FROM installations
          WHERE installation_id = ?`,
      )
      .get('install-duplicate-target') as Record<string, unknown>;
    expect(duplicateInstallation).toEqual({
      challenge: duplicateChallenge.challenge,
      challenge_expires_at: duplicateChallenge.challenge_expires_at,
      hardware_hash: null,
    });

    const duplicateEntitlementCount = env.__db
      .prepare(
        'SELECT COUNT(*) AS count FROM openrouter_entitlements WHERE installation_id = ?',
      )
      .get('install-duplicate-target') as { count: number };
    expect(duplicateEntitlementCount.count).toBe(0);
  });
});
