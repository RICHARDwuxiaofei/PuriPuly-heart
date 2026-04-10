import { describe, expect, it } from 'vitest';

import { BROKER_SERVICE_NAME } from '../../src/contract';
import {
  TRIAL_STATUS_SIGNATURE_HEADER,
  TRIAL_STATUS_TIMESTAMP_HEADER,
} from '../../src/trial-handshake';
import {
  createDeviceKeyPair,
  signCanonicalIssueRequest,
  signCanonicalStatusRequest,
  signCanonicalVerifyRequest,
} from '../test-support/ed25519';

const CANONICAL_WORKER_NAME = 'puripuly-heart-broker';
const ISSUE_REASON = 'llm_start';
const ISSUE_BUDGET_USD = 0.07;
const ISSUE_MODEL = 'google/gemma-4-26b-a4b-it';
const BOOTSTRAP_PLACEHOLDER = '__BOOTSTRAP_REQUIRED__';
const OPENROUTER_API_BASE_URL = new URL('https://openrouter.ai');
const smokeBaseUrl = process.env.BROKER_DEPLOY_SMOKE_BASE_URL?.trim();
const smokeDisallowedModel = normalizeDisallowedModel(
  process.env.BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL,
  ISSUE_MODEL,
  process.env.CI === 'true',
);
type JsonRequestOptions = {
  method: string;
  url: URL;
  body?: unknown;
  headers?: HeadersInit;
};

const describeDeploySmoke =
  smokeBaseUrl || process.env.CI === 'true' ? describe : describe.skip;

describe('broker deploy smoke helpers', () => {
  it('reads issued child-key metadata from the OpenRouter current-key payload', () => {
    expect(
      readOpenRouterCurrentKeyMetadata({
        data: {
          limit: ISSUE_BUDGET_USD,
          expires_at: '2026-10-08T06:00:00.000Z',
        },
      }),
    ).toEqual({
      limit: ISSUE_BUDGET_USD,
      expiresAt: '2026-10-08T06:00:00.000Z',
    });
  });

  it('recognizes model-routing failures as guardrail enforcement for a disallowed model probe', () => {
    expect(
      isDisallowedModelGuardrailFailure(503, {
        error: {
          code: 503,
          message: 'No allowed model/provider is available for this request.',
        },
      }),
    ).toBe(true);
    expect(
      isDisallowedModelGuardrailFailure(401, {
        error: {
          code: 401,
          message: 'Invalid credentials',
        },
      }),
    ).toBe(false);
  });

  it('requires a distinct disallowed model probe when smoke runs in CI', () => {
    expect(normalizeDisallowedModel(undefined, ISSUE_MODEL, false)).toBeUndefined();
    expect(normalizeDisallowedModel('openai/gpt-4o-mini', ISSUE_MODEL, true)).toBe(
      'openai/gpt-4o-mini',
    );
    expect(() => normalizeDisallowedModel(ISSUE_MODEL, ISSUE_MODEL, true)).toThrow(
      /must differ from the managed allowlisted model/i,
    );
  });
});

