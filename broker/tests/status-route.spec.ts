import { describe, expect, it } from 'vitest';

import { createDeviceKeyPair, signCanonicalStatusRequest } from './test-support/ed25519';
import { normalizedErrorEnvelope } from './test-support/errors';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { getTrialStatus } from './test-support/trial-api';

describe('GET /v1/trial/status route contract', () => {
  it('requires the installation_id query parameter', async () => {
    const env = createTestBrokerEnv();

    const response = await getTrialStatus({ env });

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'invalid_request',
        class: 'terminal',
        message: 'installation_id query parameter is required',
      }),
    );
  });

  it('rejects oversized installation_id query values', async () => {
    const env = createTestBrokerEnv();

    const response = await getTrialStatus({
      env,
      installationId: 'i'.repeat(129),
      headers: {
        'X-Puripuly-Timestamp': '2026-04-08T06:00:00.000Z',
        'X-Puripuly-Signature': 'placeholder',
      },
    });

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'invalid_request',
        class: 'terminal',
        message: 'installation_id must be between 1 and 128 characters',
      }),
    );
  });

  it('rejects non-ISO X-Puripuly-Timestamp header values', async () => {
    const env = createTestBrokerEnv();

    const response = await getTrialStatus({
      env,
      installationId: 'install-status-route',
      headers: {
        'X-Puripuly-Timestamp': 'Wed, 08 Apr 2026 06:00:00 GMT',
        'X-Puripuly-Signature': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      },
    });

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'invalid_request',
        class: 'terminal',
        message: 'X-Puripuly-Timestamp must be a valid ISO-8601 timestamp',
      }),
    );
  });

  it('returns installation_not_found for an unknown installation_id after validating the signed request format', async () => {
    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    const signedRequest = await signCanonicalStatusRequest(keyPair.privateKey, {
      installation_id: 'unknown-installation',
      timestamp: '2026-04-08T06:00:00.000Z',
    });

    const response = await getTrialStatus({
      env,
      installationId: 'unknown-installation',
      headers: {
        'X-Puripuly-Timestamp': signedRequest.timestamp,
        'X-Puripuly-Signature': signedRequest.signature,
      },
    });

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'installation_not_found',
        message: 'installation_id is not registered with the broker',
      }),
    );
  });
});
