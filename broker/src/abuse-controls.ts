import type { Context } from 'hono';

import type { PublicErrorClass, PublicErrorCode } from './broker-error';
import type { BrokerEnv } from './contract';
import {
  BROKER_RUNTIME_CONFIG_KEYS,
  DEFAULT_BROKER_ABUSE_CONTROLS,
  type BrokerAbuseControlsConfigValue,
  type BrokerEndpointRateLimitConfig,
  type OpenRouterEntitlementRecord,
} from './persistence';

export interface RequestAbuseContext {
  endpoint: string;
  now: Date;
  ip: string | null;
  installationId: string | null;
  hardwareHash: string | null;
}

export interface AbuseDecision {
  status: 400 | 401 | 404 | 409 | 410 | 429 | 500 | 503;
  code: PublicErrorCode;
  class: PublicErrorClass;
  message: string;
  subcode: string | null;
  retryAfterMs: number | null;
}

export interface SubjectHookMatch extends AbuseDecision {
  hookKind: 'denylist' | 'reputation' | 'revocation';
}

interface VelocityCapHookRow {
  id: number;
  subject_type: 'ip' | 'installation_id';
  subject_value: string;
  max_requests: number;
  window_minutes: number;
  outcome_code: PublicErrorCode;
  outcome_class: PublicErrorClass;
  outcome_subcode: string | null;
  reason: string | null;
  expires_at: string | null;
}

interface SubjectHookRow {
  id: number;
  hook_kind: 'denylist' | 'reputation' | 'revocation';
  subject_type: 'ip' | 'installation_id' | 'hardware_hash';
  subject_value: string;
  outcome_code: PublicErrorCode;
  outcome_class: PublicErrorClass;
  outcome_subcode: string | null;
  reason: string | null;
  expires_at: string | null;
}

export function resolveClientIp(c: Context<BrokerEnv>): string | null {
  const directIp = nonEmptyString(c.req.header('cf-connecting-ip'));
  if (directIp) {
    return directIp;
  }

  const forwardedFor = nonEmptyString(c.req.header('x-forwarded-for'));
  if (!forwardedFor) {
    return null;
  }

  return nonEmptyString(forwardedFor.split(',')[0] ?? null);
}

export async function getBrokerAbuseControlsConfig(
  db: D1Database,
): Promise<BrokerAbuseControlsConfigValue> {
  const row = await db
    .prepare('SELECT value FROM broker_config WHERE key = ?')
    .bind(BROKER_RUNTIME_CONFIG_KEYS.abuseControls)
    .first<{ value: string }>();

  if (!row) {
    return DEFAULT_BROKER_ABUSE_CONTROLS;
  }

  try {
    const parsed = JSON.parse(row.value) as unknown;
    return validateBrokerAbuseControlsConfig(parsed) ?? DEFAULT_BROKER_ABUSE_CONTROLS;
  } catch {
    return DEFAULT_BROKER_ABUSE_CONTROLS;
  }
}

export async function recordRequestEvent(
  db: D1Database,
  context: RequestAbuseContext,
): Promise<void> {
  if (!context.ip && !context.installationId) {
    return;
  }

  await db
    .prepare(
      `INSERT INTO broker_request_events (
          endpoint,
          ip,
          installation_id,
          observed_at
        ) VALUES (?, ?, ?, ?)`,
    )
    .bind(
      context.endpoint,
      context.ip,
      context.installationId,
      context.now.toISOString(),
    )
    .run();
}

