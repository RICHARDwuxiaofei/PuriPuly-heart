import { resolveEffectiveEntitlementLifecycle } from './managed-state';
import type { OpenRouterEntitlementRecord, ReferralCodeRecord } from './persistence';

export const REFERRAL_ID_LENGTH = 6;
export const REFERRAL_ID_ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ';

const REFERRAL_ID_PATTERN = new RegExp(
  `^[${REFERRAL_ID_ALPHABET}]{${REFERRAL_ID_LENGTH}}$`,
  'u',
);
const REFERRAL_RANDOM_REJECTION_THRESHOLD =
  Math.floor(256 / REFERRAL_ID_ALPHABET.length) * REFERRAL_ID_ALPHABET.length;
const REFERRAL_ID_MAX_RANDOM_DRAWS = 64;
const OWNED_DISCORD_USER_REF_PATTERN = /^ph-discord-user-v\d+_[A-Za-z0-9_-]{32,128}$/u;
const DEFAULT_REFERRAL_ID_COLLISION_ATTEMPTS = 12;

export type ReferralIdRandomBytes = (byteLength: number) => Uint8Array;
export type ReferralIdGenerator = () => string;

export type OwnedReferralIdEnsureFailureReason =
  | 'not_eligible'
  | 'unsafe_discord_user_ref'
  | 'disabled'
  | 'collision_exhausted';

export type OwnedReferralIdEnsureResult =
  | {
      ok: true;
      referralCode: ReferralCodeRecord;
      created: boolean;
    }
  | {
      ok: false;
      reason: OwnedReferralIdEnsureFailureReason;
    };

interface ActiveDiscordManagedReferralOwner extends OpenRouterEntitlementRecord {
  installation_id: string;
  discord_user_ref: string;
  managed_credential_ref: string;
  expires_at: string;
  discord_issue_status: 'active';
  discord_issue_delivered_at: string;
}

export function normalizeReferralId(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim().toUpperCase();
  if (!normalized || !REFERRAL_ID_PATTERN.test(normalized)) {
    return null;
  }

  return normalized;
}

export function generateReferralId(
  randomBytes: ReferralIdRandomBytes = cryptoReferralRandomBytes,
): string {
  let referralId = '';
  let drawCount = 0;

  while (referralId.length < REFERRAL_ID_LENGTH) {
    drawCount += 1;
    if (drawCount > REFERRAL_ID_MAX_RANDOM_DRAWS) {
      throw new Error('unable to generate Referral ID from random source');
    }

    const bytes = randomBytes(REFERRAL_ID_LENGTH - referralId.length);
    if (bytes.length === 0) {
      throw new Error('Referral ID random source returned no bytes');
    }

    for (const byte of bytes) {
      if (byte >= REFERRAL_RANDOM_REJECTION_THRESHOLD) {
        continue;
      }

      referralId += REFERRAL_ID_ALPHABET[byte % REFERRAL_ID_ALPHABET.length];
      if (referralId.length === REFERRAL_ID_LENGTH) {
        break;
      }
    }
  }

  return referralId;
}

export async function ensureOwnedReferralIdForActiveDiscordManagedUser(
  db: D1Database,
  input: {
    installationId: string;
    nowIso: string;
    generateReferralId?: ReferralIdGenerator;
    maxCollisionAttempts?: number;
  },
): Promise<OwnedReferralIdEnsureResult> {
  const owner = await getActiveDiscordManagedReferralOwner(
    db,
    input.nowIso,
    input.installationId,
  );
  if (!owner) {
    return { ok: false, reason: 'not_eligible' };
  }

  const discordUserRef = owner.discord_user_ref.trim();
  if (!isPersistableOwnedDiscordUserRef(discordUserRef)) {
    return { ok: false, reason: 'unsafe_discord_user_ref' };
  }

  const existing = await getReferralCodeForDiscordUserRef(db, discordUserRef);
  if (existing) {
    if (existing.status === 'disabled') {
      return { ok: false, reason: 'disabled' };
    }

    const refreshed = await refreshActiveReferralCodeOwnerInstallation(db, {
      referralId: existing.referral_id,
      discordUserRef,
      installationId: owner.installation_id,
      nowIso: input.nowIso,
    });
    if (!refreshed) {
      const latest = await getReferralCodeForDiscordUserRef(db, discordUserRef);
      if (latest?.status === 'disabled') {
        return { ok: false, reason: 'disabled' };
      }

      return { ok: false, reason: 'not_eligible' };
    }

    return {
      ok: true,
      referralCode: refreshed,
      created: false,
    };
  }

  const createReferralId = input.generateReferralId ?? generateReferralId;
  const maxCollisionAttempts =
    input.maxCollisionAttempts ?? DEFAULT_REFERRAL_ID_COLLISION_ATTEMPTS;

  for (let attempt = 0; attempt < maxCollisionAttempts; attempt += 1) {
    const referralId = normalizeReferralId(createReferralId());
    if (!referralId) {
      throw new Error('generated Referral ID did not match the approved format');
    }

    const inserted = await insertActiveOwnedReferralCode(db, {
      referralId,
      discordUserRef,
      installationId: owner.installation_id,
      nowIso: input.nowIso,
    });
    if (inserted) {
      const created = await getActiveReferralCodeForDiscordUserRef(db, discordUserRef);
      if (!created) {
        const latest = await getReferralCodeForDiscordUserRef(db, discordUserRef);
        if (latest?.status === 'disabled') {
          return { ok: false, reason: 'disabled' };
        }

        throw new Error('created Referral ID could not be read back as active');
      }
      return { ok: true, referralCode: created, created: true };
    }

    const concurrentlyCreated = await getReferralCodeForDiscordUserRef(
      db,
      discordUserRef,
    );
    if (concurrentlyCreated) {
      if (concurrentlyCreated.status === 'disabled') {
        return { ok: false, reason: 'disabled' };
      }
      return {
        ok: true,
        referralCode: concurrentlyCreated,
        created: false,
      };
    }
  }

  return { ok: false, reason: 'collision_exhausted' };
}

