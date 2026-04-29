import type { Context } from 'hono';

import {
  checkEndpointRateLimit,
  getBrokerAbuseControlsConfig,
  recordRequestEvent,
  resolveClientIp,
} from './abuse-controls';
import { errorResponse as publicErrorResponse } from './broker-error';
import type { BrokerEnv } from './contract';
import {
  assertRedirectAllowed,
  buildDiscordAuthorizationUrl,
  deriveDiscordAccountCreatedAt,
  exchangeDiscordCode,
  fetchDiscordUser,
  generatePkcePair,
  parseDiscordRedirectAllowlist,
  type DiscordUserResponse,
} from './discord-oauth';
import { getFingerprintSaltConfig } from './fingerprint-salt';
import { normalizeManagedState } from './managed-state';
import { nonEmptyString, stringValue, validatePublicInput } from './public-input';
import type {
  BrokerPendingDiscordOAuthSessionsConfig,
  DiscordOAuthSessionRecord,
} from './persistence';
import {
  MANAGED_TRIAL_BUDGET_POLICY,
  TRIAL_PROVIDER_POLICY,
} from './trial-policy';

export const DISCORD_OAUTH_SESSION_TTL_SECONDS = 300;

const DISCORD_AUTH_START_ENDPOINT = 'POST /v1/auth/discord/start';
const DISCORD_OPENROUTER_ISSUE_METHOD = 'POST';
const DISCORD_OPENROUTER_ISSUE_PATH = '/v1/providers/openrouter/discord/issue';
const DISCORD_ISSUE_MAX_CLOCK_SKEW_SECONDS = 60;
const DISCORD_ISSUE_REASON = 'llm_start';
const DISCORD_ACCOUNT_MIN_AGE_MS = 30 * 24 * 60 * 60 * 1000;
const MANAGED_TRIAL_ALLOWED_MODEL_SET = new Set<string>(
  TRIAL_PROVIDER_POLICY.managedFreeTrial.models,
);
const MANAGED_TRIAL_ALLOWED_MODEL_LIST =
  TRIAL_PROVIDER_POLICY.managedFreeTrial.models.join(', ');
const STRICT_ISO_8601_TIMESTAMP =
  /^(?<year>\d{4})-(?<month>0[1-9]|1[0-2])-(?<day>0[1-9]|[12]\d|3[01])T(?<hour>[01]\d|2[0-3]):(?<minute>[0-5]\d):(?<second>[0-5]\d)(?:\.(?<millisecond>\d{3}))?(?:(?<utc>Z)|(?<offsetSign>[+-])(?<offsetHour>[01]\d|2[0-3]):(?<offsetMinute>[0-5]\d))$/u;
const textEncoder = new TextEncoder();

interface DiscordAuthStartRequestBody {
  installation_id?: unknown;
  device_public_key?: unknown;
  redirect_uri?: unknown;
  app_version?: unknown;
}

interface DiscordOpenRouterIssueRequestBody {
  code?: unknown;
  state?: unknown;
  installation_id?: unknown;
  device_public_key?: unknown;
  redirect_uri?: unknown;
  hardware_hash?: unknown;
  hardware_hash_salt_version?: unknown;
  app_version?: unknown;
  reason?: unknown;
  budget_usd?: unknown;
  model?: unknown;
  issue_nonce?: unknown;
  signed_at?: unknown;
  signature_alg?: unknown;
  signature?: unknown;
}

interface DiscordOpenRouterIssueInput {
  code: string;
  state: string;
  installationId: string;
  devicePublicKey: string;
  redirectUri: string;
  hardwareHash: string;
  hardwareHashSaltVersion: number;
  appVersion: string;
  reason: string;
  budgetUsd: number;
  model: string;
  issueNonce: string;
  signedAt: string;
  signatureAlg: 'ed25519';
  signature: string;
}

interface DiscordEligibilityDecision {
  ok: boolean;
  discordAccountCreatedAt: string | null;
  discordEmailVerified: 0 | 1 | null;
  subcode?: 'discord_email_unverified' | 'discord_account_too_new' | 'discord_invalid_snowflake';
  message?: string;
}