export async function checkEndpointRateLimit(
  db: D1Database,
  context: RequestAbuseContext,
): Promise<AbuseDecision | null> {
  const controls = await getBrokerAbuseControlsConfig(db);
  const endpointConfig = getEndpointRateLimitConfig(controls, context.endpoint);
  if (!endpointConfig) {
    return null;
  }

  const scopeValue =
    endpointConfig.scope === 'ip' ? context.ip : context.installationId;
  if (!scopeValue) {
    return null;
  }

  const windowStartIso = new Date(
    context.now.getTime() - endpointConfig.windowMinutes * 60_000,
  ).toISOString();
  const scopeColumn = endpointConfig.scope === 'ip' ? 'ip' : 'installation_id';
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count, MIN(observed_at) AS oldest
         FROM broker_request_events
        WHERE endpoint = ?
          AND ${scopeColumn} = ?
          AND observed_at >= ?`,
    )
    .bind(context.endpoint, scopeValue, windowStartIso)
    .first<{ count: number; oldest: string | null }>();

  const count = Number(row?.count ?? 0);
  if (count <= endpointConfig.maxRequests) {
    return null;
  }

  return {
    status: 429,
    code: 'rate_limited',
    class: 'retryable',
    message: `request rate limit exceeded for ${context.endpoint}`,
    subcode:
      endpointConfig.scope === 'ip'
        ? 'ip_rate_limited'
        : 'installation_rate_limited',
    retryAfterMs: retryAfterFromIso(
      row?.oldest,
      endpointConfig.windowMinutes * 60_000,
      context.now,
    ),
  };
}

export async function checkVelocityCapHook(
  db: D1Database,
  context: RequestAbuseContext,
): Promise<AbuseDecision | null> {
  const matchingHooks = await listMatchingVelocityCapHooks(db, context);

  for (const hook of matchingHooks) {
    const windowStartIso = new Date(
      context.now.getTime() - hook.window_minutes * 60_000,
    ).toISOString();
    const column = hook.subject_type === 'ip' ? 'ip' : 'installation_id';
    const row = await db
      .prepare(
        `SELECT COUNT(*) AS count, MIN(observed_at) AS oldest
           FROM broker_request_events
          WHERE ${column} = ?
            AND observed_at >= ?`,
      )
      .bind(hook.subject_value, windowStartIso)
      .first<{ count: number; oldest: string | null }>();

    const count = Number(row?.count ?? 0);
    if (count <= hook.max_requests) {
      continue;
    }

    return {
      status: mapPublicErrorCodeToStatus(hook.outcome_code),
      code: hook.outcome_code,
      class: hook.outcome_class,
      message: hook.reason ?? 'velocity cap hook rejected the request',
      subcode: normalizeHookPublicSubcode({
        code: hook.outcome_code,
        subjectType: hook.subject_type,
      }),
      retryAfterMs: retryAfterFromIso(
        row?.oldest,
        hook.window_minutes * 60_000,
        context.now,
      ),
    };
  }

  return null;
}

export async function matchSubjectHook(
  db: D1Database,
  context: RequestAbuseContext,
): Promise<SubjectHookMatch | null> {
  const hooks = await listMatchingSubjectHooks(db, context);
  const hook = hooks[0] ?? null;
  if (!hook) {
    return null;
  }

  if (hook.hook_kind === 'revocation') {
    await applyRevocationHook(db, hook);
  }

  return {
    hookKind: hook.hook_kind,
    status: mapPublicErrorCodeToStatus(hook.outcome_code),
    code: hook.outcome_code,
    class: hook.outcome_class,
    message: hook.reason ?? `${hook.hook_kind} hook rejected the request`,
    subcode: normalizeHookPublicSubcode({
      code: hook.outcome_code,
      subjectType: hook.subject_type,
    }),
    retryAfterMs: retryAfterUntilIso(hook.expires_at, context.now),
  };
}

export async function checkDailyIssuanceCap(
  db: D1Database,
  now: Date,
  currentEntitlement: OpenRouterEntitlementRecord | null,
): Promise<AbuseDecision | null> {
  if (
    currentEntitlement?.status === 'pending_release' ||
    currentEntitlement?.status === 'active'
  ) {
    return null;
  }

  const controls = await getBrokerAbuseControlsConfig(db);
  const maxCount = controls.newActiveEntitlementsPerDay.maxCount;
  if (maxCount === null) {
    return null;
  }

  const windowStart = startOfUtcDay(now);
  windowStart.setUTCDate(
    windowStart.getUTCDate() - (controls.newActiveEntitlementsPerDay.windowDays - 1),
  );
  const windowEnd = new Date(windowStart.getTime());
  windowEnd.setUTCDate(
    windowEnd.getUTCDate() + controls.newActiveEntitlementsPerDay.windowDays,
  );
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count, MIN(issued_at) AS oldest
         FROM openrouter_entitlements
        WHERE issued_at IS NOT NULL
          AND issued_at >= ?
          AND issued_at < ?`,
    )
    .bind(windowStart.toISOString(), windowEnd.toISOString())
    .first<{ count: number; oldest: string | null }>();

  const count = Number(row?.count ?? 0);
  if (count < maxCount) {
    return null;
  }

  return {
    status: 503,
    code: 'issuance_suspended',
    class: 'retryable',
    message: 'new entitlement issuance is temporarily suspended',
    subcode: 'global_cap_reached',
    retryAfterMs: Math.max(windowEnd.getTime() - now.getTime(), 0),
  };
}

