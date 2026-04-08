import {
  createDeviceKeyPair,
  signCanonicalIssueRequest,
  signCanonicalVerifyRequest,
  type DeviceKeyPair,
} from './ed25519';
import type { TestBrokerEnv } from './sqlite-d1';
import { issueChallenge, postIssue, postVerify } from './trial-api';

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

export async function activatePendingReleaseSession(options: {
  env: TestBrokerEnv;
  installationId: string;
  appVersion: string;
  hardwareHash: string;
  verifySignedAt?: string;
  issueSignedAt?: string;
}): Promise<{
  keyPair: DeviceKeyPair;
  releaseToken: string;
  releaseTokenExpiresAt: string;
  response: Response;
}> {
  const pendingRelease = await createPendingReleaseSession(options);
  const requestBody = await signCanonicalIssueRequest(pendingRelease.keyPair.privateKey, {
    installation_id: options.installationId,
    device_public_key: pendingRelease.keyPair.devicePublicKey,
    release_token: pendingRelease.releaseToken,
    reason: 'llm_start',
    budget_usd: 0.07,
    model: 'google/gemma-4-26b-a4b-it',
    signed_at: options.issueSignedAt ?? '2026-04-08T06:00:45.000Z',
  });
  const response = await postIssue(options.env, requestBody);

  return {
    ...pendingRelease,
    response,
  };
}
