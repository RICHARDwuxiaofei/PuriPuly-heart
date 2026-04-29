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
import {
  createTestBrokerEnv,
  insertEntitlement,
  type TestBrokerEnv,
} from './test-support/sqlite-d1';
import { postDiscordIssue, postDiscordStart } from './test-support/trial-api';
import { updateAbuseControls } from './test-support/abuse-controls';

const REGISTERED_REDIRECT_URI = 'http://127.0.0.1:62187/discord/callback';
const APP_VERSION = '1.2.3';
const MODEL = 'google/gemma-4-26b-a4b-it';
const NOW_ISO = '2026-04-30T06:00:00.000Z';
const SIGNED_AT_ISO = '2026-04-30T06:00:30.000Z';
const DISCORD_TOKEN_URL = 'https://discord.com/api/oauth2/token';
const DISCORD_USER_URL = 'https://discord.com/api/users/@me';
const OPENROUTER_KEYS_URL = 'https://openrouter.ai/api/v1/keys';
const OPENROUTER_GUARDRAIL_URL =
  'https://openrouter.ai/api/v1/guardrails/test-managed-guardrail-id/assignments/keys';
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
  consumed_at: string | null;
}

interface DiscordIdentityRow {
  discord_user_ref: string;
  entitlement_installation_id: string | null;
  status: string;
  updated_at: string;
}

interface IssueSuccessEventRow {
  installation_id: string;
  managed_credential_ref: string;
  ip_hash: string | null;
  ip_prefix_hash: string | null;
  observed_at: string;
}

