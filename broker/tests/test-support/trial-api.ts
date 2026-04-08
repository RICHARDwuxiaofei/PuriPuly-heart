import app from '../../src/index';

import type { TestBrokerEnv } from './sqlite-d1';

export interface ChallengeResponse {
  challenge: string;
  challenge_expires_at: string;
}

export async function issueChallenge(options: {
  env: TestBrokerEnv;
  installationId: string;
  devicePublicKey: string;
  appVersion: string;
}): Promise<ChallengeResponse> {
  const response = await app.request(
    'http://broker.test/v1/trial/challenge',
    {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        installation_id: options.installationId,
        device_public_key: options.devicePublicKey,
        app_version: options.appVersion,
      }),
    },
    options.env,
  );

  if (response.status !== 200) {
    throw new Error(`challenge request failed with status ${response.status}`);
  }

  return (await response.json()) as ChallengeResponse;
}

export async function postVerify(
  env: TestBrokerEnv,
  body: object | string,
): Promise<Response> {
  return app.request(
    'http://broker.test/v1/trial/challenge/verify',
    {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
      },
      body: typeof body === 'string' ? body : JSON.stringify(body),
    },
    env,
  );
}