export async function handleDiscordAuthStart(
  c: Context<BrokerEnv>,
): Promise<Response> {
  const body = await readJsonBody<DiscordAuthStartRequestBody>(c);
  if (!body.ok) {
    return invalidRequestBodyResponse(c, body.reason);
  }

  const installationId = stringValue(body.value.installation_id);
  const devicePublicKey = nonEmptyString(body.value.device_public_key);
  const redirectUri = stringValue(body.value.redirect_uri);
  const appVersion = stringValue(body.value.app_version);

  if (!installationId || !devicePublicKey || !redirectUri || !appVersion) {
    return invalidRequestResponse(
      c,
      'installation_id, device_public_key, redirect_uri, and app_version are required',
    );
  }

  const installationIdBoundsError = validatePublicInput(
    'installation_id',
    installationId,
  );
  if (installationIdBoundsError) {
    return invalidRequestResponse(c, installationIdBoundsError);
  }

  const appVersionBoundsError = validatePublicInput('app_version', appVersion);
  if (appVersionBoundsError) {
    return invalidRequestResponse(c, appVersionBoundsError);
  }

  if (!isBase64Url(devicePublicKey, 32)) {
    return invalidRequestResponse(
      c,
      'device_public_key must be base64url-encoded Ed25519 public key bytes',
    );
  }

  let redirectAllowlist: string[];
  try {
    redirectAllowlist = parseDiscordRedirectAllowlist(
      c.env.DISCORD_REDIRECT_URI_ALLOWLIST,
    );
    assertRedirectAllowed(redirectUri, redirectAllowlist);
  } catch (error) {
    return invalidRequestResponse(
      c,
      error instanceof Error ? error.message : 'Discord redirect URI is invalid',
    );
  }

  const now = new Date();
  const requestContext = {
    endpoint: DISCORD_AUTH_START_ENDPOINT,
    now,
    ip: resolveClientIp(c),
    installationId,
    hardwareHash: null,
  };

  await recordRequestEvent(c.env.BROKER_DB, requestContext);

  const rateLimitDecision = await checkEndpointRateLimit(
    c.env.BROKER_DB,
    requestContext,
  );
  if (rateLimitDecision) {
    return publicErrorResponse(c, rateLimitDecision.status, {
      code: rateLimitDecision.code,
      class: rateLimitDecision.class,
      subcode: rateLimitDecision.subcode,
      retryAfterMs: rateLimitDecision.retryAfterMs,
      message: rateLimitDecision.message,
      entitlement: null,
    });
  }

  const controls = await getBrokerAbuseControlsConfig(c.env.BROKER_DB);
  const pendingControls = controls.pendingDiscordOAuthSessions;
  const pendingLimitDecision = await checkPendingDiscordOAuthIpLimit(
    c.env.BROKER_DB,
    requestContext,
    pendingControls,
  );
  if (pendingLimitDecision) {
    return pendingLimitDecision(c);
  }

  const fingerprintSalt = await getFingerprintSaltConfig(c.env.BROKER_DB);
  const pkce = await generatePkcePair();
  const state = randomBase64Url(32);
  const issueNonce = randomBase64Url(32);
  const expiresAt = new Date(
    now.getTime() + DISCORD_OAUTH_SESSION_TTL_SECONDS * 1000,
  ).toISOString();

  const insertSucceeded = await insertPendingDiscordOAuthSession(c.env.BROKER_DB, {
    stateHash: await sha256Base64Url(state),
    installationId,
    devicePublicKey,
    redirectUri,
    pkceCodeVerifier: pkce.codeVerifier,
    issueNonceHash: await sha256Base64Url(issueNonce),
    fingerprintSaltVersion: fingerprintSalt.current.version,
    nowIso: now.toISOString(),
    expiresAt,
    maxPendingPerInstallation: pendingControls.maxPerInstallation,
  });

  if (!insertSucceeded) {
    return pendingInstallationLimitResponse(c, pendingControls);
  }

  return c.json({
    authorization_url: buildDiscordAuthorizationUrl({
      clientId: c.env.DISCORD_CLIENT_ID,
      redirectUri,
      state,
      codeChallenge: pkce.codeChallenge,
    }),
    redirect_uri: redirectUri,
    oauth_session_expires_at: expiresAt,
    issue_nonce: issueNonce,
    fingerprint_salt: {
      version: fingerprintSalt.current.version,
      salt: fingerprintSalt.current.salt,
    },
    fingerprint_salt_version: fingerprintSalt.current.version,
  });
}