describe('Discord issue gate', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('reservation success exchanges Discord code with PKCE, activates one managed key, monitors success, and consumes the session', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-gate-valid');
    const sessionBefore = await readSessionByState(started.env, started.state);
    const code = 'discord-oauth-code-valid';
    const rawDiscordUserId = discordSnowflakeForAgeDays(31);
    const rawDiscordEmail = 'verified@example.test';
    const expectedDiscordUserRef = await deriveExpectedDiscordUserRef(
      started.env.DISCORD_USER_REF_SECRET,
      rawDiscordUserId,
    );
    const discordApi = mockDiscordApi({
      user: {
        id: rawDiscordUserId,
        verified: true,
        email: rawDiscordEmail,
      },
    });
    const requestBody = await signedIssueRequest(started, { code });

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(200);
    const payload = (await response.json()) as Record<string, unknown>;
    expect(payload).toEqual(
      expect.objectContaining({
        openrouter_api_key: 'or-discord-managed-child-key-test-1',
        managed_credential_ref: 'hash_discord_managed_child_test_1',
        managed_state: {
          lifecycle: 'active',
          managed_availability: true,
        },
        expires_at: '2026-07-30T06:00:00.000Z',
        budget_usd: 0.07,
        model: MODEL,
      }),
    );
    expectTokenExchange(discordApi.fetchMock, {
      code,
      redirectUri: started.redirectUri,
      codeVerifier: sessionBefore.pkce_code_verifier,
    });
    expectDiscordUserFetch(discordApi.fetchMock);
    expect(discordApi.openRouterCreateCalls).toHaveLength(1);
    expect(discordApi.openRouterGuardrailCalls).toHaveLength(1);
    expectCallOrder(discordApi.fetchMock, [
      `POST ${OPENROUTER_KEYS_URL}`,
      `POST ${OPENROUTER_GUARDRAIL_URL}`,
    ]);
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'consumed',
        pkce_code_verifier: null,
        processing_started_at: NOW_ISO,
        eligibility_checked_at: NOW_ISO,
        consumed_at: NOW_ISO,
      }),
    );
    await expect(readEntitlement(started.env, started.installationId)).resolves.toEqual(
      expect.objectContaining({
        status: 'active',
        managed_credential_ref: 'hash_discord_managed_child_test_1',
        issued_at: NOW_ISO,
        expires_at: '2026-07-30T06:00:00.000Z',
        verified_hardware_hash: 'hardware-hash-discord-issue',
        discord_user_ref: expectedDiscordUserRef,
        discord_issue_status: 'active',
        discord_issue_reserved_at: NOW_ISO,
        discord_issue_delivered_at: NOW_ISO,
      }),
    );
    await expect(readDiscordIdentity(started.env, expectedDiscordUserRef)).resolves.toEqual(
      expect.objectContaining({
        entitlement_installation_id: started.installationId,
        status: 'active',
        updated_at: NOW_ISO,
      }),
    );
    const issueSuccessEvents = readIssueSuccessEvents(started.env);
    expect(issueSuccessEvents).toEqual([
      expect.objectContaining({
        installation_id: started.installationId,
        managed_credential_ref: 'hash_discord_managed_child_test_1',
        observed_at: NOW_ISO,
      }),
    ]);
    expect(JSON.stringify(issueSuccessEvents)).not.toContain(rawDiscordUserId);
    expect(JSON.stringify(issueSuccessEvents)).not.toContain(rawDiscordEmail);
  });

  it('returns a restart boundary when Discord token exchange fails after terminalizing the session', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-issue-token-failure');
    const sessionBefore = await readSessionByState(started.env, started.state);
    const code = 'discord-oauth-code-token-failure-redact';
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    mockDiscordApi({ tokenStatus: 500 });
    const requestBody = await signedIssueRequest(started, { code });

    const response = await postDiscordIssue(started.env, requestBody);

    expect(response.status).toBe(410);
    const responseText = await response.text();
    expect(JSON.parse(responseText)).toEqual(
      normalizedErrorEnvelope({
        code: 'challenge_expired',
        class: 'retryable',
        subcode: 'discord_oauth_failed',
        retryAfterMs: 0,
        message: 'Discord OAuth verification failed; restart Discord OAuth onboarding',
      }),
    );
    const sensitiveValues = [
      code,
      started.state,
      sessionBefore.pkce_code_verifier,
    ].filter((value): value is string => value !== null);
    expectTextNotToContainSensitiveValues(responseText, sensitiveValues);
    expectTextNotToContainSensitiveValues(
      stringifyConsoleCalls(consoleErrorSpy),
      sensitiveValues,
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

    expect(validResponse.status).toBe(200);
    expectTokenExchange(discordApi.fetchMock, {
      code,
      redirectUri: started.redirectUri,
      codeVerifier: sessionBeforeRetry.pkce_code_verifier,
    });
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'consumed',
        pkce_code_verifier: null,
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

  it('reservation/lifetime rejects a Discord account that already has a delivered managed entitlement', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const env = createTestBrokerEnv();
    const discordUserId = discordSnowflakeForAgeDays(31);
    const first = await startDiscordSession('install-discord-lifetime-first', env);
    const discordApi = mockDiscordApi({
      user: {
        id: discordUserId,
        verified: true,
      },
    });

    const firstResponse = await postDiscordIssue(
      env,
      await signedIssueRequest(first, {
        code: 'discord-oauth-code-lifetime-first',
        hardware_hash: 'hardware-hash-lifetime-first',
      }),
    );
    expect(firstResponse.status).toBe(200);

    const second = await startDiscordSession('install-discord-lifetime-second', env);
    const secondResponse = await postDiscordIssue(
      env,
      await signedIssueRequest(second, {
        code: 'discord-oauth-code-lifetime-second',
        hardware_hash: 'hardware-hash-lifetime-second',
      }),
    );

    expect(secondResponse.status).toBe(409);
    await expect(secondResponse.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'discord_lifetime_used',
        message: 'Discord account has already used a managed trial',
      }),
    );
    expect(discordApi.openRouterCreateCalls).toHaveLength(1);
    await expect(readSessionByState(env, second.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
      }),
    );
    expect(countDiscordIdentities(env)).toBe(1);
  });

  it('reservation/lifetime rejects a Discord account that already has an issuing reservation', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const env = createTestBrokerEnv();
    const discordUserId = discordSnowflakeForAgeDays(31);
    const discordUserRef = await deriveExpectedDiscordUserRef(
      env.DISCORD_USER_REF_SECRET,
      discordUserId,
    );
    insertInstallation(env, {
      installationId: 'install-discord-lifetime-reserved-existing',
      devicePublicKey: 'reserved-device-public-key',
      hardwareHash: 'hardware-hash-lifetime-reserved-existing',
      hardwareHashSaltVersion: 7,
    });
    env.__db
      .prepare(
        `INSERT INTO discord_identities (
            discord_user_ref,
            entitlement_installation_id,
            status,
            ref_secret_version,
            created_at,
            updated_at
          ) VALUES (?, ?, 'issuing', 1, ?, ?)`,
      )
      .run(
        discordUserRef,
        'install-discord-lifetime-reserved-existing',
        NOW_ISO,
        NOW_ISO,
      );

    const started = await startDiscordSession('install-discord-lifetime-reserved-new', env);
    const discordApi = mockDiscordApi({
      user: {
        id: discordUserId,
        verified: true,
      },
    });

    const response = await postDiscordIssue(
      env,
      await signedIssueRequest(started, {
        code: 'discord-oauth-code-lifetime-reserved',
        hardware_hash: 'hardware-hash-lifetime-reserved-new',
      }),
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'discord_lifetime_used',
        message: 'Discord account has already used a managed trial',
      }),
    );
    expect(discordApi.openRouterCreateCalls).toHaveLength(0);
    expect(countDiscordIdentities(env)).toBe(1);
    expect(countDiscordEntitlements(env)).toBe(0);
  });

  it('hardware duplicate rejects legacy installation evidence before creating a child key', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const env = createTestBrokerEnv();
    insertInstallation(env, {
      installationId: 'install-discord-hardware-legacy-existing',
      devicePublicKey: 'legacy-device-public-key',
      hardwareHash: 'hardware-hash-duplicate-legacy',
      hardwareHashSaltVersion: 7,
    });
    insertEntitlement(env, {
      installation_id: 'install-discord-hardware-legacy-existing',
      status: 'active',
      budget_usd: 0.07,
      managed_credential_ref: 'legacy-managed-key',
      issued_at: NOW_ISO,
      expires_at: '2026-07-30T06:00:00.000Z',
    });

    const started = await startDiscordSession('install-discord-hardware-legacy-new', env);
    const discordApi = mockDiscordApi();
    const response = await postDiscordIssue(
      env,
      await signedIssueRequest(started, {
        code: 'discord-oauth-code-hardware-legacy',
        hardware_hash: 'hardware-hash-duplicate-legacy',
      }),
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'hardware_duplicate',
        message: 'This device has already used a managed trial',
      }),
    );
    expect(discordApi.openRouterCreateCalls).toHaveLength(0);
    expect(countDiscordIdentities(env)).toBe(0);
    expect(countDiscordEntitlements(env)).toBe(1);
  });

  it('hardware duplicate rejects delivered Discord entitlement evidence before creating a child key', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const env = createTestBrokerEnv();
    insertInstallation(env, {
      installationId: 'install-discord-hardware-entitlement-existing',
      devicePublicKey: 'entitlement-device-public-key',
      hardwareHash: null,
      hardwareHashSaltVersion: null,
    });
    insertEntitlement(env, {
      installation_id: 'install-discord-hardware-entitlement-existing',
      status: 'active',
      budget_usd: 0.07,
      managed_credential_ref: 'discord-managed-key-existing',
      issued_at: NOW_ISO,
      expires_at: '2026-07-30T06:00:00.000Z',
      verified_hardware_hash: 'hardware-hash-duplicate-entitlement',
      verified_hardware_hash_salt_version: 7,
      discord_issue_status: 'active',
      discord_issue_reserved_at: NOW_ISO,
      discord_issue_delivered_at: NOW_ISO,
    });

    const started = await startDiscordSession('install-discord-hardware-entitlement-new', env);
    const discordApi = mockDiscordApi({
      user: {
        id: discordSnowflakeForAgeDays(32),
        verified: true,
      },
    });
    const response = await postDiscordIssue(
      env,
      await signedIssueRequest(started, {
        code: 'discord-oauth-code-hardware-entitlement',
        hardware_hash: 'hardware-hash-duplicate-entitlement',
      }),
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'hardware_duplicate',
        message: 'This device has already used a managed trial',
      }),
    );
    expect(discordApi.openRouterCreateCalls).toHaveLength(0);
    expect(countDiscordIdentities(env)).toBe(0);
    expect(countDiscordEntitlements(env)).toBe(1);
  });

  it('same installation hardware duplicate rejects a previously delivered entitlement before creating a child key', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const env = createTestBrokerEnv();
    const installationId = 'install-discord-same-installation-delivered-hardware';
    insertInstallation(env, {
      installationId,
      devicePublicKey: 'same-installation-delivered-device-key',
      hardwareHash: 'hardware-hash-same-installation-delivered',
      hardwareHashSaltVersion: 7,
      appVersion: '1.0-existing-active',
      challenge: 'existing-active-challenge',
      challengeExpiresAt: '2026-04-30T06:15:00.000Z',
      challengeSaltVersion: 7,
    });
    insertEntitlement(env, {
      installation_id: installationId,
      status: 'active',
      budget_usd: 0.07,
      managed_credential_ref: 'same-installation-delivered-managed-key',
      issued_at: NOW_ISO,
      expires_at: '2026-07-30T06:00:00.000Z',
      verified_hardware_hash: 'hardware-hash-same-installation-delivered',
      verified_hardware_hash_salt_version: 7,
      discord_issue_status: 'active',
      discord_issue_reserved_at: NOW_ISO,
      discord_issue_delivered_at: NOW_ISO,
    });
    const installationBefore = await readInstallation(env, installationId);

    const started = await startDiscordSession(installationId, env);
    const discordApi = mockDiscordApi({
      user: {
        id: discordSnowflakeForAgeDays(32),
        verified: true,
      },
    });
    const response = await postDiscordIssue(
      env,
      await signedIssueRequest(started, {
        code: 'discord-oauth-code-same-installation-delivered-hardware',
        hardware_hash: 'hardware-hash-same-installation-delivered',
      }),
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'hardware_duplicate',
        message: 'This device has already used a managed trial',
      }),
    );
    expect(discordApi.openRouterCreateCalls).toHaveLength(0);
    expect(countDiscordIdentities(env)).toBe(0);
    await expect(readEntitlement(env, installationId)).resolves.toEqual(
      expect.objectContaining({
        status: 'active',
        managed_credential_ref: 'same-installation-delivered-managed-key',
        discord_issue_status: 'active',
      }),
    );
    await expect(readInstallation(env, installationId)).resolves.toEqual(
      installationBefore,
    );
  });

  it('same installation issuing conflict does not overwrite another Discord reservation or strand identity', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const env = createTestBrokerEnv();
    const installationId = 'install-discord-same-installation-issuing-conflict';
    const existingDiscordUserRef = await deriveExpectedDiscordUserRef(
      env.DISCORD_USER_REF_SECRET,
      discordSnowflakeForAgeDays(35),
    );
    insertInstallation(env, {
      installationId,
      devicePublicKey: 'same-installation-issuing-device-key',
      hardwareHash: 'hardware-hash-same-installation-issuing-old',
      hardwareHashSaltVersion: 7,
      appVersion: '1.0-existing-issuing',
      challenge: 'existing-issuing-challenge',
      challengeExpiresAt: '2026-04-30T06:15:00.000Z',
      challengeSaltVersion: 7,
    });
    insertDiscordIdentity(env, {
      discordUserRef: existingDiscordUserRef,
      installationId,
      status: 'issuing',
    });
    insertEntitlement(env, {
      installation_id: installationId,
      status: 'pending_release',
      budget_usd: 0.07,
      verified_hardware_hash: 'hardware-hash-same-installation-issuing-old',
      verified_hardware_hash_salt_version: 7,
      discord_user_ref: existingDiscordUserRef,
      discord_issue_status: 'issuing',
      discord_issue_reserved_at: NOW_ISO,
    });
    const installationBefore = await readInstallation(env, installationId);

    const started = await startDiscordSession(installationId, env);
    const discordApi = mockDiscordApi({
      user: {
        id: discordSnowflakeForAgeDays(31),
        verified: true,
      },
    });
    const response = await postDiscordIssue(
      env,
      await signedIssueRequest(started, {
        code: 'discord-oauth-code-same-installation-issuing-conflict',
        hardware_hash: 'hardware-hash-same-installation-issuing-new',
      }),
    );

    expect(response.status).toBe(410);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'challenge_expired',
        class: 'retryable',
        subcode: 'discord_installation_already_issuing',
        retryAfterMs: 0,
        message:
          'Discord managed entitlement is already issuing for this installation; restart Discord OAuth onboarding',
      }),
    );
    expect(discordApi.openRouterCreateCalls).toHaveLength(0);
    expect(countDiscordIdentities(env)).toBe(1);
    await expect(readSessionByState(env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
      }),
    );
    await expect(readEntitlement(env, installationId)).resolves.toEqual(
      expect.objectContaining({
        status: 'pending_release',
        discord_user_ref: existingDiscordUserRef,
        discord_issue_status: 'issuing',
        verified_hardware_hash: 'hardware-hash-same-installation-issuing-old',
      }),
    );
    await expect(readInstallation(env, installationId)).resolves.toEqual(
      installationBefore,
    );
  });

  it('cap rejects final issue when the UTC daily managed issuance cap is reached', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const env = createTestBrokerEnv();
    updateAbuseControls(env, (controls) => {
      controls.newActiveEntitlementsPerDay.maxCount = 1;
    });
    insertInstallation(env, {
      installationId: 'install-discord-cap-existing',
      devicePublicKey: 'cap-existing-device-public-key',
      hardwareHash: 'hardware-hash-cap-existing',
      hardwareHashSaltVersion: 7,
    });
    insertEntitlement(env, {
      installation_id: 'install-discord-cap-existing',
      status: 'active',
      budget_usd: 0.07,
      managed_credential_ref: 'cap-existing-managed-key',
      issued_at: NOW_ISO,
      expires_at: '2026-07-30T06:00:00.000Z',
      discord_issue_status: 'active',
      discord_issue_delivered_at: NOW_ISO,
    });

    const started = await startDiscordSession('install-discord-cap-new', env);
    const discordApi = mockDiscordApi();
    const response = await postDiscordIssue(
      env,
      await signedIssueRequest(started, {
        code: 'discord-oauth-code-cap',
        hardware_hash: 'hardware-hash-cap-new',
      }),
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'issuance_suspended',
        class: 'retryable',
        subcode: 'global_cap_reached',
        retryAfterMs: 64_800_000,
        message: 'Daily managed issuance cap reached',
      }),
    );
    expect(discordApi.openRouterCreateCalls).toHaveLength(0);
    await expect(readSessionByState(env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
      }),
    );
    expect(countDiscordIdentities(env)).toBe(0);
  });

  it('replay rejects repeated final issue for the same consumed state without a second key side effect', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const started = await startDiscordSession('install-discord-replay');
    const discordApi = mockDiscordApi();
    const requestBody = await signedIssueRequest(started, {
      code: 'discord-oauth-code-replay',
      hardware_hash: 'hardware-hash-replay',
    });

    const firstResponse = await postDiscordIssue(started.env, requestBody);
    const replayResponse = await postDiscordIssue(started.env, requestBody);

    expect(firstResponse.status).toBe(200);
    expect(replayResponse.status).toBe(409);
    await expect(replayResponse.json()).resolves.toEqual(
      normalizedErrorEnvelope({
        code: 'challenge_invalid',
        class: 'security_fail',
        subcode: 'discord_oauth_session_consumed',
        message: 'Discord OAuth session can no longer issue a managed key',
      }),
    );
    expect(discordApi.openRouterCreateCalls).toHaveLength(1);
    expect(countDiscordEntitlements(started.env, "discord_issue_status = 'active'")).toBe(1);
    expect(countDiscordEntitlements(started.env, "discord_issue_status = 'issuing'")).toBe(0);
  });

  it('PKCE success clears verifier, invalid signature and salt mismatch preserve verifier', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const invalidSignatureSession = await startDiscordSession('install-discord-pkce-invalid-signature');
    const invalidSignatureBody = await signedIssueRequest(invalidSignatureSession, {
      code: 'discord-oauth-code-pkce-invalid-signature',
    });
    const invalidSignature = encodeBase64Url(new Uint8Array(64).fill(9));

    const invalidSignatureResponse = await postDiscordIssue(invalidSignatureSession.env, {
      ...invalidSignatureBody,
      signature: invalidSignature,
    });

    expect(invalidSignatureResponse.status).toBe(401);
    await expect(
      readSessionByState(invalidSignatureSession.env, invalidSignatureSession.state),
    ).resolves.toEqual(
      expect.objectContaining({
        status: 'pending',
        pkce_code_verifier: expect.any(String),
      }),
    );

    const saltMismatchSession = await startDiscordSession('install-discord-pkce-salt-mismatch');
    const saltMismatchResponse = await postDiscordIssue(
      saltMismatchSession.env,
      await signedIssueRequest(saltMismatchSession, {
        code: 'discord-oauth-code-pkce-salt-mismatch',
        hardware_hash_salt_version: saltMismatchSession.fingerprintSaltVersion + 1,
      }),
    );

    expect(saltMismatchResponse.status).toBe(409);
    await expect(readSessionByState(saltMismatchSession.env, saltMismatchSession.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'pending',
        pkce_code_verifier: expect.any(String),
      }),
    );

    const successSession = await startDiscordSession('install-discord-pkce-success');
    mockDiscordApi();
    const successResponse = await postDiscordIssue(
      successSession.env,
      await signedIssueRequest(successSession, {
        code: 'discord-oauth-code-pkce-success',
        hardware_hash: 'hardware-hash-pkce-success',
      }),
    );

    expect(successResponse.status).toBe(200);
    await expect(readSessionByState(successSession.env, successSession.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'consumed',
        pkce_code_verifier: null,
      }),
    );
  });

  it('expiry and cancel reject before Discord token exchange, clear PKCE, and do not burn eligibility', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const expired = await startDiscordSession('install-discord-expiry');
    vi.setSystemTime(new Date('2026-04-30T06:06:00.000Z'));
    const expiredDiscordApi = mockDiscordApi();
    const expiredResponse = await postDiscordIssue(
      expired.env,
      await signedIssueRequest(expired, {
        code: 'discord-oauth-code-expiry',
        signed_at: '2026-04-30T06:06:00.000Z',
      }),
    );

    expect(expiredResponse.status).toBe(410);
    expect(expiredDiscordApi.fetchMock).not.toHaveBeenCalled();
    await expect(readSessionByState(expired.env, expired.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'expired',
        pkce_code_verifier: null,
      }),
    );
    expect(countDiscordIdentities(expired.env)).toBe(0);
    expect(countDiscordEntitlements(expired.env)).toBe(0);

    vi.setSystemTime(new Date(NOW_ISO));
    const canceled = await startDiscordSession('install-discord-cancel');
    await envUpdateSessionStatus(canceled.env, canceled.state, 'canceled');
    const canceledDiscordApi = mockDiscordApi();
    const canceledResponse = await postDiscordIssue(
      canceled.env,
      await signedIssueRequest(canceled, {
        code: 'discord-oauth-code-cancel',
      }),
    );

    expect(canceledResponse.status).toBe(409);
    expect(canceledDiscordApi.fetchMock).not.toHaveBeenCalled();
    await expect(readSessionByState(canceled.env, canceled.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'canceled',
        pkce_code_verifier: null,
      }),
    );
    expect(countDiscordIdentities(canceled.env)).toBe(0);
    expect(countDiscordEntitlements(canceled.env)).toBe(0);
  });

  it('reservation release removes issuing identity and entitlement when child-key creation fails before a key is returned', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const discordUserId = discordSnowflakeForAgeDays(31);
    const env = createTestBrokerEnv();
    const started = await startDiscordSession('install-discord-reservation-release', env);
    const discordApi = mockDiscordApi({
      openRouterMode: 'create_failure',
      user: {
        id: discordUserId,
        verified: true,
      },
    });
    const response = await postDiscordIssue(
      started.env,
      await signedIssueRequest(started, {
        code: 'discord-oauth-code-reservation-release',
        hardware_hash: 'hardware-hash-reservation-release',
      }),
    );

    expect(response.status).toBe(500);
    expect(await response.text()).not.toContain('or-discord-managed-child-key-test-1');
    expect(discordApi.openRouterCreateCalls).toHaveLength(1);
    expect(countDiscordIdentities(started.env)).toBe(0);
    expect(countDiscordEntitlements(started.env)).toBe(0);
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
      }),
    );

    const retry = await startDiscordSession('install-discord-reservation-release-retry', env);
    mockDiscordApi({
      user: {
        id: discordUserId,
        verified: true,
      },
    });
    const retryResponse = await postDiscordIssue(
      env,
      await signedIssueRequest(retry, {
        code: 'discord-oauth-code-reservation-release-retry',
        hardware_hash: 'hardware-hash-reservation-release-retry',
      }),
    );
    expect(retryResponse.status).toBe(200);
  });

  it('guardrail assignment failure after child-key creation cleans up and releases Discord eligibility for fresh OAuth', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const env = createTestBrokerEnv();
    const discordUserId = discordSnowflakeForAgeDays(31);
    const started = await startDiscordSession('install-discord-guardrail-cleanup-success', env);
    const discordApi = mockDiscordApi({
      openRouterMode: 'guardrail_failure',
      user: {
        id: discordUserId,
        verified: true,
      },
    });

    const response = await postDiscordIssue(
      env,
      await signedIssueRequest(started, {
        code: 'discord-oauth-code-guardrail-cleanup-success',
        hardware_hash: 'hardware-hash-guardrail-cleanup-success',
      }),
    );

    expect(response.status).toBe(500);
    expect(await response.text()).not.toContain('or-discord-managed-child-key-test-1');
    expect(discordApi.openRouterCreateCalls).toHaveLength(1);
    expect(discordApi.openRouterGuardrailCalls).toHaveLength(1);
    expect(discordApi.openRouterCleanupCalls.map(({ init }) => init?.method)).toEqual([
      'PATCH',
      'DELETE',
    ]);
    expect(countDiscordIdentities(env)).toBe(0);
    expect(countDiscordEntitlements(env)).toBe(0);
    await expect(readSessionByState(env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
      }),
    );

    const retry = await startDiscordSession('install-discord-guardrail-cleanup-success-retry', env);
    mockDiscordApi({
      user: {
        id: discordUserId,
        verified: true,
      },
    });
    const retryResponse = await postDiscordIssue(
      env,
      await signedIssueRequest(retry, {
        code: 'discord-oauth-code-guardrail-cleanup-success-retry',
        hardware_hash: 'hardware-hash-guardrail-cleanup-success-retry',
      }),
    );
    expect(retryResponse.status).toBe(200);
  });

  it('guardrail assignment failure with cleanup failure records cleanup_required without leaking sensitive values', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));

    const rawCode = 'discord-oauth-code-guardrail-cleanup-redact';
    const rawDiscordUserId = discordSnowflakeForAgeDays(31);
    const rawEmail = 'sensitive-redaction@example.test';
    const rawAccessToken = 'discord-access-token-sensitive-redact';
    const rawRefreshToken = 'discord-refresh-token-sensitive-redact';
    const rawOpenRouterChildKey = 'or-discord-managed-child-key-sensitive-redact';
    const childKeyHash = 'hash_discord_managed_child_cleanup_required';
    const env = createTestBrokerEnv();
    const started = await startDiscordSession('install-discord-guardrail-cleanup-required', env);
    const sessionBefore = await readSessionByState(env, started.state);
    const expectedDiscordUserRef = await deriveExpectedDiscordUserRef(
      env.DISCORD_USER_REF_SECRET,
      rawDiscordUserId,
    );
    const sensitiveValues = [
      rawCode,
      started.state,
      sessionBefore.pkce_code_verifier,
      rawAccessToken,
      rawRefreshToken,
      rawDiscordUserId,
      rawEmail,
      rawOpenRouterChildKey,
    ].filter((value): value is string => value !== null);
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const discordApi = mockDiscordApi({
      openRouterMode: 'guardrail_failure_cleanup_failure',
      rawChildKey: rawOpenRouterChildKey,
      childKeyHash,
      accessToken: rawAccessToken,
      refreshToken: rawRefreshToken,
      user: {
        id: rawDiscordUserId,
        verified: true,
        email: rawEmail,
      },
      guardrailFailureMessage: `guardrail failed ${rawCode} ${started.state} ${sessionBefore.pkce_code_verifier} ${rawAccessToken} ${rawRefreshToken} ${rawDiscordUserId} ${rawEmail} ${rawOpenRouterChildKey}`,
      cleanupFailureMessage: `cleanup failed ${rawCode} ${started.state} ${sessionBefore.pkce_code_verifier} ${rawAccessToken} ${rawRefreshToken} ${rawDiscordUserId} ${rawEmail} ${rawOpenRouterChildKey}`,
    });

    const response = await postDiscordIssue(
      env,
      await signedIssueRequest(started, {
        code: rawCode,
        hardware_hash: 'hardware-hash-guardrail-cleanup-required',
      }),
    );

    expect(response.status).toBe(500);
    const responseText = await response.text();
    expectTextNotToContainSensitiveValues(responseText, sensitiveValues);
    expectTextNotToContainSensitiveValues(
      stringifyConsoleCalls(consoleErrorSpy),
      sensitiveValues,
    );
    expect(discordApi.openRouterCreateCalls).toHaveLength(1);
    expect(discordApi.openRouterGuardrailCalls).toHaveLength(1);
    expect(discordApi.openRouterCleanupCalls.map(({ init }) => init?.method)).toEqual([
      'PATCH',
      'DELETE',
    ]);
    await expect(readEntitlement(env, started.installationId)).resolves.toEqual(
      expect.objectContaining({
        status: 'pending_release',
        managed_credential_ref: childKeyHash,
        issued_at: null,
        expires_at: null,
        discord_user_ref: expectedDiscordUserRef,
        discord_issue_status: 'cleanup_required',
        discord_issue_delivered_at: null,
      }),
    );
    await expect(readDiscordIdentity(env, expectedDiscordUserRef)).resolves.toEqual(
      expect.objectContaining({
        entitlement_installation_id: started.installationId,
        status: 'cleanup_required',
      }),
    );
    await expect(readSessionByState(env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
      }),
    );
  });
});

