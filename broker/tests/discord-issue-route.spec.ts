import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createDeviceKeyPair,
  encodeBase64Url,
  signCanonicalDiscordIssueRequest,
  type DeviceKeyPair,
  type SignedDiscordIssueRequestInput,
} from './test-support/ed25519';
import { normalizedErrorEnvelope } from './test-support/errors';
import { sha256Base64Url } from './test-support/hash';
import { createTestBrokerEnv, type TestBrokerEnv } from './test-support/sqlite-d1';
import { postDiscordIssue, postDiscordStart } from './test-support/trial-api';

const REGISTERED_REDIRECT_URI = 'http://127.0.0.1:62187/discord/callback';
const APP_VERSION = '1.2.3';
const MODEL = 'google/gemma-4-26b-a4b-it';
const NOW_ISO = '2026-04-30T06:00:00.000Z';
const SIGNED_AT_ISO = '2026-04-30T06:00:30.000Z';
const DISCORD_TOKEN_URL = 'https://discord.com/api/oauth2/token';
const DISCORD_USER_URL = 'https://discord.com/api/users/@me';
const DISCORD_EPOCH_MS = 1420070400000n;
const THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000;

interface StartedDiscordSession {
  env: TestBrokerEnv;
  keyPair: DeviceKeyPair;
  installationId: string;
  state: string;
  issueNonce: string;
  redirectUri: string;
  appVersion: string;
  fingerprintSaltVersion: number;
}

interface DiscordSessionRow {
  installation_id: string;
  device_public_key: string;
  redirect_uri: string;
  pkce_code_verifier: string | null;
  issue_nonce_hash: string;
  fingerprint_salt_version: number;
  status: string;
  processing_started_at: string | null;
  eligibility_checked_at: string | null;
}

describe('Discord issue gate', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('valid signed request exchanges Discord code with PKCE and reaches activation placeholder after marking session processing', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-gate-valid');
    const sessionBefore = await readSessionByState(started.env, started.state);
    const code = 'discord-oauth-code-valid';
    const discordApi = mockDiscordApi({
      user: {
        id: discordSnowflakeForAgeDays(31),
        verified: true,
        email: 'verified@example.test',
      },
    });
    const requestBody = await signedIssueRequest(started, { code });

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(501);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_unavailable',
        class: 'retryable',
        subcode: 'not_implemented',
        message: 'Discord OpenRouter activation is not implemented yet',
      }),
    );
    expectTokenExchange(discordApi.fetchMock, {
      code,
      redirectUri: started.redirectUri,
      codeVerifier: sessionBefore.pkce_code_verifier,
    });
    expectDiscordUserFetch(discordApi.fetchMock);
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'processing',
        processing_started_at: NOW_ISO,
        eligibility_checked_at: NOW_ISO,
      }),
    );
  });

  it('returns a restart boundary when Discord token exchange fails after terminalizing the session', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-token-failure');
    mockDiscordApi({ tokenStatus: 500 });
    const requestBody = await signedIssueRequest(started);

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(410);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'challenge_expired',
        class: 'retryable',
        subcode: 'discord_oauth_failed',
        retryAfterMs: 0,
        message: 'Discord OAuth verification failed; restart Discord OAuth onboarding',
      }),
    );
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
      }),
    );
  });

  it('returns a restart boundary when Discord user fetch fails after terminalizing the session', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-user-fetch-failure');
    mockDiscordApi({ userStatus: 500 });
    const requestBody = await signedIssueRequest(started);

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(410);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'challenge_expired',
        class: 'retryable',
        subcode: 'discord_oauth_failed',
        retryAfterMs: 0,
        message: 'Discord OAuth verification failed; restart Discord OAuth onboarding',
      }),
    );
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
      }),
    );
  });

  it('rejects invalid JSON with 400', async () => {
    const env = createTestBrokerEnv();

    const response = await postDiscordIssue(env, '{');

    expect(response.status).toBe(400);
  });

  it('rejects missing required fields with 400', async () => {
    const env = createTestBrokerEnv();

    const response = await postDiscordIssue(env, {});

    expect(response.status).toBe(400);
  });

  it('rejects unknown state without Discord token exchange', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-unknown-state');
    const discordApi = mockDiscordApi();
    const requestBody = await signedIssueRequest(started, {
      state: 'unknown-discord-oauth-state',
    });

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(410);
    expect(discordApi.fetchMock).not.toHaveBeenCalled();
  });

  it('rejects state binding mismatch without Discord token exchange', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-binding');
    const discordApi = mockDiscordApi();
    const requestBody = await signedIssueRequest(started, {
      installation_id: 'install-discord-issue-binding-other',
    });

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(409);
    expect(discordApi.fetchMock).not.toHaveBeenCalled();
  });

  it('rejects stale signed_at without Discord token exchange', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-stale');
    const discordApi = mockDiscordApi();
    const requestBody = await signedIssueRequest(started, {
      signed_at: '2026-04-30T06:01:01.000Z',
    });

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(401);
    expect(discordApi.fetchMock).not.toHaveBeenCalled();
  });

  it('keeps pending session reusable after invalid signature', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-invalid-signature');
    const code = 'discord-oauth-code-invalid-signature';
    const signedRequest = await signedIssueRequest(started, { code });
    const invalidSignature = encodeBase64Url(new Uint8Array(64).fill(7));

    const invalidResponse = await postDiscordIssue(started.env, {
      ...signedRequest,
      signature: invalidSignature,
    });

    expect(invalidResponse.status).toBe(401);
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'pending',
        pkce_code_verifier: expect.any(String),
      }),
    );

    const sessionBeforeRetry = await readSessionByState(started.env, started.state);
    const discordApi = mockDiscordApi({
      user: {
        id: discordSnowflakeForAgeDays(31),
        verified: true,
      },
    });
    const validResponse = await postDiscordIssue(started.env, signedRequest);

    expect(validResponse.status).toBe(501);
    expectTokenExchange(discordApi.fetchMock, {
      code,
      redirectUri: started.redirectUri,
      codeVerifier: sessionBeforeRetry.pkce_code_verifier,
    });
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'processing',
      }),
    );
  });

  it('rejects hardware salt version mismatch without Discord token exchange', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-salt-mismatch');
    const discordApi = mockDiscordApi();
    const requestBody = await signedIssueRequest(started, {
      hardware_hash_salt_version: started.fingerprintSaltVersion + 1,
    });

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'hardware_salt_mismatch',
        message: 'hardware_hash_salt_version does not match the pending Discord OAuth session',
      }),
    );
    expect(discordApi.fetchMock).not.toHaveBeenCalled();
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'pending',
        pkce_code_verifier: expect.any(String),
      }),
    );
  });
});

