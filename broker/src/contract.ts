export const BROKER_SERVICE_NAME = 'puripuly-heart-broker';

export const BROKER_RUNTIME_STACK = {
  language: 'TypeScript',
  framework: 'Hono',
  runtime: 'Cloudflare Workers',
  database: 'Cloudflare D1',
  secretStorage: 'Worker secrets',
} as const;

export const REQUIRED_BINDINGS = {
  d1: 'BROKER_DB',
  secrets: ['OPENROUTER_MANAGED_API_KEY'],
} as const;

export interface BrokerBindings {
  BROKER_DB: D1Database;
  OPENROUTER_MANAGED_API_KEY: string;
}

export type BrokerEnv = {
  Bindings: BrokerBindings;
};

export const HOSTING_ASSUMPTIONS = {
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
} as const;

export const SERVICE_BOUNDARY = {
  role: 'trial-credential-broker',
  proxiesTranslationText: false,
  inferencePath: 'app-direct-to-openrouter',
} as const;

export const TRIAL_PROVIDER_POLICY = {
  managedFreeTrial: {
    provider: 'OpenRouter',
    model: 'google/gemma-4-26b-a4b-it',
  },
  upstreamProviderRouting: 'unpinned-by-broker',
  excludedProviders: ['Alibaba'],
} as const;

export const FOUNDATION_RESPONSE = {
  service: BROKER_SERVICE_NAME,
  runtime: BROKER_RUNTIME_STACK,
  bindings: REQUIRED_BINDINGS,
  hosting: HOSTING_ASSUMPTIONS,
  serviceBoundary: SERVICE_BOUNDARY,
  trialProviderPolicy: TRIAL_PROVIDER_POLICY,
} as const;
