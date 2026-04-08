import type { OpenRouterEntitlementRecord } from './persistence';
import { TRIAL_PROVIDER_POLICY } from './trial-policy';

export interface ManagedStateResponse {
  managed_state: {
    lifecycle:
      | 'none'
      | 'pending_release'
      | 'active'
      | 'expired'
      | 'revoked';
    managed_availability: boolean;
  };
  current_entitlement:
    | {
        provider: string;
        budget_usd: number;
        issued_at: string | null;
        expires_at: string | null;
      }
    | null;
}

export interface TrialStatusResponse extends ManagedStateResponse {
  onboarding_eligibility: {
    eligible: boolean;
    reason: 'eligible' | 'pending_release' | 'active' | 'expired' | 'revoked';
  };
}

export function normalizeManagedState(
  entitlement: OpenRouterEntitlementRecord | null,
): ManagedStateResponse {
  const lifecycle = entitlement?.status ?? 'none';

  return {
    managed_state: {
      lifecycle,
      managed_availability:
        lifecycle === 'none' ||
        lifecycle === 'pending_release' ||
        lifecycle === 'active',
    },
    current_entitlement: entitlement
      ? {
          provider: TRIAL_PROVIDER_POLICY.managedFreeTrial.provider,
          budget_usd: entitlement.budget_usd,
          issued_at: entitlement.issued_at,
          expires_at: entitlement.expires_at,
        }
      : null,
  };
}

export function normalizeTrialStatusResponse(
  entitlement: OpenRouterEntitlementRecord | null,
): TrialStatusResponse {
  const managedState = normalizeManagedState(entitlement);
  const lifecycle = managedState.managed_state.lifecycle;

  return {
    ...managedState,
    onboarding_eligibility: {
      eligible: lifecycle === 'none' || lifecycle === 'pending_release',
      reason: lifecycle === 'none' ? 'eligible' : lifecycle,
    },
  };
}
