import { describe, expect, it } from 'vitest';

import { MANAGED_TRIAL_POLICY, TRIAL_PROVIDER_POLICY } from '../src/contract';

describe('managed trial policy', () => {
  it('keeps the managed path pinned to OpenRouter Gemma 4 and reuses the provider contract', () => {
    expect(MANAGED_TRIAL_POLICY.managedPath).toEqual({
      provider: 'OpenRouter',
      model: 'google/gemma-4-26b-a4b-it',
    });
    expect(MANAGED_TRIAL_POLICY.managedPath).toBe(
      TRIAL_PROVIDER_POLICY.managedFreeTrial,
    );
  });

  it('limits issuance to one user-specific managed key per eligible installation with six-month expiry', () => {
    expect(MANAGED_TRIAL_POLICY.entitlement.issuance).toEqual({
      keyScope: 'user-specific',
      maxManagedKeysPerEligibleInstallation: 1,
      expiry: {
        durationMonths: 6,
        anchor: 'issued_at',
      },
    });
  });
});
