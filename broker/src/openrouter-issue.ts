import type { Context } from 'hono';

import type {
  InstallationRecord,
  OpenRouterEntitlementRecord,
} from './persistence';
import type { BrokerEnv } from './contract';
import {
  checkEndpointRateLimit,
  checkVelocityCapHook,
  matchSubjectHook,
  recordRequestEvent,
  resolveClientIp,
} from './abuse-controls';
import { errorResponse as publicErrorResponse } from './broker-error';
import {
  MANAGED_TRIAL_BUDGET_POLICY,
  MANAGED_TRIAL_POLICY,
  TRIAL_PROVIDER_POLICY,
} from './trial-policy';

const ISSUE_MAX_CLOCK_SKEW_SECONDS = 60;
const ISSUE_REQUEST_REASON = 'llm_start';
const ISSUE_SIGNATURE_PAYLOAD_FIELDS = [
  'installation_id',
  'device_public_key',
  'release_token',
  'reason',
  'budget_usd',
  'model',
  'signed_at',
] as const;

const STRICT_ISO_8601_TIMESTAMP =
  /^(?<year>\d{4})-(?<month>0[1-9]|1[0-2])-(?<day>0[1-9]|[12]\d|3[01])T(?<hour>[01]\d|2[0-3]):(?<minute>[0-5]\d):(?<second>[0-5]\d)(?:\.(?<millisecond>\d{3}))?(?:(?<utc>Z)|(?<offsetSign>[+-])(?<offsetHour>[01]\d|2[0-3]):(?<offsetMinute>[0-5]\d))$/u;

interface IssueRequestBody {
  installation_id?: unknown;
  device_public_key?: unknown;
  release_token?: unknown;
  reason?: unknown;
  budget_usd?: unknown;
  model?: unknown;
  signed_at?: unknown;
  signature?: unknown;
}

