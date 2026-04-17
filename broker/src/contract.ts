import {
  MANAGED_TRIAL_POLICY,
  TRIAL_PROVIDER_POLICY,
} from './trial-policy';

export {
  BROKER_PUBLIC_INPUT_BOUNDS,
  BROKER_PERSISTENCE_MODEL,
  BROKER_RETENTION_POLICY,
  BROKER_RUNTIME_CONFIG_KEYS,
  BROKER_RUNTIME_CONFIG_SCHEMA,
  DEFAULT_BROKER_ABUSE_CONTROLS,
  FINGERPRINT_SALT_POLICY,
  OPENROUTER_ENTITLEMENT_STATUS_VALUES,
} from './persistence';
export {
  MANAGED_TRIAL_BUDGET_POLICY,
  MANAGED_TRIAL_COST_ACCOUNTING_POLICY,
  MANAGED_TRIAL_ENTITLEMENT_POLICY,
  MANAGED_TRIAL_LIFECYCLE_VALUES,
  MANAGED_TRIAL_LIVE_USAGE_POLICY,
  MANAGED_TRIAL_POLICY,
  TRIAL_PROVIDER_POLICY,
} from './trial-policy';
export type {
  BrokerDailyIssuanceCapConfig,
  BrokerEndpointRateLimitConfig,
  BrokerAbuseControlsConfigValue,
  BrokerAbuseSubjectHookRecord,
  BrokerConfigRow,
  BrokerRequestEventRecord,
  BrokerVelocityCapHookRecord,
  FingerprintSaltConfigValue,
  FingerprintSaltVersion,
  InstallationRecord,
  OpenRouterEntitlementRecord,
  OpenRouterEntitlementStatus,
} from './persistence';
export type { ManagedTrialLifecycle } from './trial-policy';

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
  // Transitional: the shared-key binding remains required until the runtime
  // issuance path switches away from OPENROUTER_MANAGED_API_KEY in a later task.
  secrets: [
    'OPENROUTER_MANAGED_API_KEY',
    'OPENROUTER_MANAGEMENT_API_KEY',
    'OPENROUTER_MANAGED_GUARDRAIL_ID',
    'OPENROUTER_MANAGED_USER_HMAC_SECRET',
  ],
} as const;

export interface BrokerBindings {
  BROKER_DB: D1Database;
  OPENROUTER_MANAGEMENT_API_KEY: string;
  OPENROUTER_MANAGED_GUARDRAIL_ID: string;
  OPENROUTER_MANAGED_API_KEY: string;
  OPENROUTER_MANAGED_USER_HMAC_SECRET: string;
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

export const FOUNDATION_RESPONSE = {
  service: BROKER_SERVICE_NAME,
  runtime: BROKER_RUNTIME_STACK,
  bindings: REQUIRED_BINDINGS,
  hosting: HOSTING_ASSUMPTIONS,
  serviceBoundary: SERVICE_BOUNDARY,
  trialProviderPolicy: TRIAL_PROVIDER_POLICY,
  managedTrialPolicy: MANAGED_TRIAL_POLICY,
} as const;