export async function handleDiscordOpenRouterIssue(
  c: Context<BrokerEnv>,
): Promise<Response> {
  const body = await readJsonBody<DiscordOpenRouterIssueRequestBody>(c);
  if (!body.ok) {
    return invalidRequestBodyResponse(c, body.reason);
  }

  const input = validateDiscordIssuePublicInput(c, body.value);
  if (!input.ok) {
    return input.response;
  }

  const now = new Date();
  const nowIso = now.toISOString();
  const stateHash = await sha256Base64Url(input.value.state);
  const codeHash = await sha256Base64Url(input.value.code);
  const issueNonceHash = await sha256Base64Url(input.value.issueNonce);
  const session = await getDiscordOAuthSession(c.env.BROKER_DB, stateHash);

  if (!session) {
    return discordStateUnknownResponse(c);
  }

  const sessionGateResponse = validateDiscordSessionGate(c, {
    session,
    input: input.value,
    issueNonceHash,
    now,
  });
  if (sessionGateResponse) {
    return sessionGateResponse;
  }

  const signedAtDate = parseIsoDate(input.value.signedAt);
  if (!signedAtDate) {
    return invalidRequestResponse(c, 'signed_at must be a valid ISO-8601 timestamp');
  }

  if (
    Math.abs(signedAtDate.getTime() - now.getTime()) >
    DISCORD_ISSUE_MAX_CLOCK_SKEW_SECONDS * 1000
  ) {
    return discordSignatureSkewResponse(c);
  }

  const signatureIsValid = await verifyEd25519Signature({
    devicePublicKey: input.value.devicePublicKey,
    signature: input.value.signature,
    payload: buildCanonicalDiscordIssuePayload({
      input: input.value,
      codeHash,
    }),
  });
  if (!signatureIsValid) {
    return discordSignatureMismatchResponse(c);
  }

  const claimed = await claimDiscordOAuthSession(c.env.BROKER_DB, {
    stateHash,
    nowIso,
  });
  if (!claimed) {
    return discordSessionAlreadyProcessingResponse(c);
  }

  let discordUser: DiscordUserResponse;
  try {
    const tokenResponse = await exchangeDiscordCode({
      clientId: c.env.DISCORD_CLIENT_ID,
      clientSecret: c.env.DISCORD_CLIENT_SECRET,
      code: input.value.code,
      redirectUri: session.redirect_uri,
      codeVerifier: session.pkce_code_verifier!,
    });
    discordUser = await fetchDiscordUser({
      accessToken: tokenResponse.access_token,
    });
  } catch {
    await failDiscordOAuthSession(c.env.BROKER_DB, {
      stateHash,
      nowIso,
      discordEmailVerified: null,
      discordAccountCreatedAt: null,
    });
    return discordOAuthFailedResponse(c);
  }

  const eligibility = assertDiscordEligibility(discordUser, now);
  if (!eligibility.ok) {
    await failDiscordOAuthSession(c.env.BROKER_DB, {
      stateHash,
      nowIso,
      discordEmailVerified: eligibility.discordEmailVerified,
      discordAccountCreatedAt: eligibility.discordAccountCreatedAt,
    });
    return discordEligibilityErrorResponse(
      c,
      eligibility.subcode ?? 'discord_invalid_snowflake',
      eligibility.message ?? 'Discord account identity is invalid',
    );
  }

  await markDiscordOAuthSessionEligible(c.env.BROKER_DB, {
    stateHash,
    nowIso,
    discordEmailVerified: eligibility.discordEmailVerified,
    discordAccountCreatedAt: eligibility.discordAccountCreatedAt,
  });

  return discordActivationPlaceholderResponse(c);
}