export async function handleOpenRouterIssue(
  c: Context<BrokerEnv>,
): Promise<Response> {
  const body = await readJsonBody<IssueRequestBody>(c);
  if (!body.ok) {
    return invalidRequestBodyResponse(c, body.reason);
  }

  const installationId = nonEmptyString(body.value.installation_id);
  const devicePublicKey = nonEmptyString(body.value.device_public_key);
  const releaseToken = nonEmptyString(body.value.release_token);
  const reason = nonEmptyString(body.value.reason);
  const model = nonEmptyString(body.value.model);
  const signedAt = nonEmptyString(body.value.signed_at);
  const signature = nonEmptyString(body.value.signature);
  const budgetUsd = typeof body.value.budget_usd === 'number' ? body.value.budget_usd : null;

  if (
    !installationId ||
    !devicePublicKey ||
    !releaseToken ||
    !reason ||
    budgetUsd === null ||
    !model ||
    !signedAt ||
    !signature
  ) {
    return errorResponse(
      c,
      400,
      'invalid_request',
      'installation_id, device_public_key, release_token, reason, budget_usd, model, signed_at, and signature are required',
    );
  }

  if (!isBase64Url(devicePublicKey, 32) || !isBase64Url(releaseToken, 32) || !isBase64Url(signature, 64)) {
    return errorResponse(
      c,
      400,
      'invalid_request',
      'device_public_key, release_token, and signature must be base64url-encoded contract values',
    );
  }

  if (reason !== ISSUE_REQUEST_REASON) {
    return errorResponse(c, 400, 'invalid_request', 'reason must be llm_start');
  }

  if (budgetUsd !== MANAGED_TRIAL_BUDGET_POLICY.hardLimit) {
    return errorResponse(
      c,
      400,
      'invalid_request',
      `budget_usd must equal ${MANAGED_TRIAL_BUDGET_POLICY.hardLimit}`,
    );
  }

  if (model !== TRIAL_PROVIDER_POLICY.managedFreeTrial.model) {
    return errorResponse(
      c,
      400,
      'invalid_request',
      `model must equal ${TRIAL_PROVIDER_POLICY.managedFreeTrial.model}`,
    );
  }

  const now = new Date();
  const requestContext = {
    endpoint: 'POST /v1/providers/openrouter/issue',
    now,
    ip: resolveClientIp(c),
    installationId,
    hardwareHash: null,
  };
  const currentEntitlement = await getEntitlement(c.env.BROKER_DB, installationId);

  const subjectHook = await matchSubjectHook(c.env.BROKER_DB, requestContext);
  if (subjectHook) {
    const hookEntitlement = await getEntitlement(c.env.BROKER_DB, installationId);
    return publicErrorResponse(c, subjectHook.status, {
      code: subjectHook.code,
      class: subjectHook.class,
      subcode: subjectHook.subcode,
      retryAfterMs: subjectHook.retryAfterMs,
      message: subjectHook.message,
      entitlement: hookEntitlement,
    });
  }

  const installation = await getInstallation(c.env.BROKER_DB, installationId);
  if (!installation) {
    return releaseTokenInvalidResponse(c, currentEntitlement);
  }

  if (installation.device_public_key !== devicePublicKey) {
    return errorResponse(
      c,
      409,
      'device_public_key_mismatch',
      'issue must use the registered device_public_key for installation_id',
    );
  }

  const signedAtDate = parseIsoDate(signedAt);
  if (!signedAtDate) {
    return errorResponse(
      c,
      400,
      'invalid_request',
      'signed_at must be a valid ISO-8601 timestamp',
    );
  }

  if (
    Math.abs(signedAtDate.getTime() - now.getTime()) >
    ISSUE_MAX_CLOCK_SKEW_SECONDS * 1000
  ) {
    return errorResponse(
      c,
      401,
      'signature_skew',
      'signed_at must be within ±60 seconds of broker time',
    );
  }

  const signatureIsValid = await verifyEd25519Signature({
    devicePublicKey,
    signature,
    payload: buildCanonicalIssuePayload({
      installation_id: installationId,
      device_public_key: devicePublicKey,
      release_token: releaseToken,
      reason,
      budget_usd: budgetUsd,
      model,
      signed_at: signedAt,
    }),
  });
  if (!signatureIsValid) {
    return errorResponse(
      c,
      401,
      'signature_invalid',
      'signature verification failed for the registered device_public_key',
    );
  }

  const releaseTokenHash = await sha256Base64Url(releaseToken);
  const entitlement = currentEntitlement;
  if (!entitlement || entitlement.release_token_hash !== releaseTokenHash) {
    return releaseTokenInvalidResponse(c, entitlement);
  }

  const releaseTokenWindowError = validateReleaseTokenWindow(c, entitlement, now);
  if (releaseTokenWindowError) {
    return releaseTokenWindowError;
  }

  await recordRequestEvent(c.env.BROKER_DB, requestContext);

  const rateLimitDecision = await checkEndpointRateLimit(c.env.BROKER_DB, requestContext);
  if (rateLimitDecision) {
    return publicErrorResponse(c, rateLimitDecision.status, {
      code: rateLimitDecision.code,
      class: rateLimitDecision.class,
      subcode: rateLimitDecision.subcode,
      retryAfterMs: rateLimitDecision.retryAfterMs,
      message: rateLimitDecision.message,
      entitlement,
    });
  }

  const velocityCapDecision = await checkVelocityCapHook(c.env.BROKER_DB, requestContext);
  if (velocityCapDecision) {
    return publicErrorResponse(c, velocityCapDecision.status, {
      code: velocityCapDecision.code,
      class: velocityCapDecision.class,
      subcode: velocityCapDecision.subcode,
      retryAfterMs: velocityCapDecision.retryAfterMs,
      message: velocityCapDecision.message,
      entitlement,
    });
  }

  if (entitlement.status === 'active') {
    return issueSuccessResponse(c, entitlement);
  }

  if (entitlement.status !== 'pending_release') {
    return releaseTokenInvalidResponse(c, entitlement);
  }

  const issuedAt = now.toISOString();
  const expiresAt = addMonthsUtc(
    now,
    MANAGED_TRIAL_POLICY.entitlement.issuance.expiry.durationMonths,
  ).toISOString();
  const managedCredentialRef = randomBase64Url(16);
  const activationSucceeded = await activatePendingEntitlement(c.env.BROKER_DB, {
    installationId,
    releaseTokenHash,
    releaseTokenExpiresAt: entitlement.release_token_expires_at!,
    managedCredentialRef,
    issuedAt,
    expiresAt,
  });

  if (!activationSucceeded) {
    const currentEntitlement = await getEntitlement(c.env.BROKER_DB, installationId);
    if (
      currentEntitlement &&
      currentEntitlement.status === 'active' &&
      currentEntitlement.release_token_hash === releaseTokenHash
    ) {
      const currentReleaseTokenWindowError = validateReleaseTokenWindow(
        c,
        currentEntitlement,
        now,
      );
      if (currentReleaseTokenWindowError) {
        return currentReleaseTokenWindowError;
      }

      return issueSuccessResponse(c, currentEntitlement);
    }

    return releaseTokenInvalidResponse(c, currentEntitlement);
  }

  const activeEntitlement = await getEntitlement(c.env.BROKER_DB, installationId);
  if (!activeEntitlement) {
    throw new Error('active entitlement missing after successful issue activation');
  }

  return issueSuccessResponse(c, activeEntitlement);
}

async function activatePendingEntitlement(
  db: D1Database,
  input: {
    installationId: string;
    releaseTokenHash: string;
    releaseTokenExpiresAt: string;
    managedCredentialRef: string;
    issuedAt: string;
    expiresAt: string;
  },
): Promise<boolean> {
  const result = await db
    .prepare(
      `UPDATE openrouter_entitlements
          SET status = ?,
              managed_credential_ref = ?,
              issued_at = ?,
              expires_at = ?
        WHERE installation_id = ?
          AND status = ?
          AND release_token_hash = ?
          AND release_token_expires_at = ?`,
    )
    .bind(
      'active',
      input.managedCredentialRef,
      input.issuedAt,
      input.expiresAt,
      input.installationId,
      'pending_release',
      input.releaseTokenHash,
      input.releaseTokenExpiresAt,
    )
    .run();

  return (result.meta.changes ?? 0) === 1;
}