export async function hasConflictingHardwareDuplicate(
  db: D1Database,
  input: {
    installationId: string;
    hardwareHash: string;
    challengeSaltVersion: number | null;
    currentSaltVersion: number;
  },
): Promise<boolean> {
  if (
    input.challengeSaltVersion === null ||
    input.challengeSaltVersion !== input.currentSaltVersion
  ) {
    return false;
  }

  const row = await db
    .prepare(
      `SELECT installation_id
         FROM openrouter_entitlements
        WHERE verified_hardware_hash = ?
          AND verified_hardware_hash_salt_version = ?
          AND status IN ('pending_release', 'active')
          AND installation_id <> ?
        LIMIT 1`,
    )
    .bind(
      input.hardwareHash,
      input.challengeSaltVersion,
      input.installationId,
    )
    .first<{ installation_id: string }>();

  if (row !== null) {
    return true;
  }

  const legacyReservedRow = await db
    .prepare(
      `SELECT e.installation_id
         FROM openrouter_entitlements e
         JOIN installations i
            ON i.installation_id = e.installation_id
        WHERE e.status IN ('pending_release', 'active')
          AND e.verified_hardware_hash IS NULL
          AND e.verified_hardware_hash_salt_version IS NULL
          AND i.hardware_hash = ?
          AND i.hardware_hash_salt_version = ?
          AND e.installation_id <> ?
        LIMIT 1`,
    )
    .bind(
      input.hardwareHash,
      input.challengeSaltVersion,
      input.installationId,
    )
    .first<{ installation_id: string }>();

  return legacyReservedRow !== null;
}

function getEndpointRateLimitConfig(
  controls: BrokerAbuseControlsConfigValue,
  endpoint: string,
): BrokerEndpointRateLimitConfig | null {
  switch (endpoint) {
    case 'POST /v1/trial/challenge':
      return controls.trialChallenge;
    case 'POST /v1/trial/challenge/verify':
      return controls.trialChallengeVerify;
    case 'POST /v1/providers/openrouter/issue':
      return controls.openrouterIssue;
    case 'GET /v1/trial/status':
      return controls.trialStatus;
    default:
      return null;
  }
}

async function listMatchingVelocityCapHooks(
  db: D1Database,
  context: RequestAbuseContext,
): Promise<VelocityCapHookRow[]> {
  const filters: string[] = [];
  const params: Array<string | number | null> = [];
  if (context.ip) {
    filters.push('(subject_type = ? AND subject_value = ?)');
    params.push('ip', context.ip);
  }
  if (context.installationId) {
    filters.push('(subject_type = ? AND subject_value = ?)');
    params.push('installation_id', context.installationId);
  }

  if (filters.length === 0) {
    return [];
  }

  const result = await db
    .prepare(
      `SELECT id, subject_type, subject_value, max_requests, window_minutes,
              outcome_code, outcome_class, outcome_subcode, reason, expires_at
         FROM broker_velocity_cap_hooks
        WHERE active = 1
          AND (expires_at IS NULL OR expires_at > ?)
          AND (${filters.join(' OR ')})
        ORDER BY id ASC`,
    )
    .bind(context.now.toISOString(), ...params)
    .all<VelocityCapHookRow>();

  return result.results;
}

async function listMatchingSubjectHooks(
  db: D1Database,
  context: RequestAbuseContext,
): Promise<SubjectHookRow[]> {
  const filters: string[] = [];
  const params: Array<string | number | null> = [];
  if (context.ip) {
    filters.push('(subject_type = ? AND subject_value = ?)');
    params.push('ip', context.ip);
  }
  if (context.installationId) {
    filters.push('(subject_type = ? AND subject_value = ?)');
    params.push('installation_id', context.installationId);
  }
  if (context.hardwareHash) {
    filters.push('(subject_type = ? AND subject_value = ?)');
    params.push('hardware_hash', context.hardwareHash);
  }

  if (filters.length === 0) {
    return [];
  }

  const result = await db
    .prepare(
      `SELECT id, hook_kind, subject_type, subject_value, outcome_code,
              outcome_class, outcome_subcode, reason, expires_at
         FROM broker_abuse_subject_hooks
        WHERE active = 1
          AND (expires_at IS NULL OR expires_at > ?)
          AND (${filters.join(' OR ')})
        ORDER BY CASE hook_kind
          WHEN 'revocation' THEN 0
          WHEN 'denylist' THEN 1
          ELSE 2
        END,
        id ASC`,
    )
    .bind(context.now.toISOString(), ...params)
    .all<SubjectHookRow>();

  return result.results;
}

async function applyRevocationHook(
  db: D1Database,
  hook: SubjectHookRow,
): Promise<void> {
  const installationIds =
    hook.subject_type === 'installation_id'
      ? [hook.subject_value]
      : hook.subject_type === 'hardware_hash'
        ? await listInstallationIdsByHardwareHash(db, hook.subject_value)
        : [];

  if (installationIds.length === 0) {
    return;
  }

  for (const installationId of installationIds) {
    await db
      .prepare(
        `UPDATE openrouter_entitlements
            SET status = 'revoked',
                release_session_ref = NULL,
                release_token_hash = NULL,
                release_token_expires_at = NULL
          WHERE installation_id = ?
            AND status <> 'revoked'`,
      )
      .bind(installationId)
      .run();
  }
}

