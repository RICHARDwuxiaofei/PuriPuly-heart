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
  generatePkcePair,
  parseDiscordRedirectAllowlist,
} from './discord-oauth';
import { getFingerprintSaltConfig } from './fingerprint-salt';
import { normalizeManagedState } from './managed-state';
import { nonEmptyString, stringValue, validatePublicInput } from './public-input';
import type { BrokerPendingDiscordOAuthSessionsConfig } from './persistence';

export const DISCORD_OAUTH_SESSION_TTL_SECONDS = 300;

const DISCORD_AUTH_START_ENDPOINT = 'POST /v1/auth/discord/start';
const textEncoder = new TextEncoder();

interface DiscordAuthStartRequestBody {
  installation_id?: unknown;
  device_public_key?: unknown;
  redirect_uri?: unknown;
  app_version?: unknown;
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

export function handleDiscordOpenRouterIssue(c: Context<BrokerEnv>): Response {
  return c.json(
    {
      error: {
        code: 'trial_unavailable',
        class: 'retryable',
        subcode: 'not_implemented',
        retry_after_ms: null,
        message: 'Discord OpenRouter issue is not implemented yet',
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