function validateDiscordIssuePublicInput(
  c: Context<BrokerEnv>,
  body: DiscordOpenRouterIssueRequestBody,
):
  | { ok: true; value: DiscordOpenRouterIssueInput }
  | { ok: false; response: Response } {
  const code = nonEmptyString(body.code);
  const state = nonEmptyString(body.state);
  const installationId = stringValue(body.installation_id);
  const devicePublicKey = nonEmptyString(body.device_public_key);
  const redirectUri = nonEmptyString(body.redirect_uri);
  const hardwareHash = nonEmptyString(body.hardware_hash);
  const hardwareHashSaltVersion =
    typeof body.hardware_hash_salt_version === 'number'
      ? body.hardware_hash_salt_version
      : null;
  const appVersion = stringValue(body.app_version);
  const reason = nonEmptyString(body.reason);
  const budgetUsd = typeof body.budget_usd === 'number' ? body.budget_usd : null;
  const model = nonEmptyString(body.model);
  const issueNonce = nonEmptyString(body.issue_nonce);
  const signedAt = nonEmptyString(body.signed_at);
  const signatureAlg = stringValue(body.signature_alg);
  const signature = nonEmptyString(body.signature);

  if (
    !code ||
    !state ||
    !installationId ||
    !devicePublicKey ||
    !redirectUri ||
    !hardwareHash ||
    hardwareHashSaltVersion === null ||
    !appVersion ||
    !reason ||
    budgetUsd === null ||
    !model ||
    !issueNonce ||
    !signedAt ||
    !signatureAlg ||
    !signature
  ) {
    return {
      ok: false,
      response: invalidRequestResponse(
        c,
        'code, state, installation_id, device_public_key, redirect_uri, hardware_hash, hardware_hash_salt_version, app_version, reason, budget_usd, model, issue_nonce, signed_at, signature_alg, and signature are required',
      ),
    };
  }

  const installationIdBoundsError = validatePublicInput(
    'installation_id',
    installationId,
  );
  if (installationIdBoundsError) {
    return { ok: false, response: invalidRequestResponse(c, installationIdBoundsError) };
  }

  const hardwareHashBoundsError = validatePublicInput('hardware_hash', hardwareHash);
  if (hardwareHashBoundsError) {
    return { ok: false, response: invalidRequestResponse(c, hardwareHashBoundsError) };
  }

  const appVersionBoundsError = validatePublicInput('app_version', appVersion);
  if (appVersionBoundsError) {
    return { ok: false, response: invalidRequestResponse(c, appVersionBoundsError) };
  }

  for (const [field, value] of [
    ['code', code],
    ['state', state],
    ['redirect_uri', redirectUri],
    ['issue_nonce', issueNonce],
    ['signed_at', signedAt],
  ] as const) {
    const fieldError = validateDiscordIssueTextField(field, value);
    if (fieldError) {
      return { ok: false, response: invalidRequestResponse(c, fieldError) };
    }
  }

  if (!isBase64Url(devicePublicKey, 32) || !isBase64Url(signature, 64)) {
    return {
      ok: false,
      response: invalidRequestResponse(
        c,
        'device_public_key and signature must be base64url-encoded Ed25519 contract values',
      ),
    };
  }

  if (!Number.isSafeInteger(hardwareHashSaltVersion) || hardwareHashSaltVersion < 0) {
    return {
      ok: false,
      response: invalidRequestResponse(
        c,
        'hardware_hash_salt_version must be a non-negative integer',
      ),
    };
  }

  if (signatureAlg !== 'ed25519') {
    return {
      ok: false,
      response: invalidRequestResponse(c, 'signature_alg must be ed25519'),
    };
  }

  if (reason !== DISCORD_ISSUE_REASON) {
    return { ok: false, response: invalidRequestResponse(c, 'reason must be llm_start') };
  }

  if (budgetUsd !== MANAGED_TRIAL_BUDGET_POLICY.hardLimit) {
    return {
      ok: false,
      response: invalidRequestResponse(
        c,
        `budget_usd must equal ${MANAGED_TRIAL_BUDGET_POLICY.hardLimit}`,
      ),
    };
  }

  if (!MANAGED_TRIAL_ALLOWED_MODEL_SET.has(model)) {
    return {
      ok: false,
      response: invalidRequestResponse(
        c,
        `model must be one of ${MANAGED_TRIAL_ALLOWED_MODEL_LIST}`,
      ),
    };
  }

  return {
    ok: true,
    value: {
      code,
      state,
      installationId,
      devicePublicKey,
      redirectUri,
      hardwareHash,
      hardwareHashSaltVersion,
      appVersion,
      reason,
      budgetUsd,
      model,
      issueNonce,
      signedAt,
      signatureAlg: 'ed25519',
      signature,
    },
  };
}

function validateDiscordIssueTextField(field: string, value: string): string | null {
  if (value.trim().length === 0) {
    return `${field} must not be blank or whitespace-only`;
  }

  if (Array.from(value).length > 2048) {
    return `${field} must be at most 2048 characters`;
  }

  if (/[\p{Cc}\r\n\u0085\u2028\u2029]/u.test(value)) {
    return `${field} must not contain control characters or newlines`;
  }

  return null;
}

