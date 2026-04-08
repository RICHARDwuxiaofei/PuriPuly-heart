import { afterEach, describe, expect, it, vi } from 'vitest';

import app from '../src/index';
import { createDeviceKeyPair } from './test-support/ed25519';
import {
  createTestBrokerEnv,
} from './test-support/sqlite-d1';
import { replaceAbuseControlsValue, updateAbuseControls } from './test-support/abuse-controls';

describe('broker abuse-controls runtime config validation', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('falls back to default abuse controls when the stored config shape is malformed', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    replaceAbuseControlsValue(env, {
      trialChallenge: {
        endpoint: 'POST /v1/trial/challenge',
        scope: 'ip',
        maxRequests: 'bad-type',
        windowMinutes: 15,
      },
    });

    for (const suffix of Array.from({ length: 10 }, (_, index) => `${index + 1}`)) {
      const keyPair = await createDeviceKeyPair();
      const response = await app.request(
        'http://broker.test/v1/trial/challenge',
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'cf-connecting-ip': '203.0.113.71',
          },
          body: JSON.stringify({
            installation_id: `install-malformed-config-${suffix}`,
            device_public_key: keyPair.devicePublicKey,
            app_version: '1.2.3',
          }),
        },
        env,
      );

      expect(response.status).toBe(200);
    }

    const blockedKeyPair = await createDeviceKeyPair();
    const blockedResponse = await app.request(
      'http://broker.test/v1/trial/challenge',
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'cf-connecting-ip': '203.0.113.71',
        },
        body: JSON.stringify({
          installation_id: 'install-malformed-config-11',
          device_public_key: blockedKeyPair.devicePublicKey,
          app_version: '1.2.3',
        }),
      },
      env,
    );

    expect(blockedResponse.status).toBe(429);
    await expect(blockedResponse.json()).resolves.toEqual({
      error: {
        code: 'rate_limited',
        class: 'retryable',
        subcode: 'ip_rate_limited',
        retry_after_ms: 900000,
        message: 'request rate limit exceeded for POST /v1/trial/challenge',
      },
      managed_state: {
        lifecycle: 'none',
        managed_availability: true,
      },
      current_entitlement: null,
    });
  });

  it('uses runtime overrides only when the full fixed abuse-control layout is valid', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    updateAbuseControls(env, (controls) => {
      controls.trialChallenge.maxRequests = 1;
    });

    const firstKeyPair = await createDeviceKeyPair();
    const firstResponse = await app.request(
      'http://broker.test/v1/trial/challenge',
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'cf-connecting-ip': '203.0.113.72',
        },
        body: JSON.stringify({
          installation_id: 'install-valid-runtime-config-1',
          device_public_key: firstKeyPair.devicePublicKey,
          app_version: '1.2.3',
        }),
      },
      env,
    );
    expect(firstResponse.status).toBe(200);

    const secondKeyPair = await createDeviceKeyPair();
    const blockedResponse = await app.request(
      'http://broker.test/v1/trial/challenge',
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'cf-connecting-ip': '203.0.113.72',
        },
        body: JSON.stringify({
          installation_id: 'install-valid-runtime-config-2',
          device_public_key: secondKeyPair.devicePublicKey,
          app_version: '1.2.3',
        }),
      },
      env,
    );

    expect(blockedResponse.status).toBe(429);
  });
});
