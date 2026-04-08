export const BROKER_RUNTIME_CONFIG_KEYS = {
  fingerprintSalt: 'fingerprint_salt',
  abuseControls: 'abuse_controls',
} as const;

export interface BrokerEndpointRateLimitConfig {
  endpoint: string;
  scope: 'ip' | 'installation_id';
  maxRequests: number;
  windowMinutes: number;
}

export interface BrokerDailyIssuanceCapConfig {
  endpoint: 'POST /v1/providers/openrouter/issue';
  scope: 'global';
  maxCount: number | null;
  windowDays: number;
}

export interface BrokerAbuseControlsConfigValue {
  trialChallenge: BrokerEndpointRateLimitConfig;
  trialChallengeVerify: BrokerEndpointRateLimitConfig;
  openrouterIssue: BrokerEndpointRateLimitConfig;
  trialStatus: BrokerEndpointRateLimitConfig;
  newActiveEntitlementsPerDay: BrokerDailyIssuanceCapConfig;
}

export const DEFAULT_BROKER_ABUSE_CONTROLS: BrokerAbuseControlsConfigValue = {
  trialChallenge: {
    endpoint: 'POST /v1/trial/challenge',
    scope: 'ip',
    maxRequests: 10,
    windowMinutes: 15,
  },
  trialChallengeVerify: {
    endpoint: 'POST /v1/trial/challenge/verify',
    scope: 'installation_id',
    maxRequests: 5,
    windowMinutes: 15,
  },
  openrouterIssue: {
    endpoint: 'POST /v1/providers/openrouter/issue',
    scope: 'installation_id',
    maxRequests: 3,
    windowMinutes: 15,
  },
  trialStatus: {
    endpoint: 'GET /v1/trial/status',
    scope: 'installation_id',
    maxRequests: 30,
    windowMinutes: 15,
  },
  newActiveEntitlementsPerDay: {
    endpoint: 'POST /v1/providers/openrouter/issue',
    scope: 'global',
    maxCount: null,
    windowDays: 1,
  },
};

export const BROKER_RUNTIME_CONFIG_SCHEMA = {
  [BROKER_RUNTIME_CONFIG_KEYS.fingerprintSalt]: ['current', 'previous', 'rotated_at'],
  [BROKER_RUNTIME_CONFIG_KEYS.abuseControls]: DEFAULT_BROKER_ABUSE_CONTROLS,
} as const;

export type BrokerRuntimeConfigKey =
  (typeof BROKER_RUNTIME_CONFIG_KEYS)[keyof typeof BROKER_RUNTIME_CONFIG_KEYS];

export interface BrokerConfigRow {
  key: BrokerRuntimeConfigKey;
  value: string;
  updated_at: string;
}

export interface FingerprintSaltVersion {
  version: number;
  salt: string;
  valid_until: string | null;
}

export interface FingerprintSaltConfigValue {
  current: {
    version: number;
    salt: string;
  };
  previous: FingerprintSaltVersion | null;
  rotated_at: string | null;
}

export interface InstallationRecord {
  installation_id: string;
  device_public_key: string;
  hardware_hash: string | null;
  hardware_hash_salt_version: number | null;
  app_version: string;
  challenge: string | null;
  challenge_expires_at: string | null;
  challenge_salt_version: number | null;
  created_at: string;
  last_seen_at: string;
}

export const BROKER_PUBLIC_INPUT_BOUNDS = {
  installation_id: {
    minLength: 1,
    maxLength: 128,
    rejectWhitespaceOnly: true,
    rejectControlCharacters: true,
    rejectNewlines: true,
  },
  app_version: {
    minLength: 1,
    maxLength: 64,
    rejectWhitespaceOnly: true,
    rejectControlCharacters: true,
    rejectNewlines: true,
  },
  hardware_hash: {
    minLength: 1,
    maxLength: 128,
    nullable: true,
    rejectWhitespaceOnly: true,
    rejectControlCharacters: true,
    rejectNewlines: true,
  },
} as const;

export const OPENROUTER_ENTITLEMENT_STATUS_VALUES = [
  'pending_release',
  'active',
  'expired',
  'revoked',
] as const;

export type OpenRouterEntitlementStatus =
  (typeof OPENROUTER_ENTITLEMENT_STATUS_VALUES)[number];

