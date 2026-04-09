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
const smokeBaseUrl = process.env.BROKER_DEPLOY_SMOKE_BASE_URL?.trim();
const describeDeploySmoke =
  smokeBaseUrl || process.env.CI === 'true' ? describe : describe.skip;

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
  }, 180_000);
});

function normalizeSmokeBaseUrl(baseUrl) {
  if (!baseUrl) {
    throw new Error('BROKER_DEPLOY_SMOKE_BASE_URL is required for deploy smoke');
  }

  return new URL(baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`);
}

function validateCanonicalWorkersDevTarget(baseUrl, canonicalWorkerName) {
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

function timestampFromHeaders(headers) {
  const headerValue = headers.get('date');

  if (headerValue) {
    const parsed = Date.parse(headerValue);

    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toISOString();
    }
  }

  return new Date().toISOString();
}

async function requestJson({ method, url, body, headers = {} }) {
  const response = await fetch(url, {
    method,
    headers: {
      ...(body ? { 'content-type': 'application/json' } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
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

function redactIssueBody(rawText) {
  return rawText.replace(
    /"openrouter_api_key"\s*:\s*"[^"]+"/gu,
    '"openrouter_api_key":"[REDACTED]"',
  );
}
