const encoder = new TextEncoder();

export interface DeviceKeyPair {
  privateKey: CryptoKey;
  devicePublicKey: string;
}

export interface SignedVerifyRequestInput {
  installation_id: string;
  device_public_key: string;
  challenge: string;
  challenge_expires_at: string;
  hardware_hash: string;
  app_version: string;
  signed_at: string;
}

export async function createDeviceKeyPair(): Promise<DeviceKeyPair> {
  const keyPair = await crypto.subtle.generateKey('Ed25519', true, [
    'sign',
    'verify',
  ]);
  const rawPublicKey = await crypto.subtle.exportKey('raw', keyPair.publicKey);

  return {
    privateKey: keyPair.privateKey,
    devicePublicKey: encodeBase64Url(new Uint8Array(rawPublicKey)),
  };
}

export async function signCanonicalVerifyRequest(
  privateKey: CryptoKey,
  input: SignedVerifyRequestInput,
): Promise<SignedVerifyRequestInput & { signature: string }> {
  return {
    ...input,
    signature: await signPayload(privateKey, canonicalVerifyPayload(input)),
  };
}

export async function signNonCanonicalVerifyRequest(
  privateKey: CryptoKey,
  input: SignedVerifyRequestInput,
): Promise<SignedVerifyRequestInput & { signature: string }> {
  return {
    ...input,
    signature: await signPayload(privateKey, nonCanonicalVerifyPayload(input)),
  };
}

function canonicalVerifyPayload(input: SignedVerifyRequestInput): Uint8Array {
  return encoder.encode(
    [
      input.installation_id,
      input.device_public_key,
      input.challenge,
      input.challenge_expires_at,
      input.hardware_hash,
      input.app_version,
      input.signed_at,
    ].join('\n'),
  );
}

function nonCanonicalVerifyPayload(input: SignedVerifyRequestInput): Uint8Array {
  return encoder.encode(
    [
      input.challenge,
      input.installation_id,
      input.device_public_key,
      input.challenge_expires_at,
      input.hardware_hash,
      input.app_version,
      input.signed_at,
    ].join('\n'),
  );
}

async function signPayload(
  privateKey: CryptoKey,
  payload: Uint8Array,
): Promise<string> {
  const signature = await crypto.subtle.sign(
    'Ed25519',
    privateKey,
    toArrayBuffer(payload),
  );
  return encodeBase64Url(new Uint8Array(signature));
}

function encodeBase64Url(bytes: Uint8Array): string {
  const binary = Array.from(bytes, (value) => String.fromCharCode(value)).join('');
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/u, '');
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
}