function validateDiscordSessionGate(
  c: Context<BrokerEnv>,
  input: {
    session: DiscordOAuthSessionRecord;
    input: DiscordOpenRouterIssueInput;
    issueNonceHash: string;
    now: Date;
  },
): Response | null {
  const expiresAt = parseIsoDate(input.session.expires_at);
  if (
    input.session.status === 'expired' ||
    !expiresAt ||
    expiresAt.getTime() < input.now.getTime()
  ) {
    return discordSessionExpiredResponse(c);
  }

  if (input.session.status === 'processing') {
    return discordSessionAlreadyProcessingResponse(c);
  }

  if (input.session.status !== 'pending') {
    return discordSessionTerminalResponse(c, input.session.status);
  }

  if (!input.session.pkce_code_verifier) {
    return discordSessionTerminalResponse(c, 'failed');
  }

  if (
    input.session.installation_id !== input.input.installationId ||
    input.session.device_public_key !== input.input.devicePublicKey ||
    input.session.redirect_uri !== input.input.redirectUri ||
    input.session.issue_nonce_hash !== input.issueNonceHash
  ) {
    return discordSessionBindingMismatchResponse(c);
  }

  if (
    Number(input.session.fingerprint_salt_version) !==
    input.input.hardwareHashSaltVersion
  ) {
    return discordHardwareSaltMismatchResponse(c);
  }

  return null;
}

async function getDiscordOAuthSession(
  db: D1Database,
  stateHash: string,
): Promise<DiscordOAuthSessionRecord | null> {
  return db
    .prepare(
      `SELECT state_hash,
              installation_id,
              device_public_key,
              redirect_uri,
              pkce_code_verifier,
              issue_nonce_hash,
              fingerprint_salt_version,
              discord_user_ref,
              discord_email_verified,
              discord_account_created_at,
              eligibility_checked_at,
              status,
              created_at,
              expires_at,
              processing_started_at,
              consumed_at
         FROM discord_oauth_sessions
        WHERE state_hash = ?`,
    )
    .bind(stateHash)
    .first<DiscordOAuthSessionRecord>();
}

async function claimDiscordOAuthSession(
  db: D1Database,
  input: {
    stateHash: string;
    nowIso: string;
  },
): Promise<boolean> {
  const result = await db
    .prepare(
      `UPDATE discord_oauth_sessions
          SET status = 'processing',
              processing_started_at = ?
        WHERE state_hash = ?
          AND status = 'pending'`,
    )
    .bind(input.nowIso, input.stateHash)
    .run();

  return Number(result.meta.changes ?? 0) === 1;
}

async function failDiscordOAuthSession(
  db: D1Database,
  input: {
    stateHash: string;
    nowIso: string;
    discordEmailVerified: 0 | 1 | null;
    discordAccountCreatedAt: string | null;
  },
): Promise<void> {
  await db
    .prepare(
      `UPDATE discord_oauth_sessions
          SET status = 'failed',
              pkce_code_verifier = NULL,
              discord_email_verified = ?,
              discord_account_created_at = ?,
              eligibility_checked_at = ?
        WHERE state_hash = ?`,
    )
    .bind(
      input.discordEmailVerified,
      input.discordAccountCreatedAt,
      input.nowIso,
      input.stateHash,
    )
    .run();
}

async function markDiscordOAuthSessionEligible(
  db: D1Database,
  input: {
    stateHash: string;
    nowIso: string;
    discordEmailVerified: 0 | 1 | null;
    discordAccountCreatedAt: string | null;
  },
): Promise<void> {
  await db
    .prepare(
      `UPDATE discord_oauth_sessions
          SET pkce_code_verifier = NULL,
              discord_email_verified = ?,
              discord_account_created_at = ?,
              eligibility_checked_at = ?
        WHERE state_hash = ?
          AND status = 'processing'`,
    )
    .bind(
      input.discordEmailVerified,
      input.discordAccountCreatedAt,
      input.nowIso,
      input.stateHash,
    )
    .run();
}