async function getInstallation(
  db: D1Database,
  installationId: string,
): Promise<InstallationRecord | null> {
  return db
    .prepare(
      `SELECT installation_id, device_public_key, hardware_hash, hardware_hash_salt_version,
              app_version, challenge, challenge_expires_at, challenge_salt_version,
              created_at, last_seen_at
         FROM installations
        WHERE installation_id = ?`,
    )
    .bind(installationId)
    .first<InstallationRecord>();
}

async function getEntitlement(
  db: D1Database,
  installationId: string,
): Promise<OpenRouterEntitlementRecord | null> {
  return db
    .prepare(
      `SELECT installation_id, status, budget_usd, managed_credential_ref, issued_at,
              expires_at, release_session_ref, release_token_hash, release_token_expires_at
         FROM openrouter_entitlements
        WHERE installation_id = ?`,
    )
    .bind(installationId)
    .first<OpenRouterEntitlementRecord>();
}

function issueSuccessResponse(
  c: Context<BrokerEnv>,
  entitlement: OpenRouterEntitlementRecord,
): Response {
  if (!entitlement.managed_credential_ref || !entitlement.expires_at) {
    throw new Error('active entitlement missing managed release metadata');
  }

  return c.json({
    openrouter_api_key: c.env.OPENROUTER_MANAGED_API_KEY,
    managed_credential_ref: entitlement.managed_credential_ref,
    managed_state: {
      lifecycle: 'active',
      managed_availability: true,
    },
    expires_at: entitlement.expires_at,
    budget_usd: entitlement.budget_usd,
    model: TRIAL_PROVIDER_POLICY.managedFreeTrial.model,
  });
}

function releaseTokenInvalidResponse(
  c: Context<BrokerEnv>,
  entitlement: OpenRouterEntitlementRecord | null = null,
): Response {
  return publicErrorResponse(c, 401, {
    code: 'challenge_invalid',
    class: 'security_fail',
    subcode: 'release_token_invalid',
    message: 'release_token does not match the active release session for installation_id',
    entitlement,
  });
}

function validateReleaseTokenWindow(
  c: Context<BrokerEnv>,
  entitlement: OpenRouterEntitlementRecord,
  now: Date,
): Response | null {
  if (!entitlement.release_token_expires_at) {
    return releaseTokenInvalidResponse(c, entitlement);
  }

  const releaseTokenExpiresAt = parseIsoDate(entitlement.release_token_expires_at);
  if (!releaseTokenExpiresAt) {
    return releaseTokenInvalidResponse(c, entitlement);
  }

  if (releaseTokenExpiresAt.getTime() < now.getTime()) {
    return publicErrorResponse(c, 410, {
      code: 'challenge_expired',
      class: 'retryable',
      subcode: 'release_token_expired',
      retryAfterMs: 0,
      message: 'release_token has expired and must be reissued',
      entitlement,
    });
  }

  return null;
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
  return errorResponse(
    c,
    400,
    'invalid_request',
    reason === 'invalid_json'
      ? 'request body must be valid JSON'
      : 'request body must be a JSON object',
  );
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null;
}

function errorResponse(
  c: Context<BrokerEnv>,
  status: 400 | 401 | 409 | 410,
  code: string,
  message: string,
): Response {
  const normalized = normalizeLegacyIssueError(code, message);

  return publicErrorResponse(c, status, normalized);
}

function normalizeLegacyIssueError(
  code: string,
  message: string,
): {
  code: 'invalid_request' | 'challenge_invalid' | 'trial_not_eligible';
  class: 'terminal' | 'security_fail';
  subcode?: string | null;
  message: string;
} {
  switch (code) {
    case 'invalid_request':
      return {
        code: 'invalid_request',
        class: 'terminal',
        message,
      };
    case 'signature_skew':
      return {
        code: 'challenge_invalid',
        class: 'security_fail',
        subcode: 'timestamp_skew',
        message,
      };
    case 'signature_invalid':
      return {
        code: 'challenge_invalid',
        class: 'security_fail',
        subcode: 'signature_mismatch',
        message,
      };
    case 'device_public_key_mismatch':
      return {
        code: 'trial_not_eligible',
        class: 'security_fail',
        subcode: 'installation_binding_mismatch',
        message,
      };
    default:
      return {
        code: 'challenge_invalid',
        class: 'security_fail',
        subcode: code,
        message,
      };
  }
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

function buildCanonicalIssuePayload(input: {
  installation_id: string;
  device_public_key: string;
  release_token: string;
  reason: string;
  budget_usd: number;
  model: string;
  signed_at: string;
}): Uint8Array {
  return new TextEncoder().encode(
    ISSUE_SIGNATURE_PAYLOAD_FIELDS.map((field) => String(input[field])).join('\n'),
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

async function sha256Base64Url(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    toArrayBuffer(new TextEncoder().encode(value)),
  );

  return encodeBase64Url(new Uint8Array(digest));
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
}

function randomBase64Url(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return encodeBase64Url(bytes);
}

function addMonthsUtc(value: Date, months: number): Date {
  const next = new Date(value.getTime());
  next.setUTCMonth(next.getUTCMonth() + months);
  return next;
}
