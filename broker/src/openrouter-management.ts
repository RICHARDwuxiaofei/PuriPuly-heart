import { MANAGED_TRIAL_BUDGET_POLICY } from './contract';

const OPENROUTER_MANAGEMENT_API_BASE_URL = 'https://openrouter.ai/api/v1';
const MANAGED_CHILD_KEY_NAME_PREFIX = 'puripuly-heart';

type FetchImpl = typeof fetch;

export type OpenRouterManagementOperation =
  | 'create_key'
  | 'assign_guardrail'
  | 'disable_key'
  | 'delete_key';

export type OpenRouterManagementErrorCode =
  | 'network_error'
  | 'upstream_http_error'
  | 'malformed_upstream';

export interface OpenRouterManagementFailureDetails {
  operation: OpenRouterManagementOperation;
  code: OpenRouterManagementErrorCode;
  status: number | null;
  upstreamCode: number | null;
  message: string;
}

export type ManagedChildKeyCleanupStepResult =
  | { ok: true }
  | {
      ok: false;
      error: OpenRouterManagementFailureDetails;
    };

export type ManagedChildKeyCleanupResult =
  | { ok: true }
  | {
      ok: false;
      reason: {
        disable: ManagedChildKeyCleanupStepResult;
        delete: ManagedChildKeyCleanupStepResult;
      };
    };

export class OpenRouterManagementError extends Error {
  readonly operation: OpenRouterManagementOperation;
  readonly code: OpenRouterManagementErrorCode;
  readonly status: number | null;
  readonly upstreamCode: number | null;

  constructor(input: OpenRouterManagementFailureDetails & { cause?: unknown }) {
    super(
      input.message,
      input.cause === undefined ? undefined : { cause: input.cause },
    );
    this.name = 'OpenRouterManagementError';
    this.operation = input.operation;
    this.code = input.code;
    this.status = input.status;
    this.upstreamCode = input.upstreamCode;
  }
}

export async function createManagedChildKey(input: {
  managementApiKey: string;
  installationId: string;
  releaseSessionRef: string;
  expiresAt: string;
  fetchImpl?: FetchImpl;
}): Promise<{ rawKey: string; hash: string }> {
  const response = await requestOpenRouter({
    operation: 'create_key',
    path: '/keys',
    managementApiKey: input.managementApiKey,
    fetchImpl: input.fetchImpl,
    method: 'POST',
    body: {
      name: `${MANAGED_CHILD_KEY_NAME_PREFIX}:${input.installationId}:${input.releaseSessionRef}`,
      limit: MANAGED_TRIAL_BUDGET_POLICY.hardLimit,
      limit_reset: MANAGED_TRIAL_BUDGET_POLICY.limitReset,
      include_byok_in_limit: false,
      expires_at: input.expiresAt,
    },
  });
  const payload = await readSuccessJson(response, 'create_key');

  if (!isRecord(payload)) {
    throw malformedUpstreamError(
      'create_key',
      response.status,
      'OpenRouter create-key response must be a JSON object',
    );
  }

  const rawKey = payload.key;
  const data = payload.data;
  if (typeof rawKey !== 'string') {
    throw malformedUpstreamError(
      'create_key',
      response.status,
      'OpenRouter create-key response must include a string key',
    );
  }
  if (!isRecord(data) || typeof data.hash !== 'string') {
    throw malformedUpstreamError(
      'create_key',
      response.status,
      'OpenRouter create-key response must include data.hash',
    );
  }

  return {
    rawKey,
    hash: data.hash,
  };
}

export async function assignManagedGuardrail(input: {
  managementApiKey: string;
  guardrailId: string;
  keyHash: string;
  fetchImpl?: FetchImpl;
}): Promise<void> {
  const response = await requestOpenRouter({
    operation: 'assign_guardrail',
    path: `/guardrails/${input.guardrailId}/assignments/keys`,
    managementApiKey: input.managementApiKey,
    fetchImpl: input.fetchImpl,
    method: 'POST',
    body: {
      key_hashes: [input.keyHash],
    },
  });
  const payload = await readSuccessJson(response, 'assign_guardrail');

  if (!isRecord(payload) || typeof payload.assigned_count !== 'number') {
    throw malformedUpstreamError(
      'assign_guardrail',
      response.status,
      'OpenRouter guardrail assignment response must include assigned_count',
    );
  }
  if (payload.assigned_count < 1) {
    throw malformedUpstreamError(
      'assign_guardrail',
      response.status,
      'OpenRouter guardrail assignment did not report any assigned keys',
    );
  }
}

export async function cleanupManagedChildKey(input: {
  managementApiKey: string;
  keyHash: string;
  fetchImpl?: FetchImpl;
}): Promise<ManagedChildKeyCleanupResult> {
  const disable = await tryCleanupStep({
    operation: 'disable_key',
    path: `/keys/${input.keyHash}`,
    managementApiKey: input.managementApiKey,
    fetchImpl: input.fetchImpl,
    method: 'PATCH',
    body: { disabled: true },
  });
  const deletion = await tryCleanupStep({
    operation: 'delete_key',
    path: `/keys/${input.keyHash}`,
    managementApiKey: input.managementApiKey,
    fetchImpl: input.fetchImpl,
    method: 'DELETE',
  });

  if (disable.ok && deletion.ok) {
    return { ok: true };
  }

  return {
    ok: false,
    reason: {
      disable,
      delete: deletion,
    },
  };
}