function assertDiscordEligibility(
  user: DiscordUserResponse,
  now: Date,
): DiscordEligibilityDecision {
  if (user.verified !== true) {
    return {
      ok: false,
      discordAccountCreatedAt: null,
      discordEmailVerified: user.verified === false ? 0 : null,
      subcode: 'discord_email_unverified',
      message: 'Discord email verification is required',
    };
  }

  let discordAccountCreatedAt: string;
  try {
    discordAccountCreatedAt = deriveDiscordAccountCreatedAt(user.id);
  } catch {
    return {
      ok: false,
      discordAccountCreatedAt: null,
      discordEmailVerified: 1,
      subcode: 'discord_invalid_snowflake',
      message: 'Discord account identity is invalid',
    };
  }

  const createdAt = parseIsoDate(discordAccountCreatedAt);
  if (!createdAt) {
    return {
      ok: false,
      discordAccountCreatedAt: null,
      discordEmailVerified: 1,
      subcode: 'discord_invalid_snowflake',
      message: 'Discord account identity is invalid',
    };
  }

  if (now.getTime() - createdAt.getTime() < DISCORD_ACCOUNT_MIN_AGE_MS) {
    return {
      ok: false,
      discordAccountCreatedAt,
      discordEmailVerified: 1,
      subcode: 'discord_account_too_new',
      message: 'Discord account must be at least 30 days old',
    };
  }

  return {
    ok: true,
    discordAccountCreatedAt,
    discordEmailVerified: 1,
  };
}

function buildCanonicalDiscordIssuePayload(input: {
  input: DiscordOpenRouterIssueInput;
  codeHash: string;
}): Uint8Array {
  return textEncoder.encode(
    [
      DISCORD_OPENROUTER_ISSUE_METHOD,
      DISCORD_OPENROUTER_ISSUE_PATH,
      input.input.installationId,
      input.input.devicePublicKey,
      input.input.state,
      input.codeHash,
      input.input.redirectUri,
      input.input.hardwareHash,
      String(input.input.hardwareHashSaltVersion),
      input.input.appVersion,
      input.input.reason,
      String(input.input.budgetUsd),
      input.input.model,
      input.input.issueNonce,
      input.input.signedAt,
    ].join('\n'),
  );
}

async function verifyEd25519Signature(input: {
  devicePublicKey: string;
  signature: string;
  payload: Uint8Array;
}): Promise<boolean> {
  try {
    const publicKey = await crypto.subtle.importKey(
      'raw',
      toArrayBuffer(decodeBase64Url(input.devicePublicKey)),
      { name: 'Ed25519' },
      false,
      ['verify'],
    );

    return crypto.subtle.verify(
      { name: 'Ed25519' },
      publicKey,
      toArrayBuffer(decodeBase64Url(input.signature)),
      toArrayBuffer(input.payload),
    );
  } catch {
    return false;
  }
}

function parseIsoDate(value: string): Date | null {
  const match = STRICT_ISO_8601_TIMESTAMP.exec(value);
  if (!match?.groups) {
    return null;
  }

  const year = Number(match.groups.year);
  const month = Number(match.groups.month);
  const day = Number(match.groups.day);
  const hour = Number(match.groups.hour);
  const minute = Number(match.groups.minute);
  const second = Number(match.groups.second);
  const millisecond = Number(match.groups.millisecond ?? '0');
  const offsetMinutes = match.groups.utc
    ? 0
    : (match.groups.offsetSign === '-' ? -1 : 1) *
      (Number(match.groups.offsetHour) * 60 + Number(match.groups.offsetMinute));

  const timestamp =
    Date.UTC(year, month - 1, day, hour, minute, second, millisecond) -
    offsetMinutes * 60_000;
  const reconstructedLocalTime = new Date(timestamp + offsetMinutes * 60_000);

  if (
    reconstructedLocalTime.getUTCFullYear() !== year ||
    reconstructedLocalTime.getUTCMonth() + 1 !== month ||
    reconstructedLocalTime.getUTCDate() !== day ||
    reconstructedLocalTime.getUTCHours() !== hour ||
    reconstructedLocalTime.getUTCMinutes() !== minute ||
    reconstructedLocalTime.getUTCSeconds() !== second ||
    reconstructedLocalTime.getUTCMilliseconds() !== millisecond
  ) {
    return null;
  }

  return new Date(timestamp);
}

function discordStateUnknownResponse(c: Context<BrokerEnv>): Response {
  return publicErrorResponse(c, 410, {
    code: 'challenge_expired',
    class: 'retryable',
    subcode: 'discord_oauth_state_unknown',
    retryAfterMs: 0,
    message: 'Discord OAuth session was not found or expired',
    entitlement: null,
  });
}