async function listInstallationIdsByHardwareHash(
  db: D1Database,
  hardwareHash: string,
): Promise<string[]> {
  const result = await db
    .prepare(
      `SELECT installation_id
         FROM installations
        WHERE hardware_hash = ?`,
    )
    .bind(hardwareHash)
    .all<{ installation_id: string }>();

  return result.results.map(
    ({ installation_id }: { installation_id: string }) => installation_id,
  );
}

function retryAfterFromIso(
  startIso: string | null | undefined,
  durationMs: number,
  now: Date,
): number | null {
  if (!startIso) {
    return null;
  }

  return Math.max(new Date(startIso).getTime() + durationMs - now.getTime(), 0);
}

function retryAfterUntilIso(
  expiresAtIso: string | null,
  now: Date,
): number | null {
  if (!expiresAtIso) {
    return null;
  }

  return Math.max(new Date(expiresAtIso).getTime() - now.getTime(), 0);
}

function startOfUtcDay(value: Date): Date {
  return new Date(
    Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate()),
  );
}

function normalizeHookPublicSubcode(input: {
  code: PublicErrorCode;
  subjectType: 'ip' | 'installation_id' | 'hardware_hash';
}): string | null {
  if (input.code !== 'rate_limited') {
    return null;
  }

  if (input.subjectType === 'ip') {
    return 'ip_rate_limited';
  }

  if (input.subjectType === 'installation_id') {
    return 'installation_rate_limited';
  }

  return null;
}

function validateBrokerAbuseControlsConfig(
  value: unknown,
): BrokerAbuseControlsConfigValue | null {
  if (!isJsonObject(value)) {
    return null;
  }

  const trialChallenge = validateEndpointRateLimitConfig(
    value.trialChallenge,
    'POST /v1/trial/challenge',
    'ip',
  );
  const trialChallengeVerify = validateEndpointRateLimitConfig(
    value.trialChallengeVerify,
    'POST /v1/trial/challenge/verify',
    'installation_id',
  );
  const openrouterIssue = validateEndpointRateLimitConfig(
    value.openrouterIssue,
    'POST /v1/providers/openrouter/issue',
    'installation_id',
  );
  const trialStatus = validateEndpointRateLimitConfig(
    value.trialStatus,
    'GET /v1/trial/status',
    'installation_id',
  );
  const newActiveEntitlementsPerDay = validateDailyIssuanceCapConfig(
    value.newActiveEntitlementsPerDay,
  );

  if (
    !trialChallenge ||
    !trialChallengeVerify ||
    !openrouterIssue ||
    !trialStatus ||
    !newActiveEntitlementsPerDay
  ) {
    return null;
  }

  return {
    trialChallenge,
    trialChallengeVerify,
    openrouterIssue,
    trialStatus,
    newActiveEntitlementsPerDay,
  };
}

function validateEndpointRateLimitConfig(
  value: unknown,
  endpoint: BrokerEndpointRateLimitConfig['endpoint'],
  scope: BrokerEndpointRateLimitConfig['scope'],
): BrokerEndpointRateLimitConfig | null {
  if (!isJsonObject(value)) {
    return null;
  }

  if (
    value.endpoint !== endpoint ||
    value.scope !== scope ||
    !isPositiveInteger(value.maxRequests) ||
    !isPositiveInteger(value.windowMinutes)
  ) {
    return null;
  }

  return {
    endpoint,
    scope,
    maxRequests: value.maxRequests,
    windowMinutes: value.windowMinutes,
  };
}

function validateDailyIssuanceCapConfig(
  value: unknown,
): BrokerAbuseControlsConfigValue['newActiveEntitlementsPerDay'] | null {
  if (!isJsonObject(value)) {
    return null;
  }

  if (
    value.endpoint !== 'POST /v1/providers/openrouter/issue' ||
    value.scope !== 'global' ||
    !(value.maxCount === null || isPositiveInteger(value.maxCount)) ||
    !isPositiveInteger(value.windowDays)
  ) {
    return null;
  }

  return {
    endpoint: 'POST /v1/providers/openrouter/issue',
    scope: 'global',
    maxCount: value.maxCount,
    windowDays: value.windowDays,
  };
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

function nonEmptyString(value: string | null | undefined): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function mapPublicErrorCodeToStatus(
  code: PublicErrorCode,
): 400 | 401 | 404 | 409 | 410 | 429 | 500 | 503 {
  switch (code) {
    case 'invalid_request':
      return 400;
    case 'rate_limited':
      return 429;
    case 'challenge_expired':
      return 410;
    case 'challenge_invalid':
      return 401;
    case 'issuance_suspended':
    case 'trial_unavailable':
      return 503;
    case 'trial_not_eligible':
      return 409;
    case 'internal_error':
      return 500;
  }
}