describe('Discord eligibility', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('rejects Discord accounts without verified email', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-eligibility-unverified');
    mockDiscordApi({
      user: {
        id: discordSnowflakeForAgeDays(31),
        verified: false,
      },
    });
    const requestBody = await signedIssueRequest(started);

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'discord_email_unverified',
        message: 'Discord email verification is required',
      }),
    );
  });

  it('rejects Discord accounts younger than 30 days', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-eligibility-too-new');
    mockDiscordApi({
      user: {
        id: discordSnowflakeForAgeMs(THIRTY_DAYS_MS - 1),
        verified: true,
      },
    });
    const requestBody = await signedIssueRequest(started);

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'discord_account_too_new',
        message: 'Discord account must be at least 30 days old',
      }),
    );
  });

  it('allows the exact 30-day Discord account age boundary to reach activation placeholder', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-eligibility-boundary');
    mockDiscordApi({
      user: {
        id: discordSnowflakeForAgeMs(THIRTY_DAYS_MS),
        verified: true,
      },
    });
    const requestBody = await signedIssueRequest(started);

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(501);
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'processing',
        eligibility_checked_at: NOW_ISO,
      }),
    );
  });

  it('rejects invalid Discord snowflakes and clears the failed session PKCE verifier', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-eligibility-invalid-snowflake');
    mockDiscordApi({
      user: {
        id: 'not-a-snowflake',
        verified: true,
      },
    });
    const requestBody = await signedIssueRequest(started);

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'discord_invalid_snowflake',
        message: 'Discord account identity is invalid',
      }),
    );
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
      }),
    );
  });
});

async function startDiscordSession(
  installationId: string,
): Promise<StartedDiscordSession> {
  const env = createTestBrokerEnv();
  const keyPair = await createDeviceKeyPair();
  const response = await postDiscordStart(env, {
    installation_id: installationId,
    device_public_key: keyPair.devicePublicKey,
    redirect_uri: REGISTERED_REDIRECT_URI,
    app_version: APP_VERSION,
  });

  if (response.status !== 200) {
    throw new Error(`Discord start failed with status ${response.status}`);
  }

  const payload = (await response.json()) as {
    authorization_url: string;
    issue_nonce: string;
    redirect_uri: string;
    fingerprint_salt_version: number;
  };
  const state = new URL(payload.authorization_url).searchParams.get('state');
  if (!state) {
    throw new Error('Discord authorization URL did not include state');
  }

  return {
    env,
    keyPair,
    installationId,
    state,
    issueNonce: payload.issue_nonce,
    redirectUri: payload.redirect_uri,
    appVersion: APP_VERSION,
    fingerprintSaltVersion: payload.fingerprint_salt_version,
  };
}