export interface OpenRouterEntitlementRecord {
  installation_id: string;
  status: OpenRouterEntitlementStatus;
  budget_usd: number;
  managed_credential_ref: string | null;
  issued_at: string | null;
  expires_at: string | null;
  release_session_ref: string | null;
  release_token_hash: string | null;
  release_token_expires_at: string | null;
}

export interface BrokerRequestEventRecord {
  id: number;
  endpoint: string;
  ip: string | null;
  installation_id: string | null;
  observed_at: string;
}

export interface BrokerVelocityCapHookRecord {
  id: number;
  subject_type: 'ip' | 'installation_id';
  subject_value: string;
  max_requests: number;
  window_minutes: number;
  outcome_code:
    | 'rate_limited'
    | 'issuance_suspended'
    | 'trial_unavailable'
    | 'trial_not_eligible';
  outcome_class: 'retryable' | 'terminal' | 'security_fail';
  outcome_subcode: string | null;
  reason: string | null;
  active: 0 | 1;
  created_at: string;
  expires_at: string | null;
}

export interface BrokerAbuseSubjectHookRecord {
  id: number;
  hook_kind: 'denylist' | 'reputation' | 'revocation';
  subject_type: 'ip' | 'installation_id' | 'hardware_hash';
  subject_value: string;
  outcome_code:
    | 'issuance_suspended'
    | 'trial_unavailable'
    | 'trial_not_eligible';
  outcome_class: 'retryable' | 'terminal' | 'security_fail';
  outcome_subcode: string | null;
  reason: string | null;
  active: 0 | 1;
  created_at: string;
  expires_at: string | null;
}

export const BROKER_PERSISTENCE_MODEL = {
  database: 'Cloudflare D1',
  tables: {
    brokerConfig: {
      name: 'broker_config',
      primaryKey: 'key',
      columns: ['key', 'value', 'updated_at'],
      valueEncoding: 'JSON',
      supportedKeys: ['fingerprint_salt', 'abuse_controls'],
      constraints: {
        key: 'supported-keys-only',
        value: 'valid-json',
      },
      seedRows: ['fingerprint_salt', 'abuse_controls'],
    },
    installations: {
      name: 'installations',
      primaryKey: 'installation_id',
      columns: [
        'installation_id',
        'device_public_key',
        'hardware_hash',
        'hardware_hash_salt_version',
        'app_version',
        'challenge',
        'challenge_expires_at',
        'challenge_salt_version',
        'created_at',
        'last_seen_at',
      ],
      unique: ['device_public_key'],
      indexed: [
        'hardware_hash',
        'hardware_hash_salt_version',
        'challenge_expires_at',
        'last_seen_at',
      ],
      textBounds: BROKER_PUBLIC_INPUT_BOUNDS,
      updateRules: {
        onChallenge: [
          'overwrite challenge',
            'overwrite challenge_expires_at',
            'overwrite challenge_salt_version',
            'overwrite app_version',
            'clear hardware_hash and hardware_hash_salt_version only when lifecycle is none or pending_release',
            'preserve hardware_hash state for active, expired, and revoked lifecycles',
            'touch last_seen_at',
          ],
        onVerify: [
          'clear challenge',
          'clear challenge_expires_at',
          'clear challenge_salt_version',
          'persist hardware_hash only after successful verify',
          'persist hardware_hash_salt_version with hardware_hash',
        ],
        beforeVerify: ['hardware_hash stays null until verify'],
      },
    },
    openrouterEntitlements: {
      name: 'openrouter_entitlements',
      provider: 'OpenRouter',
      rowCardinality: 'zero-or-one-row-per-installation',
      primaryKey: 'installation_id',
      absenceRepresents: 'none',
      storedStatuses: OPENROUTER_ENTITLEMENT_STATUS_VALUES,
      columns: [
        'installation_id',
        'status',
        'budget_usd',
        'managed_credential_ref',
        'issued_at',
        'expires_at',
        'release_session_ref',
        'release_token_hash',
        'release_token_expires_at',
      ],
      unique: ['managed_credential_ref'],
      indexed: ['status', 'expires_at'],
      partialUniqueIndexes: [
        {
          name: 'idx_openrouter_entitlements_release_token_hash',
          columns: ['release_token_hash'],
          predicate: 'release_token_hash IS NOT NULL',
        },
      ],
      updateStrategy: 'in-place',
      liveRemainingBudgetSource: 'OpenRouter metadata',
      releaseSessionState: {
        storage: 'ephemeral-columns-on-openrouter_entitlements',
        fields: [
          'release_session_ref',
          'release_token_hash',
          'release_token_expires_at',
        ],
        releaseToken: {
          binding: 'installation-bound',
          oneTimeUse: true,
          ttlMinutes: 15,
          issuanceIdempotencyKey: 'installation_identity + release_session_ref',
          verifyBehavior: 'rotate for existing pending_release row',
        },
      },
    },
    brokerRequestEvents: {
      name: 'broker_request_events',
      purpose: ['per-endpoint rate limits', 'cross-endpoint velocity hooks'],
      columns: ['id', 'endpoint', 'ip', 'installation_id', 'observed_at'],
      appendOnly: true,
      indexed: [
        'endpoint + ip + observed_at',
        'endpoint + installation_id + observed_at',
        'ip + observed_at',
        'installation_id + observed_at',
      ],
    },
    brokerVelocityCapHooks: {
      name: 'broker_velocity_cap_hooks',
      purpose: 'manual cross-endpoint velocity controls with observable outcomes',
      columns: [
        'id',
        'subject_type',
        'subject_value',
        'max_requests',
        'window_minutes',
        'outcome_code',
        'outcome_class',
        'outcome_subcode',
        'reason',
        'active',
        'created_at',
        'expires_at',
      ],
      supportedSubjects: ['ip', 'installation_id'],
      indexed: ['subject_type + subject_value + active + expires_at'],
    },
    brokerAbuseSubjectHooks: {
      name: 'broker_abuse_subject_hooks',
      purpose:
        'denylist, reputation, and fast-revocation controls with observable outcomes',
      columns: [
        'id',
        'hook_kind',
        'subject_type',
        'subject_value',
        'outcome_code',
        'outcome_class',
        'outcome_subcode',
        'reason',
        'active',
        'created_at',
        'expires_at',
      ],
      hookKinds: ['denylist', 'reputation', 'revocation'],
      supportedSubjects: ['ip', 'installation_id', 'hardware_hash'],
      indexed: ['subject_type + subject_value + hook_kind + active + expires_at'],
    },
  },
} as const;