describeDeploySmoke('broker direct deploy smoke', () => {
  it('passes the canonical workers.dev trial flow', async () => {
    const baseUrl = normalizeSmokeBaseUrl(smokeBaseUrl);
    validateCanonicalWorkersDevTarget(baseUrl, CANONICAL_WORKER_NAME);

    const keyPair = await createDeviceKeyPair();
    const installationId = `deploy-smoke-${crypto.randomUUID().replace(/-/gu, '')}`.slice(
      0,
      64,
    );
    const appVersion = 'deploy-smoke-1.0.0';
    const hardwareHash = `deploy-smoke-hardware-${crypto.randomUUID()}`.slice(0, 96);

    const healthz = await requestJson({
      method: 'GET',
      url: new URL('/healthz', baseUrl),
    });
    expect(healthz.status).toBe(200);
    expect(healthz.body.ok).toBe(true);
    expect(healthz.body.service).toBe(BROKER_SERVICE_NAME);

    const foundation = await requestJson({
      method: 'GET',
      url: new URL('/v1/foundation', baseUrl),
    });
    expect(foundation.status).toBe(200);
    expect(foundation.body.service).toBe(BROKER_SERVICE_NAME);
    expect(foundation.body.trialProviderPolicy?.managedFreeTrial?.provider).toBe(
      'OpenRouter',
    );
    expect(foundation.body.trialProviderPolicy?.managedFreeTrial?.model).toBe(
      ISSUE_MODEL,
    );

    const challenge = await requestJson({
      method: 'POST',
      url: new URL('/v1/trial/challenge', baseUrl),
      body: {
        installation_id: installationId,
        device_public_key: keyPair.devicePublicKey,
        app_version: appVersion,
      },
    });
    expect(challenge.status).toBe(200);
    expect(typeof challenge.body.challenge).toBe('string');
    expect(typeof challenge.body.challenge_expires_at).toBe('string');
    expect(challenge.body.managed_state?.lifecycle).toBe('none');
    expect(challenge.body.fingerprint_salt?.current?.salt).not.toBe(
      BOOTSTRAP_PLACEHOLDER,
    );

    const verifySignedAt = timestampFromHeaders(challenge.headers);
    const verifyRequest = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: installationId,
      device_public_key: keyPair.devicePublicKey,
      challenge: challenge.body.challenge,
      challenge_expires_at: challenge.body.challenge_expires_at,
      hardware_hash: hardwareHash,
      app_version: appVersion,
      signed_at: verifySignedAt,
    });
    const verify = await requestJson({
      method: 'POST',
      url: new URL('/v1/trial/challenge/verify', baseUrl),
      body: verifyRequest,
    });
    expect(verify.status).toBe(200);
    expect(typeof verify.body.release_token).toBe('string');
    expect(typeof verify.body.release_token_expires_at).toBe('string');
    expect(verify.body.managed_state?.lifecycle).toBe('pending_release');
    expect(verify.body.managed_state?.managed_availability).toBe(true);

    const statusTimestamp = timestampFromHeaders(verify.headers);
    const statusRequest = await signCanonicalStatusRequest(keyPair.privateKey, {
      installation_id: installationId,
      timestamp: statusTimestamp,
    });
    const statusUrl = new URL('/v1/trial/status', baseUrl);
    statusUrl.searchParams.set('installation_id', installationId);
    const status = await requestJson({
      method: 'GET',
      url: statusUrl,
      headers: {
        [TRIAL_STATUS_TIMESTAMP_HEADER]: statusRequest.timestamp,
        [TRIAL_STATUS_SIGNATURE_HEADER]: statusRequest.signature,
      },
    });
    expect(status.status).toBe(200);
    expect(status.body.managed_state?.lifecycle).toBe('pending_release');
    expect(status.body.current_entitlement?.provider).toBe('OpenRouter');

    const issueSignedAt = timestampFromHeaders(status.headers);
    const issueRequest = await signCanonicalIssueRequest(keyPair.privateKey, {
      installation_id: installationId,
      device_public_key: keyPair.devicePublicKey,
      release_token: verify.body.release_token,
      hardware_hash: hardwareHash,
      reason: ISSUE_REASON,
      budget_usd: ISSUE_BUDGET_USD,
      model: ISSUE_MODEL,
      signed_at: issueSignedAt,
    });
    const issue = await requestJson({
      method: 'POST',
      url: new URL('/v1/providers/openrouter/issue', baseUrl),
      body: issueRequest,
    });
    expect(issue.status).toBe(200);
    expect(issue.body.managed_state?.lifecycle).toBe('active');
    expect(issue.body.managed_state?.managed_availability).toBe(true);
    expect(issue.body.budget_usd).toBe(ISSUE_BUDGET_USD);
    expect(issue.body.model).toBe(ISSUE_MODEL);
    expect(typeof issue.body.openrouter_api_key).toBe('string');
    expect(issue.body.openrouter_api_key.length).toBeGreaterThan(0);
    expect(typeof issue.body.managed_credential_ref).toBe('string');
    expect(issue.body.managed_credential_ref.length).toBeGreaterThan(0);
    expect(typeof issue.body.expires_at).toBe('string');

    const issuedKeyMetadata = readOpenRouterCurrentKeyMetadata(
      (
        await requestJson({
          method: 'GET',
          url: new URL('/api/v1/key', OPENROUTER_API_BASE_URL),
          headers: {
            authorization: `Bearer ${issue.body.openrouter_api_key}`,
          },
        })
      ).body,
    );
    expect(issuedKeyMetadata.limit).toBe(ISSUE_BUDGET_USD);
    expect(Date.parse(issuedKeyMetadata.expiresAt)).toBe(Date.parse(issue.body.expires_at));

    const guardrailProbe = await requestJsonAllowFailure({
      method: 'POST',
      url: new URL('/api/v1/chat/completions', OPENROUTER_API_BASE_URL),
      headers: {
        authorization: `Bearer ${issue.body.openrouter_api_key}`,
      },
      body: {
        model: requireDisallowedModel(smokeDisallowedModel),
        messages: [
          {
            role: 'user',
            content: 'Reply with the single word blocked.',
          },
        ],
        max_tokens: 8,
      },
    });
    expect(guardrailProbe.status).toBeGreaterThanOrEqual(400);
    expect(
      isDisallowedModelGuardrailFailure(guardrailProbe.status, guardrailProbe.body),
    ).toBe(true);
  }, 180_000);
});