function discordSessionExpiredResponse(c: Context<BrokerEnv>): Response {
  return publicErrorResponse(c, 410, {
    code: 'challenge_expired',
    class: 'retryable',
    subcode: 'discord_oauth_session_expired',
    retryAfterMs: 0,
    message: 'Discord OAuth session has expired and must be restarted',
    entitlement: null,
  });
}

function discordOAuthFailedResponse(c: Context<BrokerEnv>): Response {
  return publicErrorResponse(c, 410, {
    code: 'challenge_expired',
    class: 'retryable',
    subcode: 'discord_oauth_failed',
    retryAfterMs: 0,
    message: 'Discord OAuth verification failed; restart Discord OAuth onboarding',
    entitlement: null,
  });
}

function discordSessionTerminalResponse(
  c: Context<BrokerEnv>,
  status: string,
): Response {
  return publicErrorResponse(c, 409, {
    code: 'challenge_invalid',
    class: 'security_fail',
    subcode: `discord_oauth_session_${status}`,
    message: 'Discord OAuth session can no longer issue a managed key',
    entitlement: null,
  });
}

function discordSessionAlreadyProcessingResponse(c: Context<BrokerEnv>): Response {
  return publicErrorResponse(c, 409, {
    code: 'trial_unavailable',
    class: 'retryable',
    subcode: 'discord_oauth_session_processing',
    message: 'Discord OAuth session is already processing',
    entitlement: null,
  });
}

function discordSessionBindingMismatchResponse(c: Context<BrokerEnv>): Response {
  return publicErrorResponse(c, 409, {
    code: 'trial_not_eligible',
    class: 'security_fail',
    subcode: 'discord_session_binding_mismatch',
    message: 'Discord OAuth session binding does not match the issue request',
    entitlement: null,
  });
}

function discordHardwareSaltMismatchResponse(c: Context<BrokerEnv>): Response {
  return publicErrorResponse(c, 409, {
    code: 'trial_not_eligible',
    class: 'terminal',
    subcode: 'hardware_salt_mismatch',
    message: 'hardware_hash_salt_version does not match the pending Discord OAuth session',
    entitlement: null,
  });
}

function discordSignatureSkewResponse(c: Context<BrokerEnv>): Response {
  return publicErrorResponse(c, 401, {
    code: 'challenge_invalid',
    class: 'security_fail',
    subcode: 'timestamp_skew',
    message: 'signed_at must be within ±60 seconds of broker time',
    entitlement: null,
  });
}

function discordSignatureMismatchResponse(c: Context<BrokerEnv>): Response {
  return publicErrorResponse(c, 401, {
    code: 'challenge_invalid',
    class: 'security_fail',
    subcode: 'signature_mismatch',
    message: 'signature verification failed for the registered device_public_key',
    entitlement: null,
  });
}

function discordEligibilityErrorResponse(
  c: Context<BrokerEnv>,
  subcode:
    | 'discord_email_unverified'
    | 'discord_account_too_new'
    | 'discord_invalid_snowflake',
  message: string,
): Response {
  return publicErrorResponse(c, 409, {
    code: 'trial_not_eligible',
    class: 'terminal',
    subcode,
    message,
    entitlement: null,
  });
}

function discordActivationPlaceholderResponse(c: Context<BrokerEnv>): Response {
  return c.json(
    {
      error: {
        code: 'trial_unavailable',
        class: 'retryable',
        subcode: 'not_implemented',
        retry_after_ms: null,
        message: 'Discord OpenRouter activation is not implemented yet',
      },
      ...normalizeManagedState(null),
    },
    501,
  );
}

async function checkPendingDiscordOAuthIpLimit(
  db: D1Database,
  context: {
    endpoint: string;
    now: Date;
    ip: string | null;
    installationId: string;
  },
  pendingControls: BrokerPendingDiscordOAuthSessionsConfig,
): Promise<((c: Context<BrokerEnv>) => Response) | null> {
  if (!context.ip) {
    return null;
  }

  const windowStart = new Date(
    context.now.getTime() - pendingControls.windowMinutes * 60_000,
  ).toISOString();
  const ipStartCount = await db
    .prepare(
      `SELECT COUNT(*) AS count
         FROM broker_request_events
        WHERE endpoint = ?
          AND ip = ?
          AND observed_at >= ?`,
    )
    .bind(context.endpoint, context.ip, windowStart)
    .first<{ count: number }>();

  if (Number(ipStartCount?.count ?? 0) > pendingControls.maxPerIp) {
    return (c: Context<BrokerEnv>) =>
      publicErrorResponse(c, 429, {
        code: 'rate_limited',
        class: 'retryable',
        subcode: 'pending_discord_oauth_ip_limit',
        retryAfterMs: pendingControls.windowMinutes * 60_000,
        message: 'pending Discord OAuth session limit exceeded for client IP',
        entitlement: null,
      });
  }

  return null;
}