async function tryCleanupStep(input: {
  operation: Extract<OpenRouterManagementOperation, 'disable_key' | 'delete_key'>;
  path: string;
  managementApiKey: string;
  fetchImpl?: FetchImpl;
  method: 'PATCH' | 'DELETE';
  body?: unknown;
}): Promise<
  ManagedChildKeyCleanupStepResult
> {
  try {
    const response = await requestOpenRouter({
      operation: input.operation,
      path: input.path,
      managementApiKey: input.managementApiKey,
      fetchImpl: input.fetchImpl,
      method: input.method,
      body: input.body,
    });
    await validateCleanupStepSuccess({
      operation: input.operation,
      response,
    });
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error: normalizeManagementError(error, input.operation),
    };
  }
}

async function requestOpenRouter(input: {
  operation: OpenRouterManagementOperation;
  path: string;
  managementApiKey: string;
  fetchImpl?: FetchImpl;
  method: 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
}): Promise<Response> {
  const fetchImpl = input.fetchImpl ?? fetch;

  let response: Response;
  try {
    response = await fetchImpl(`${OPENROUTER_MANAGEMENT_API_BASE_URL}${input.path}`, {
      method: input.method,
      headers: buildHeaders(input.managementApiKey, input.body !== undefined),
      body: input.body === undefined ? undefined : JSON.stringify(input.body),
    });
  } catch (error) {
    throw new OpenRouterManagementError({
      operation: input.operation,
      code: 'network_error',
      status: null,
      upstreamCode: null,
      message: describeUnknownError(error),
      cause: error,
    });
  }

  if (!response.ok) {
    throw await buildUpstreamHttpError(input.operation, response);
  }

  return response;
}

async function validateCleanupStepSuccess(input: {
  operation: Extract<OpenRouterManagementOperation, 'disable_key' | 'delete_key'>;
  response: Response;
}): Promise<void> {
  if (input.operation === 'disable_key') {
    const payload = await readSuccessJson(input.response, 'disable_key');
    if (
      !isRecord(payload) ||
      !isRecord(payload.data) ||
      payload.data.disabled !== true
    ) {
      throw malformedUpstreamError(
        'disable_key',
        input.response.status,
        'OpenRouter disable-key response must include data.disabled=true',
      );
    }
    return;
  }

  if (input.response.status === 204) {
    return;
  }

  const payload = await readSuccessJson(input.response, 'delete_key');
  if (!isRecord(payload) || payload.deleted !== true) {
    throw malformedUpstreamError(
      'delete_key',
      input.response.status,
      'OpenRouter delete-key response must include deleted=true',
    );
  }
}

async function buildUpstreamHttpError(
  operation: OpenRouterManagementOperation,
  response: Response,
): Promise<OpenRouterManagementError> {
  const bodyText = await response.text();
  if (!bodyText) {
    return new OpenRouterManagementError({
      operation,
      code: 'upstream_http_error',
      status: response.status,
      upstreamCode: null,
      message: `OpenRouter ${operation} request failed with status ${response.status}`,
    });
  }

  try {
    const payload = JSON.parse(bodyText) as unknown;
    if (isRecord(payload) && isRecord(payload.error)) {
      const upstreamCode =
        typeof payload.error.code === 'number' ? payload.error.code : null;
      const message =
        typeof payload.error.message === 'string'
          ? payload.error.message
          : `OpenRouter ${operation} request failed with status ${response.status}`;

      return new OpenRouterManagementError({
        operation,
        code: 'upstream_http_error',
        status: response.status,
        upstreamCode,
        message,
      });
    }
  } catch {
    // Fall back to a generic HTTP failure below.
  }

  return new OpenRouterManagementError({
    operation,
    code: 'upstream_http_error',
    status: response.status,
    upstreamCode: null,
    message: `OpenRouter ${operation} request failed with status ${response.status}`,
  });
}

async function readSuccessJson(
  response: Response,
  operation: OpenRouterManagementOperation,
): Promise<unknown> {
  const bodyText = await response.text();
  if (!bodyText) {
    throw malformedUpstreamError(
      operation,
      response.status,
      `OpenRouter ${operation} response must not be empty`,
    );
  }

  try {
    return JSON.parse(bodyText) as unknown;
  } catch {
    throw malformedUpstreamError(
      operation,
      response.status,
      `OpenRouter ${operation} response must be valid JSON`,
    );
  }
}

function buildHeaders(
  managementApiKey: string,
  includeJsonContentType: boolean,
): HeadersInit {
  return {
    Authorization: `Bearer ${managementApiKey}`,
    ...(includeJsonContentType ? { 'Content-Type': 'application/json' } : {}),
  };
}

function malformedUpstreamError(
  operation: OpenRouterManagementOperation,
  status: number,
  message: string,
): OpenRouterManagementError {
  return new OpenRouterManagementError({
    operation,
    code: 'malformed_upstream',
    status,
    upstreamCode: null,
    message,
  });
}

function normalizeManagementError(
  error: unknown,
  operation: OpenRouterManagementOperation,
): OpenRouterManagementFailureDetails {
  if (error instanceof OpenRouterManagementError) {
    return {
      operation: error.operation,
      code: error.code,
      status: error.status,
      upstreamCode: error.upstreamCode,
      message: error.message,
    };
  }

  return {
    operation,
    code: 'network_error',
    status: null,
    upstreamCode: null,
    message: describeUnknownError(error),
  };
}

function describeUnknownError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  return 'unknown OpenRouter management error';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
