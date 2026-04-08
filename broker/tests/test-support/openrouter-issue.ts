import {
  createDeviceKeyPair,
  signCanonicalVerifyRequest,
  type DeviceKeyPair,
} from './ed25519';
import type { TestBrokerEnv } from './sqlite-d1';
import { issueChallenge, postVerify } from './trial-api';

export async function createPendingReleaseSession(options: {
  env: TestBrokerEnv;
  installationId: string;
  appVersion: string;
  hardwareHash: string;
  verifySignedAt?: string;
}): Promise<{
  keyPair: DeviceKeyPair;
  releaseToken: string;
  releaseTokenExpiresAt: string;
}> {
  const keyPair = await createDeviceKeyPair();
  const challenge = await issueChallenge({
    env: options.env,
    installationId: options.installationId,
    devicePublicKey: keyPair.devicePublicKey,
    appVersion: options.appVersion,
  });
  const requestBody = await signCanonicalVerifyRequest(keyPair.privateKey, {
    installation_id: options.installationId,
    device_public_key: keyPair.devicePublicKey,
    challenge: challenge.challenge,
    challenge_expires_at: challenge.challenge_expires_at,
    hardware_hash: options.hardwareHash,
    app_version: options.appVersion,
    signed_at: options.verifySignedAt ?? '2026-04-08T06:00:30.000Z',
  });
  const response = await postVerify(options.env, requestBody);

  if (response.status !== 200) {
    throw new Error(`verify request failed with status ${response.status}`);
  }

  const payload = (await response.json()) as {
    release_token: string;
    release_token_expires_at: string;
  };

  return {
    keyPair,
    releaseToken: payload.release_token,
    releaseTokenExpiresAt: payload.release_token_expires_at,
  };
}