function normalizeDisallowedModel(
  rawValue: string | undefined,
  managedAllowlistedModel: string,
  isCi: boolean,
): string | undefined {
  const normalized = rawValue?.trim();

  if (!normalized) {
    if (isCi) {
      throw new Error(
        'BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL is required for CI smoke runs',
      );
    }

    return undefined;
  }

  if (normalized === managedAllowlistedModel) {
    throw new Error(
      'BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL must differ from the managed allowlisted model',
    );
  }

  return normalized;
}

function requireDisallowedModel(model: string | undefined): string {
  if (!model) {
    throw new Error('BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL is required for deploy smoke');
  }

  return model;
}

function normalizeSmokeBaseUrl(baseUrl: string | undefined): URL {
  if (!baseUrl) {
    throw new Error('BROKER_DEPLOY_SMOKE_BASE_URL is required for deploy smoke');
  }

  return new URL(baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`);
}

function validateCanonicalWorkersDevTarget(baseUrl: URL, canonicalWorkerName: string): void {
  if (baseUrl.protocol !== 'https:') {
    throw new Error('deploy smoke must target an https workers.dev URL');
  }

  if (!baseUrl.hostname.endsWith('.workers.dev')) {
    throw new Error('deploy smoke must target the canonical workers.dev hostname');
  }

  if (!baseUrl.hostname.startsWith(`${canonicalWorkerName}.`)) {
    throw new Error(
      `deploy smoke must target the canonical worker ${canonicalWorkerName}`,
    );
  }
}

function timestampFromHeaders(headers: Headers): string {
  const headerValue = headers.get('date');

  if (headerValue) {
    const parsed = Date.parse(headerValue);

    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toISOString();
    }
  }

  return new Date().toISOString();
}

function readOpenRouterCurrentKeyMetadata(payload: unknown): {
  limit: number;
  expiresAt: string;
} {
  const data = readObjectField(payload, 'data', 'OpenRouter current-key response');
  const { limit } = data;
  const expiresAt = data.expires_at;

  if (typeof limit !== 'number' || !Number.isFinite(limit)) {
    throw new Error('OpenRouter current-key response must include a numeric data.limit');
  }

  if (typeof expiresAt !== 'string' || Number.isNaN(Date.parse(expiresAt))) {
    throw new Error(
      'OpenRouter current-key response must include a valid ISO timestamp in data.expires_at',
    );
  }

  return {
    limit,
    expiresAt,
  };
}

function isDisallowedModelGuardrailFailure(status: number, body: unknown): boolean {
  if (status < 400 || status === 401) {
    return false;
  }

  return /allowed model|disallowed model|model\/provider|model[^\n]*available|provider[^\n]*available|guardrail|route/iu.test(
    stringifyForPatternMatch(body),
  );
}

function readObjectField(
  value: unknown,
  fieldName: string,
  context: string,
): Record<string, unknown> {
  if (!isRecord(value) || !isRecord(value[fieldName])) {
    throw new Error(`${context} must include an object ${fieldName}`);
  }

  return value[fieldName];
}

function stringifyForPatternMatch(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function requestJson({ method, url, body, headers = {} }: JsonRequestOptions) {
  const response = await fetch(url, {
    method,
    headers: {
      ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const rawText = await response.text();
  const safeText = redactIssueBody(rawText);

  if (!response.ok) {
    throw new Error(
      `${method} ${url.pathname} failed with ${response.status}: ${safeText}`,
    );
  }

  try {
    return {
      status: response.status,
      headers: response.headers,
      body: JSON.parse(rawText),
    };
  } catch {
    throw new Error(`${method} ${url.pathname} returned non-JSON: ${safeText}`);
  }
}

async function requestJsonAllowFailure({
  method,
  url,
  body,
  headers = {},
}: JsonRequestOptions) {
  const response = await fetch(url, {
    method,
    headers: {
      ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const rawText = await response.text();

  try {
    return {
      status: response.status,
      headers: response.headers,
      body: JSON.parse(rawText),
    };
  } catch {
    return {
      status: response.status,
      headers: response.headers,
      body: redactIssueBody(rawText),
    };
  }
}

function redactIssueBody(rawText: string): string {
  return rawText.replace(
    /"openrouter_api_key"\s*:\s*"[^"]+"/gu,
    '"openrouter_api_key":"[REDACTED]"',
  );
}