function cryptoReferralRandomBytes(byteLength: number): Uint8Array {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return bytes;
}

async function getActiveDiscordManagedReferralOwner(
  db: D1Database,
  nowIso: string,
  installationId: string,
): Promise<ActiveDiscordManagedReferralOwner | null> {
  const row = await db
    .prepare(
      `SELECT entitlement.installation_id,
              entitlement.status,
              entitlement.budget_usd,
              entitlement.managed_credential_ref,
              entitlement.issued_at,
              entitlement.expires_at,
              entitlement.release_session_ref,
              entitlement.release_token_hash,
              entitlement.release_token_expires_at,
              entitlement.verified_hardware_hash,
              entitlement.verified_hardware_hash_salt_version,
              entitlement.discord_user_ref,
              entitlement.discord_issue_status,
              entitlement.discord_issue_reserved_at,
              entitlement.discord_issue_delivered_at
         FROM openrouter_entitlements entitlement
         JOIN discord_identities identity
           ON identity.discord_user_ref = entitlement.discord_user_ref
        WHERE entitlement.installation_id = ?
          AND entitlement.status = 'active'
          AND entitlement.discord_user_ref IS NOT NULL
          AND length(trim(entitlement.discord_user_ref)) > 0
          AND entitlement.managed_credential_ref IS NOT NULL
          AND length(trim(entitlement.managed_credential_ref)) > 0
          AND entitlement.expires_at IS NOT NULL
          AND length(trim(entitlement.expires_at)) > 0
          AND entitlement.discord_issue_status = 'active'
          AND entitlement.discord_issue_delivered_at IS NOT NULL
          AND length(trim(entitlement.discord_issue_delivered_at)) > 0
          AND identity.status = 'active'
          AND identity.entitlement_installation_id = entitlement.installation_id`,
    )
    .bind(installationId)
    .first<ActiveDiscordManagedReferralOwner>();

  if (!row) {
    return null;
  }

  const now = new Date(nowIso);
  if (Number.isNaN(now.getTime())) {
    return null;
  }

  return resolveEffectiveEntitlementLifecycle(row, now) === 'active' ? row : null;
}

function isPersistableOwnedDiscordUserRef(value: string): boolean {
  return OWNED_DISCORD_USER_REF_PATTERN.test(value);
}

async function getReferralCodeForDiscordUserRef(
  db: D1Database,
  discordUserRef: string,
): Promise<ReferralCodeRecord | null> {
  return db
    .prepare(
      `SELECT referral_id,
              owner_discord_user_ref,
              owner_installation_id,
              status,
              created_at,
              updated_at
         FROM referral_codes
        WHERE owner_discord_user_ref = ?`,
    )
    .bind(discordUserRef)
    .first<ReferralCodeRecord>();
}

async function getActiveReferralCodeForDiscordUserRef(
  db: D1Database,
  discordUserRef: string,
): Promise<ReferralCodeRecord | null> {
  return db
    .prepare(
      `SELECT referral_id,
              owner_discord_user_ref,
              owner_installation_id,
              status,
              created_at,
              updated_at
         FROM referral_codes
        WHERE owner_discord_user_ref = ?
          AND status = 'active'`,
    )
    .bind(discordUserRef)
    .first<ReferralCodeRecord>();
}

async function refreshActiveReferralCodeOwnerInstallation(
  db: D1Database,
  input: {
    referralId: string;
    discordUserRef: string;
    installationId: string;
    nowIso: string;
  },
): Promise<ReferralCodeRecord | null> {
  await db
    .prepare(
      `UPDATE referral_codes
          SET owner_installation_id = ?,
              updated_at = ?
        WHERE referral_id = ?
          AND owner_discord_user_ref = ?
          AND status = 'active'
          AND (owner_installation_id IS NULL OR owner_installation_id <> ?)`,
    )
    .bind(
      input.installationId,
      input.nowIso,
      input.referralId,
      input.discordUserRef,
      input.installationId,
    )
    .run();

  return getActiveReferralCodeForDiscordUserRef(db, input.discordUserRef);
}

async function insertActiveOwnedReferralCode(
  db: D1Database,
  input: {
    referralId: string;
    discordUserRef: string;
    installationId: string;
    nowIso: string;
  },
): Promise<boolean> {
  const result = await db
    .prepare(
      `INSERT OR IGNORE INTO referral_codes (
          referral_id,
          owner_discord_user_ref,
          owner_installation_id,
          status,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, 'active', ?, ?)`,
    )
    .bind(
      input.referralId,
      input.discordUserRef,
      input.installationId,
      input.nowIso,
      input.nowIso,
    )
    .run();

  return Number(result.meta.changes ?? 0) === 1;
}
