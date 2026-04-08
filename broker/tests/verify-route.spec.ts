import { afterEach, describe, expect, it, vi } from 'vitest';

import { createDeviceKeyPair, signCanonicalVerifyRequest } from './test-support/ed25519';
import { normalizedErrorEnvelope } from './test-support/errors';
import { createTestBrokerEnv, insertEntitlement } from './test-support/sqlite-d1';
import { issueChallenge, postVerify } from './test-support/trial-api';

const OVERSIZED_INSTALLATION_ID = 'i'.repeat(129);
const OVERSIZED_APP_VERSION = 'v'.repeat(65);
const OVERSIZED_HARDWARE_HASH = 'h'.repeat(129);

interface VerifyBoundsCase {
  name: 'installation_id' | 'app_version' | 'hardware_hash';
  overrides: Partial<{
    installation_id: string;
    app_version: string;
    hardware_hash: string;
  }>;
  message: string;
}

function createDeferred(): {
  promise: Promise<void>;
  resolve: () => void;
} {
  let resolve!: () => void;
  return {
    promise: new Promise<void>((resolvePromise) => {
      resolve = resolvePromise;
    }),
    resolve,
  };
}

describe('POST /v1/trial/challenge/verify route contract', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('consumes the active challenge only after successful verify and persists hardware_hash with the challenge salt version', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    const challenge = await issueChallenge({
      env,
      installationId: 'install-verify-route',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });

    const requestBody = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: 'install-verify-route',
      device_public_key: keyPair.devicePublicKey,
      challenge: challenge.challenge,
      challenge_expires_at: challenge.challenge_expires_at,
      hardware_hash: 'hardware-hash-route-001',
      app_version: '2.0.0',
      signed_at: '2026-04-08T06:00:30.000Z',
    });

    const response = await postVerify(env, requestBody);
    expect(response.status).toBe(200);

    const installation = env.__db
      .prepare(
        `SELECT hardware_hash, hardware_hash_salt_version, app_version, challenge,
                challenge_expires_at, challenge_salt_version
           FROM installations
          WHERE installation_id = ?`,
      )
      .get('install-verify-route') as Record<string, unknown>;

    expect(installation).toEqual({
      hardware_hash: 'hardware-hash-route-001',
      hardware_hash_salt_version: 7,
      app_version: '2.0.0',
      challenge: null,
      challenge_expires_at: null,
      challenge_salt_version: null,
    });

    const replayResponse = await postVerify(env, requestBody);
    expect(replayResponse.status).toBe(404);
    await expect(replayResponse.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'challenge_invalid',
        class: 'security_fail',
        subcode: 'challenge_not_found',
        message: 'no active challenge exists for installation_id',
      }),
    );
  });

  it('rejects expired challenges without consuming stored challenge state', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    const challenge = await issueChallenge({
      env,
      installationId: 'install-expired',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });

    vi.setSystemTime(new Date('2026-04-08T06:05:01Z'));

    const requestBody = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: 'install-expired',
      device_public_key: keyPair.devicePublicKey,
      challenge: challenge.challenge,
      challenge_expires_at: challenge.challenge_expires_at,
      hardware_hash: 'hardware-hash-route-002',
      app_version: '1.2.3',
      signed_at: '2026-04-08T06:05:00.000Z',
    });

    const response = await postVerify(env, requestBody);
    expect(response.status).toBe(410);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'challenge_expired',
        class: 'retryable',
        retryAfterMs: 0,
        message: 'challenge has expired and must be reissued',
      }),
    );

    const installation = env.__db
      .prepare(
        `SELECT hardware_hash, challenge, challenge_expires_at, challenge_salt_version
           FROM installations
          WHERE installation_id = ?`,
      )
      .get('install-expired') as Record<string, unknown>;

    expect(installation).toEqual({
      hardware_hash: null,
      challenge: challenge.challenge,
      challenge_expires_at: challenge.challenge_expires_at,
      challenge_salt_version: 7,
    });
  });

  it('keeps verify responses free of challenge preflight and release-session storage fields', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    const challenge = await issueChallenge({
      env,
      installationId: 'install-no-leaks',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });

    const requestBody = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: 'install-no-leaks',
      device_public_key: keyPair.devicePublicKey,
      challenge: challenge.challenge,
      challenge_expires_at: challenge.challenge_expires_at,
      hardware_hash: 'hardware-hash-route-003',
      app_version: '1.2.3',
      signed_at: '2026-04-08T06:00:20.000Z',
    });

    const response = await postVerify(env, requestBody);
    expect(response.status).toBe(200);

    const payload = (await response.json()) as Record<string, unknown>;
    expect(payload).not.toHaveProperty('challenge');
    expect(payload).not.toHaveProperty('challenge_expires_at');
    expect(payload).not.toHaveProperty('fingerprint_salt');
    expect(payload).not.toHaveProperty('release_session_ref');
    expect(payload).not.toHaveProperty('release_token_hash');
    expect(payload).not.toHaveProperty('managed_credential_ref');
  });

  it('rejects non-object JSON bodies with invalid_request instead of throwing', async () => {
    const env = createTestBrokerEnv();
    const response = await postVerify(env, 'null');

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'invalid_request',
        class: 'terminal',
        message: 'request body must be a JSON object',
      }),
    );
  });

  it.each([
    {
      name: 'installation_id',
      overrides: {
        installation_id: OVERSIZED_INSTALLATION_ID,
      },
      message: 'installation_id must be between 1 and 128 characters',
    },
    {
      name: 'app_version',
      overrides: {
        app_version: OVERSIZED_APP_VERSION,
      },
      message: 'app_version must be between 1 and 64 characters',
    },
    {
      name: 'hardware_hash',
      overrides: {
        hardware_hash: OVERSIZED_HARDWARE_HASH,
      },
      message: 'hardware_hash must be between 1 and 128 characters',
    },
  ] satisfies VerifyBoundsCase[])(
    'rejects oversized $name values before consuming challenge state',
    async (testCase: VerifyBoundsCase) => {
      const { overrides, message } = testCase;
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    const challenge = await issueChallenge({
      env,
      installationId: 'install-verify-bounds',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });

    const requestBody = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: 'install-verify-bounds',
      device_public_key: keyPair.devicePublicKey,
      challenge: challenge.challenge,
      challenge_expires_at: challenge.challenge_expires_at,
      hardware_hash: 'hardware-hash-verify-bounds',
      app_version: '1.2.3',
      signed_at: '2026-04-08T06:00:30.000Z',
      ...overrides,
    });

    const response = await postVerify(env, requestBody);

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'invalid_request',
        class: 'terminal',
        message,
      }),
    );

    const installation = env.__db
      .prepare(
        `SELECT challenge, challenge_expires_at, hardware_hash, app_version
           FROM installations
          WHERE installation_id = ?`,
      )
      .get('install-verify-bounds') as Record<string, unknown>;

    expect(installation).toEqual({
      challenge: challenge.challenge,
      challenge_expires_at: challenge.challenge_expires_at,
      hardware_hash: null,
      app_version: '1.2.3',
    });
    },
  );

  it('rejects non-ISO signed_at strings instead of accepting Date.parse-compatible timestamps', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    const challenge = await issueChallenge({
      env,
      installationId: 'install-non-iso-signed-at',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });

    const requestBody = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: 'install-non-iso-signed-at',
      device_public_key: keyPair.devicePublicKey,
      challenge: challenge.challenge,
      challenge_expires_at: challenge.challenge_expires_at,
      hardware_hash: 'hardware-hash-non-iso-signed-at',
      app_version: '1.2.3',
      signed_at: 'Wed, 08 Apr 2026 06:00:30 GMT',
    });

    const response = await postVerify(env, requestBody);

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'invalid_request',
        class: 'terminal',
        message: 'challenge_expires_at and signed_at must be valid ISO-8601 timestamps',
      }),
    );
  });

  it('rejects impossible ISO-looking signed_at strings instead of normalizing them', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    const env = createTestBrokerEnv();
    const keyPair = await createDeviceKeyPair();
    const challenge = await issueChallenge({
      env,
      installationId: 'install-impossible-iso-signed-at',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.2.3',
    });

    const requestBody = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: 'install-impossible-iso-signed-at',
      device_public_key: keyPair.devicePublicKey,
      challenge: challenge.challenge,
      challenge_expires_at: challenge.challenge_expires_at,
      hardware_hash: 'hardware-hash-impossible-iso-signed-at',
      app_version: '1.2.3',
      signed_at: '2026-04-31T06:00:30Z',
    });

    const response = await postVerify(env, requestBody);

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'invalid_request',
        class: 'terminal',
        message: 'challenge_expires_at and signed_at must be valid ISO-8601 timestamps',
      }),
    );
  });

  it('allows only one successful verify when two requests race to consume the same challenge', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    let armConsumePause = false;
    let pausedOnce = false;
    let installationReadCount = 0;
    const consumeStarted = createDeferred();
    const secondReadStarted = createDeferred();
    const releaseConsume = createDeferred();
    const env = createTestBrokerEnv({
      beforeFirst: async ({ sql }) => {
        if (
          armConsumePause &&
          sql.includes('FROM installations') &&
          sql.includes('WHERE installation_id = ?')
        ) {
          installationReadCount += 1;
          if (installationReadCount === 2) {
            secondReadStarted.resolve();
          }
        }
      },
      beforeRun: async ({ sql }) => {
        if (
          armConsumePause &&
          !pausedOnce &&
          sql.includes('UPDATE installations') &&
          sql.includes('challenge = NULL')
        ) {
          pausedOnce = true;
          consumeStarted.resolve();
          await releaseConsume.promise;
        }
      },
    });
    const keyPair = await createDeviceKeyPair();

    const firstChallenge = await issueChallenge({
      env,
      installationId: 'install-race',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.0.0',
    });
    const firstVerify = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: 'install-race',
      device_public_key: keyPair.devicePublicKey,
      challenge: firstChallenge.challenge,
      challenge_expires_at: firstChallenge.challenge_expires_at,
      hardware_hash: 'hardware-hash-race-initial',
      app_version: '1.0.0',
      signed_at: '2026-04-08T06:00:30.000Z',
    });
    const initialResponse = await postVerify(env, firstVerify);
    expect(initialResponse.status).toBe(200);

    const secondChallenge = await issueChallenge({
      env,
      installationId: 'install-race',
      devicePublicKey: keyPair.devicePublicKey,
      appVersion: '1.0.1',
    });
    const racingRequest = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: 'install-race',
      device_public_key: keyPair.devicePublicKey,
      challenge: secondChallenge.challenge,
      challenge_expires_at: secondChallenge.challenge_expires_at,
      hardware_hash: 'hardware-hash-race-final',
      app_version: '1.0.1',
      signed_at: '2026-04-08T06:00:40.000Z',
    });

    armConsumePause = true;
    installationReadCount = 0;
    const firstRacingResponsePromise = postVerify(env, racingRequest);
    await consumeStarted.promise;
    const secondRacingResponsePromise = postVerify(env, racingRequest);
    await secondReadStarted.promise;
    releaseConsume.resolve();

    const [firstRacingResponse, secondRacingResponse] = await Promise.all([
      firstRacingResponsePromise,
      secondRacingResponsePromise,
    ]);
    const responses = [firstRacingResponse, secondRacingResponse];
    const statuses = responses.map(({ status }) => status).sort();

    expect(statuses).toEqual([200, 409]);

    const successResponse = responses.find(({ status }) => status === 200);
    const conflictResponse = responses.find(({ status }) => status === 409);

    expect(successResponse).toBeDefined();
    expect(conflictResponse).toBeDefined();
    await expect(successResponse!.json()).resolves.toEqual(
      expect.objectContaining({
        release_token: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
        release_token_expires_at: '2026-04-08T06:15:00.000Z',
      }),
    );
    await expect(conflictResponse!.json()).resolves.toEqual({
      ...normalizedErrorEnvelope({
        code: 'challenge_invalid',
        class: 'security_fail',
        subcode: 'challenge_consumed',
        message: 'challenge has already been consumed or replaced',
      }),
    });

    const installation = env.__db
      .prepare(
        `SELECT challenge, challenge_expires_at, hardware_hash, app_version
           FROM installations
          WHERE installation_id = ?`,
      )
      .get('install-race') as Record<string, unknown>;

    expect(installation).toEqual({
      challenge: null,
      challenge_expires_at: null,
      hardware_hash: 'hardware-hash-race-final',
      app_version: '1.0.1',
    });

    const entitlement = env.__db
      .prepare(
        `SELECT status, release_session_ref, release_token_hash, release_token_expires_at
           FROM openrouter_entitlements
          WHERE installation_id = ?`,
      )
      .get('install-race') as Record<string, unknown>;

    expect(entitlement.status).toBe('pending_release');
    expect(entitlement.release_session_ref).toBeTypeOf('string');
    expect(entitlement.release_token_hash).toBeTypeOf('string');
    expect(entitlement.release_token_expires_at).toBe('2026-04-08T06:15:00.000Z');
  });
});