export const BROKER_RETENTION_POLICY = {
  challengePreflight: {
    statuses: ['none'],
    entitlementRow: 'absent',
    challengeState: 'present',
    inactiveDays: 1,
    reference: 'max(installations.last_seen_at, installations.challenge_expires_at)',
    deleteFrom: 'installations',
    cascadesTo: [],
  },
  pendingRelease: {
    statuses: ['pending_release'],
    inactiveDays: 30,
    reference: 'installations.last_seen_at',
    deleteFrom: 'installations',
    cascadesTo: ['openrouter_entitlements'],
  },
  terminal: {
    statuses: ['expired', 'revoked'],
    inactiveDays: 90,
    reference: 'max(installations.last_seen_at, openrouter_entitlements.expires_at)',
    deleteFrom: 'installations',
    cascadesTo: ['openrouter_entitlements'],
  },
} as const;

export const FINGERPRINT_SALT_POLICY = {
  configKey: 'fingerprint_salt',
  managedBy: 'broker',
  sharedAcrossClients: true,
  duplicateDetectionScope: 'cross-installation',
  storageModel: 'bounded-current-plus-previous',
  valueShape: {
    current: ['version', 'salt'],
    previous: ['version', 'salt', 'valid_until'],
    rotated_at: 'timestamp-or-null',
  },
  installationTracking: {
    challengeSaltVersionField: 'challenge_salt_version',
    hardwareHashSaltVersionField: 'hardware_hash_salt_version',
  },
  duplicateMatching: {
    hashField: 'hardware_hash',
    currentVersionOnly: true,
  },
  rotation: {
    newChallengesUse: 'current salt only',
    inFlightChallenges: 'accept previous salt version until challenge_expires_at',
    staleHardwareHash:
      'exclude non-current hardware_hash from duplicate matching until refreshed or cleared',
    migrationPath:
      'overwrite hardware_hash in place on next verify with current salt, otherwise clear on challenge reissue only for none or pending_release lifecycles',
  },
} as const;