async function insertPendingDiscordOAuthSession(
  db: D1Database,
  input: {
    stateHash: string;
    installationId: string;
    devicePublicKey: string;
    redirectUri: string;
    pkceCodeVerifier: string;
    issueNonceHash: string;
    fingerprintSaltVersion: number;
    nowIso: string;
    expiresAt: string;
    maxPendingPerInstallation: number;
  },
): Promise<boolean> {
  const result = await db
    .prepare(
      `INSERT INTO discord_oauth_sessions (
          state_hash,
          installation_id,
          device_public_key,
          redirect_uri,
          pkce_code_verifier,
          issue_nonce_hash,
          fingerprint_salt_version,
          status,
          created_at,
          expires_at
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?
         WHERE (
           SELECT COUNT(*)
             FROM discord_oauth_sessions
            WHERE installation_id = ?
              AND status = 'pending'
              AND expires_at > ?
         ) < ?`,
    )
    .bind(
      input.stateHash,
      input.installationId,
      input.devicePublicKey,
      input.redirectUri,
      input.pkceCodeVerifier,
      input.issueNonceHash,
      input.fingerprintSaltVersion,
      input.nowIso,
      input.expiresAt,
      input.installationId,
      input.nowIso,
      input.maxPendingPerInstallation,
    )
    .run();

  return Number(result.meta.changes ?? 0) === 1;
}

function pendingInstallationLimitResponse(
  c: Context<BrokerEnv>,
  pendingControls: BrokerPendingDiscordOAuthSessionsConfig,
): Response {
  return publicErrorResponse(c, 429, {
    code: 'rate_limited',
    class: 'retryable',
    subcode: 'pending_discord_oauth_installation_limit',
    retryAfterMs: pendingControls.windowMinutes * 60_000,
    message: 'pending Discord OAuth session limit exceeded for installation_id',
    entitlement: null,
  });
}

async function readJsonBody<T>(
  c: Context<BrokerEnv>,
): Promise<
  | { ok: true; value: T }
  | { ok: false; reason: 'invalid_json' | 'not_object' }
> {
  try {
    const value = await c.req.json();
    if (!isJsonObject(value)) {
      return {
        ok: false,
        reason: 'not_object',
      };
    }

    return {
      ok: true,
      value: value as T,
    };
  } catch {
    return {
      ok: false,
      reason: 'invalid_json',
    };
  }
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function invalidRequestBodyResponse(
  c: Context<BrokerEnv>,
  reason: 'invalid_json' | 'not_object',
): Response {
  return invalidRequestResponse(
    c,
    reason === 'invalid_json'
      ? 'request body must be valid JSON'
      : 'request body must be a JSON object',
  );
}

function invalidRequestResponse(
  c: Context<BrokerEnv>,
  message: string,
): Response {
  return publicErrorResponse(c, 400, {
    code: 'invalid_request',
    class: 'terminal',
    message,
    entitlement: null,
  });
}

async function sha256Base64Url(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', textEncoder.encode(value));
  return encodeBase64Url(new Uint8Array(digest));
}

function randomBase64Url(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return encodeBase64Url(bytes);
}

function isBase64Url(value: string, byteLength?: number): boolean {
  if (!/^[A-Za-z0-9_-]+$/u.test(value)) {
    return false;
  }

  try {
    const decoded = decodeBase64Url(value);
    return byteLength === undefined || decoded.length === byteLength;
  } catch {
    return false;
  }
}

function decodeBase64Url(value: string): Uint8Array {
  const padding = (4 - (value.length % 4 || 4)) % 4;
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat(padding);
  const binary = atob(normalized);
  const bytes = new Uint8Array(binary.length);

  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }

  return bytes;
}

function encodeBase64Url(bytes: Uint8Array): string {
  const binary = Array.from(bytes, (value) => String.fromCharCode(value)).join('');
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/u, '');
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
}