describe('Discord eligibility', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('PKCE policy terminal rejection clears verifier for Discord accounts without verified email', async () => {
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
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'failed',
        pkce_code_verifier: null,
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

  it('allows the exact 30-day Discord account age boundary to activate a managed key', async () => {
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

    expect(response.status).toBe(200);
    await expect(readSessionByState(started.env, started.state)).resolves.toEqual(
      expect.objectContaining({
        status: 'consumed',
        pkce_code_verifier: null,
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
  env: TestBrokerEnv = createTestBrokerEnv(),
): Promise<StartedDiscordSession> {
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
              eligibility_checked_at,
              consumed_at
         FROM discord_oauth_sessions
        WHERE state_hash = ?`,
    )
    .get(stateHash) as DiscordSessionRow | undefined;

  if (!row) {
    throw new Error('Discord OAuth session row was not found');
  }

  return row;
}

async function readDiscordIdentity(
  env: TestBrokerEnv,
  discordUserRef: string,
): Promise<DiscordIdentityRow | null> {
  const row = env.__db
    .prepare(
      `SELECT discord_user_ref,
              entitlement_installation_id,
              status,
              updated_at
         FROM discord_identities
        WHERE discord_user_ref = ?`,
    )
    .get(discordUserRef) as DiscordIdentityRow | undefined;

  return row ?? null;
}

function readIssueSuccessEvents(env: TestBrokerEnv): IssueSuccessEventRow[] {
  return env.__db
    .prepare(
      `SELECT installation_id,
              managed_credential_ref,
              ip_hash,
              ip_prefix_hash,
              observed_at
         FROM broker_issue_success_events
        ORDER BY observed_at ASC`,
    )
    .all() as unknown as IssueSuccessEventRow[];
}

async function readEntitlement(
  env: TestBrokerEnv,
  installationId: string,
): Promise<Record<string, unknown> | null> {
  const row = env.__db
    .prepare(
      `SELECT installation_id,
              status,
              budget_usd,
              managed_credential_ref,
              issued_at,
              expires_at,
              verified_hardware_hash,
              verified_hardware_hash_salt_version,
              discord_user_ref,
              discord_issue_status,
              discord_issue_reserved_at,
              discord_issue_delivered_at
         FROM openrouter_entitlements
        WHERE installation_id = ?`,
    )
    .get(installationId) as Record<string, unknown> | undefined;
  return row ?? null;
}

async function readInstallation(
  env: TestBrokerEnv,
  installationId: string,
): Promise<Record<string, unknown> | null> {
  const row = env.__db
    .prepare(
      `SELECT installation_id,
              device_public_key,
              hardware_hash,
              hardware_hash_salt_version,
              app_version,
              challenge,
              challenge_expires_at,
              challenge_salt_version,
              created_at,
              last_seen_at
         FROM installations
        WHERE installation_id = ?`,
    )
    .get(installationId) as Record<string, unknown> | undefined;
  return row ?? null;
}

function insertInstallation(
  env: TestBrokerEnv,
  input: {
    installationId: string;
    devicePublicKey: string;
    hardwareHash: string | null;
    hardwareHashSaltVersion: number | null;
    appVersion?: string;
    challenge?: string | null;
    challengeExpiresAt?: string | null;
    challengeSaltVersion?: number | null;
  },
): void {
  env.__db
    .prepare(
      `INSERT INTO installations (
          installation_id,
          device_public_key,
          hardware_hash,
          hardware_hash_salt_version,
          app_version,
          challenge,
          challenge_expires_at,
          challenge_salt_version,
          created_at,
          last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      input.installationId,
      input.devicePublicKey,
      input.hardwareHash,
      input.hardwareHashSaltVersion,
      input.appVersion ?? APP_VERSION,
      input.challenge ?? null,
      input.challengeExpiresAt ?? null,
      input.challengeSaltVersion ?? null,
      NOW_ISO,
      NOW_ISO,
    );
}

function insertDiscordIdentity(
  env: TestBrokerEnv,
  input: {
    discordUserRef: string;
    installationId: string;
    status: 'issuing' | 'active' | 'failed' | 'cleanup_required';
  },
): void {
  env.__db
    .prepare(
      `INSERT INTO discord_identities (
          discord_user_ref,
          entitlement_installation_id,
          status,
          ref_secret_version,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, 1, ?, ?)`,
    )
    .run(
      input.discordUserRef,
      input.installationId,
      input.status,
      NOW_ISO,
      NOW_ISO,
    );
}

function countDiscordIdentities(env: TestBrokerEnv): number {
  const row = env.__db
    .prepare('SELECT COUNT(*) AS count FROM discord_identities')
    .get() as { count: number };
  return Number(row.count);
}

function countDiscordEntitlements(env: TestBrokerEnv, where = '1 = 1'): number {
  const row = env.__db
    .prepare(`SELECT COUNT(*) AS count FROM openrouter_entitlements WHERE ${where}`)
    .get() as { count: number };
  return Number(row.count);
}

async function envUpdateSessionStatus(
  env: TestBrokerEnv,
  state: string,
  status: 'canceled' | 'expired' | 'failed' | 'consumed',
): Promise<void> {
  env.__db
    .prepare(
      `UPDATE discord_oauth_sessions
          SET status = ?
        WHERE state_hash = ?`,
    )
    .run(status, await sha256Base64Url(state));
}

async function deriveExpectedDiscordUserRef(
  secret: string,
  discordUserId: string,
): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret.trim()),
    {
      name: 'HMAC',
      hash: 'SHA-256',
    },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    encoder.encode(`puripuly-heart:discord-user:v1\n${discordUserId.trim()}`),
  );
  return `ph-discord-user-v1_${encodeBase64Url(new Uint8Array(signature))}`;
}

function mockDiscordApi(options: {
  user?: Record<string, unknown>;
  tokenStatus?: number;
  userStatus?: number;
  openRouterMode?:
    | 'success'
    | 'create_failure'
    | 'guardrail_failure'
    | 'guardrail_failure_cleanup_failure';
  rawChildKey?: string;
  childKeyHash?: string;
  accessToken?: string;
  refreshToken?: string;
  guardrailFailureMessage?: string;
  cleanupFailureMessage?: string;
} = {}): {
  fetchMock: ReturnType<typeof vi.fn>;
  openRouterCreateCalls: Array<{ input: string | URL; init?: RequestInit }>;
  openRouterGuardrailCalls: Array<{ input: string | URL; init?: RequestInit }>;
  openRouterCleanupCalls: Array<{ input: string | URL; init?: RequestInit }>;
} {
  const user = options.user ?? {
    id: discordSnowflakeForAgeDays(31),
    verified: true,
  };
  const openRouterCreateCalls: Array<{ input: string | URL; init?: RequestInit }> = [];
  const openRouterGuardrailCalls: Array<{ input: string | URL; init?: RequestInit }> = [];
  const openRouterCleanupCalls: Array<{ input: string | URL; init?: RequestInit }> = [];
  const childKeyHash = options.childKeyHash ?? 'hash_discord_managed_child_test_1';
  const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';

    if (url === DISCORD_TOKEN_URL && method === 'POST') {
      if (options.tokenStatus !== undefined && options.tokenStatus >= 400) {
        return jsonResponse({ error: 'token exchange failed' }, options.tokenStatus);
      }

      return jsonResponse({
        access_token: options.accessToken ?? 'discord-access-token',
        token_type: 'Bearer',
        ...(options.refreshToken ? { refresh_token: options.refreshToken } : {}),
      });
    }

    if (url === DISCORD_USER_URL && method === 'GET') {
      if (options.userStatus !== undefined && options.userStatus >= 400) {
        return jsonResponse({ error: 'user fetch failed' }, options.userStatus);
      }

      return jsonResponse(user);
    }

    if (url === OPENROUTER_KEYS_URL && method === 'POST') {
      openRouterCreateCalls.push({ input, init });
      if (options.openRouterMode === 'create_failure') {
        return jsonResponse({ error: { message: 'create failed before key delivery' } }, 500);
      }

      const sequence = openRouterCreateCalls.length;
      return jsonResponse(
        {
          key: options.rawChildKey ?? `or-discord-managed-child-key-test-${sequence}`,
          data: {
            hash: options.childKeyHash ?? `hash_discord_managed_child_test_${sequence}`,
          },
        },
        201,
      );
    }

    if (url === OPENROUTER_GUARDRAIL_URL && method === 'POST') {
      openRouterGuardrailCalls.push({ input, init });
      if (
        options.openRouterMode === 'guardrail_failure' ||
        options.openRouterMode === 'guardrail_failure_cleanup_failure'
      ) {
        return jsonResponse(
          {
            error: {
              message: options.guardrailFailureMessage ?? 'guardrail assignment failed',
            },
          },
          500,
        );
      }

      return jsonResponse({ assigned_count: 1 });
    }

    if (url === `${OPENROUTER_KEYS_URL}/${childKeyHash}` && method === 'PATCH') {
      openRouterCleanupCalls.push({ input, init });
      if (options.openRouterMode === 'guardrail_failure_cleanup_failure') {
        return jsonResponse(
          {
            error: {
              message: options.cleanupFailureMessage ?? 'disable cleanup failed',
            },
          },
          500,
        );
      }

      return jsonResponse({ data: { hash: childKeyHash, disabled: true } });
    }

    if (url === `${OPENROUTER_KEYS_URL}/${childKeyHash}` && method === 'DELETE') {
      openRouterCleanupCalls.push({ input, init });
      if (options.openRouterMode === 'guardrail_failure_cleanup_failure') {
        return jsonResponse(
          {
            error: {
              message: options.cleanupFailureMessage ?? 'delete cleanup failed',
            },
          },
          500,
        );
      }

      return new Response(null, { status: 204 });
    }

    throw new Error(`unexpected Discord API request: ${method} ${url}`);
  });

  vi.stubGlobal('fetch', fetchMock as typeof fetch);
  return {
    fetchMock,
    openRouterCreateCalls,
    openRouterGuardrailCalls,
    openRouterCleanupCalls,
  };
}

function expectTokenExchange(
  fetchMock: ReturnType<typeof vi.fn>,
  expected: {
    code: string;
    redirectUri: string;
    codeVerifier: string | null;
  },
): void {
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

function expectCallOrder(
  fetchMock: ReturnType<typeof vi.fn>,
  expectedOrderedCalls: string[],
): void {
  const calls = fetchMock.mock.calls.map(([input, init]) => {
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    return `${method} ${String(input)}`;
  });
  const indexes = expectedOrderedCalls.map((expected) => calls.indexOf(expected));
  for (const index of indexes) {
    expect(index).toBeGreaterThanOrEqual(0);
  }
  expect(indexes).toEqual([...indexes].sort((left, right) => left - right));
}

function expectTextNotToContainSensitiveValues(text: string, values: string[]): void {
  for (const value of values) {
    expect(text).not.toContain(value);
  }
}

function stringifyConsoleCalls(spy: { mock: { calls: unknown[][] } }): string {
  return JSON.stringify(
    spy.mock.calls.map((call: unknown[]) =>
      call.map((value: unknown) => {
        if (value instanceof Error) {
          return {
            name: value.name,
            message: value.message,
          };
        }

        return value;
      }),
    ),
  );
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
