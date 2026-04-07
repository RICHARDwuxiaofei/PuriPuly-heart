import { describe, expect, it } from 'vitest';

import app from '../src/index';
import { BROKER_SERVICE_NAME } from '../src/contract';

describe('broker foundation', () => {
  it('exposes a fetch-compatible Hono worker app with explicit bindings', async () => {
    expect(app.fetch).toBeTypeOf('function');

    const response = await app.request('http://broker.test/v1/foundation');
    expect(response.status).toBe(200);

    await expect(response.json()).resolves.toEqual({
      service: BROKER_SERVICE_NAME,
      runtime: {
        language: 'TypeScript',
        framework: 'Hono',
        runtime: 'Cloudflare Workers',
        database: 'Cloudflare D1',
        secretStorage: 'Worker secrets',
      },
      bindings: {
        d1: 'BROKER_DB',
        secrets: ['OPENROUTER_MANAGED_API_KEY'],
      },
      hosting: {
        regionMode: 'single-region-rollout-assumption',
        d1LocationHint: 'apac',
        infrastructure: ['worker-service', 'd1-database', 'worker-secrets'],
        exclusions: [
          'translation-proxying',
          'multi-region-deployment',
          'kv',
          'r2',
          'admin-dashboard',
        ],
      },
      serviceBoundary: {
        role: 'trial-credential-broker',
        proxiesTranslationText: false,
        inferencePath: 'app-direct-to-openrouter',
      },
      trialProviderPolicy: {
        managedFreeTrial: {
          provider: 'OpenRouter',
          model: 'google/gemma-4-26b-a4b-it',
        },
        upstreamProviderRouting: 'unpinned-by-broker',
        excludedProviders: ['Alibaba'],
      },
    });
  });

  it('keeps the public foundation contract at the boundary level only', async () => {
    const response = await app.request('http://broker.test/v1/foundation');
    expect(response.status).toBe(200);

    const payload = (await response.json()) as {
      serviceBoundary: Record<string, unknown>;
    };

    expect(payload.serviceBoundary).not.toHaveProperty('responsibilities');
  });

  it('reports broker liveness without implying proxy behavior', async () => {
    const response = await app.request('http://broker.test/healthz');

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      ok: true,
      service: BROKER_SERVICE_NAME,
    });
  });
});
