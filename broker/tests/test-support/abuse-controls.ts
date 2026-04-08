import type { TestBrokerEnv } from './sqlite-d1';

interface StoredAbuseControls {
  trialChallenge: {
    endpoint: string;
    scope: 'ip';
    maxRequests: number;
    windowMinutes: number;
  };
  trialChallengeVerify: {
    endpoint: string;
    scope: 'installation_id';
    maxRequests: number;
    windowMinutes: number;
  };
  openrouterIssue: {
    endpoint: string;
    scope: 'installation_id';
    maxRequests: number;
    windowMinutes: number;
  };
  trialStatus: {
    endpoint: string;
    scope: 'installation_id';
    maxRequests: number;
    windowMinutes: number;
  };
  newActiveEntitlementsPerDay: {
    endpoint: string;
    scope: 'global';
    maxCount: number | null;
    windowDays: number;
  };
}

export function updateAbuseControls(
  env: TestBrokerEnv,
  mutate: (controls: StoredAbuseControls) => void,
): void {
  const row = env.__db
    .prepare('SELECT value FROM broker_config WHERE key = ?')
    .get('abuse_controls') as { value: string };
  const controls = JSON.parse(row.value) as StoredAbuseControls;
  mutate(controls);
  env.__db
    .prepare('UPDATE broker_config SET value = ?, updated_at = ? WHERE key = ?')
    .run(JSON.stringify(controls), new Date().toISOString(), 'abuse_controls');
}

export function replaceAbuseControlsValue(env: TestBrokerEnv, value: unknown): void {
  env.__db
    .prepare('UPDATE broker_config SET value = ?, updated_at = ? WHERE key = ?')
    .run(JSON.stringify(value), new Date().toISOString(), 'abuse_controls');
}

export function insertVelocityCapHook(
  env: TestBrokerEnv,
  input: {
    subject_type: 'ip' | 'installation_id';
    subject_value: string;
    max_requests: number;
    window_minutes: number;
    outcome_code: string;
    outcome_class: string;
    outcome_subcode?: string | null;
    reason?: string | null;
    expires_at?: string | null;
  },
): void {
  env.__db
    .prepare(
      `INSERT INTO broker_velocity_cap_hooks (
          subject_type,
          subject_value,
          max_requests,
          window_minutes,
          outcome_code,
          outcome_class,
          outcome_subcode,
          reason,
          expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      input.subject_type,
      input.subject_value,
      input.max_requests,
      input.window_minutes,
      input.outcome_code,
      input.outcome_class,
      input.outcome_subcode ?? null,
      input.reason ?? null,
      input.expires_at ?? null,
    );
}

export function insertSubjectHook(
  env: TestBrokerEnv,
  input: {
    hook_kind: 'denylist' | 'reputation' | 'revocation';
    subject_type: 'ip' | 'installation_id' | 'hardware_hash';
    subject_value: string;
    outcome_code: string;
    outcome_class: string;
    outcome_subcode?: string | null;
    reason?: string | null;
    expires_at?: string | null;
  },
): void {
  env.__db
    .prepare(
      `INSERT INTO broker_abuse_subject_hooks (
          hook_kind,
          subject_type,
          subject_value,
          outcome_code,
          outcome_class,
          outcome_subcode,
          reason,
          expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      input.hook_kind,
      input.subject_type,
      input.subject_value,
      input.outcome_code,
      input.outcome_class,
      input.outcome_subcode ?? null,
      input.reason ?? null,
      input.expires_at ?? null,
    );
}