async function signedIssueRequest(
  started: StartedDiscordSession,
  overrides: Partial<SignedDiscordIssueRequestInput> = {},
): Promise<
  SignedDiscordIssueRequestInput & {
    signature_alg: 'ed25519';
    signature: string;
  }
> {
  return signCanonicalDiscordIssueRequest(started.keyPair.privateKey, {
    installation_id: started.installationId,
    device_public_key: started.keyPair.devicePublicKey,
    state: started.state,
    code: 'discord-oauth-code',
    redirect_uri: started.redirectUri,
    hardware_hash: 'hardware-hash-discord-issue',
    hardware_hash_salt_version: started.fingerprintSaltVersion,
    app_version: started.appVersion,
    reason: 'llm_start',
    budget_usd: 0.07,
    model: MODEL,
    issue_nonce: started.issueNonce,
    signed_at: SIGNED_AT_ISO,
    ...overrides,
  });
}

async function readSessionByState(
  env: TestBrokerEnv,
  state: string,
): Promise<DiscordSessionRow> {
  const stateHash = await sha256Base64Url(state);
  const row = env.__db
    .prepare(
      `SELECT installation_id,
              device_public_key,
              redirect_uri,
              pkce_code_verifier,
              issue_nonce_hash,
              fingerprint_salt_version,
              status,
              processing_started_at,
              eligibility_checked_at
         FROM discord_oauth_sessions
        WHERE state_hash = ?`,
    )
    .get(stateHash) as DiscordSessionRow | undefined;

  if (!row) {
    throw new Error('Discord OAuth session row was not found');
  }

  return row;
}

function mockDiscordApi(options: {
  user?: Record<string, unknown>;
  tokenStatus?: number;
  userStatus?: number;
} = {}): { fetchMock: ReturnType<typeof vi.fn> } {
  const user = options.user ?? {
    id: discordSnowflakeForAgeDays(31),
    verified: true,
  };
  const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';

    if (url === DISCORD_TOKEN_URL && method === 'POST') {
      if (options.tokenStatus !== undefined && options.tokenStatus >= 400) {
        return jsonResponse({ error: 'token exchange failed' }, options.tokenStatus);
      }

      return jsonResponse({
        access_token: 'discord-access-token',
        token_type: 'Bearer',
      });
    }

    if (url === DISCORD_USER_URL && method === 'GET') {
      if (options.userStatus !== undefined && options.userStatus >= 400) {
        return jsonResponse({ error: 'user fetch failed' }, options.userStatus);
      }

      return jsonResponse(user);
    }

    throw new Error(`unexpected Discord API request: ${method} ${url}`);
  });

  vi.stubGlobal('fetch', fetchMock as typeof fetch);
  return { fetchMock };
}

function expectTokenExchange(
  fetchMock: ReturnType<typeof vi.fn>,
  expected: {
    code: string;
    redirectUri: string;
    codeVerifier: string | null;
  },
): void {
  expect(fetchMock).toHaveBeenCalledTimes(2);
  const [input, init] = fetchMock.mock.calls[0] as [string | URL, RequestInit];
  expect(String(input)).toBe(DISCORD_TOKEN_URL);
  expect(init.method).toBe('POST');
  expect(init.headers).toEqual({
    'content-type': 'application/x-www-form-urlencoded',
  });

  const params = new URLSearchParams(String(init.body));
  expect(params.get('grant_type')).toBe('authorization_code');
  expect(params.get('code')).toBe(expected.code);
  expect(params.get('redirect_uri')).toBe(expected.redirectUri);
  expect(params.get('client_id')).toBe('test-discord-client-id');
  expect(params.get('client_secret')).toBe('test-discord-client-secret');
  expect(params.get('code_verifier')).toBe(expected.codeVerifier);
}

function expectDiscordUserFetch(fetchMock: ReturnType<typeof vi.fn>): void {
  const [input, init] = fetchMock.mock.calls[1] as [string | URL, RequestInit];
  expect(String(input)).toBe(DISCORD_USER_URL);
  expect(init.method).toBe('GET');
  expect(init.headers).toEqual({
    authorization: 'Bearer discord-access-token',
  });
}

function discordSnowflakeForAgeDays(days: number): string {
  return discordSnowflakeForAgeMs(days * 24 * 60 * 60 * 1000);
}

function discordSnowflakeForAgeMs(ageMs: number): string {
  return discordSnowflakeForDate(new Date(Date.now() - ageMs));
}

function discordSnowflakeForDate(createdAt: Date): string {
  const timestamp = BigInt(createdAt.getTime()) - DISCORD_EPOCH_MS;
  return (timestamp << 22n).toString();
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'content-type': 'application/json',
    },
  });
}
