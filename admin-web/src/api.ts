// API 客户端：CSRF token 仅保存在内存（刷新页面后由 /auth/me 重新获取），
// 全部请求携带同源 cookie（credentials: same-origin），不使用 localStorage/sessionStorage。
import type {
  DraftPatchBody,
  DraftPayload,
  DraftResponse,
  FloorGeometryPayload,
  MePayload,
  PublishPayload,
  RollbackPayload,
  SiteSummary,
  ValidatePayload,
  VersionDetailPayload,
  VersionsPayload,
  DraftResetPayload,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  readonly details: Record<string, unknown> | null;

  constructor(
    status: number,
    code: string,
    message: string,
    retryable = false,
    details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.details = details;
  }
}

let csrfToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/** 会话失效（ADMIN_UNAUTHORIZED）时由 App 注册回调切换到登录页。 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: { csrf?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }
  if (opts.csrf) {
    if (!csrfToken) {
      throw new ApiError(0, 'NO_CSRF', '会话状态缺失，请刷新页面重新登录');
    }
    headers['X-Admin-CSRF'] = csrfToken;
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', '网络请求失败，请检查网络连接后重试', true);
  }

  let payload: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const err =
      (payload as { error?: Record<string, unknown> } | null)?.error ?? {};
    const code =
      typeof err.code === 'string' ? err.code : `HTTP_${response.status}`;
    const message =
      typeof err.message === 'string'
        ? err.message
        : `请求失败（HTTP ${response.status}）`;
    if (response.status === 401 && code === 'ADMIN_UNAUTHORIZED') {
      unauthorizedHandler?.();
    }
    throw new ApiError(
      response.status,
      code,
      message,
      err.retryable === true,
      (err.details as Record<string, unknown> | undefined) ?? null,
    );
  }
  return payload as T;
}

export const api = {
  me: () => request<MePayload>('GET', '/api/v1/admin/auth/me'),

  login: (username: string, password: string) =>
    request<MePayload>('POST', '/api/v1/admin/auth/login', { username, password }),

  changePassword: (currentPassword: string, newPassword: string, confirmPassword: string) =>
    request<MePayload>(
      'POST',
      '/api/v1/admin/auth/password',
      { currentPassword, newPassword, confirmPassword },
      { csrf: true },
    ),

  logout: () =>
    request<{ ok: boolean }>('POST', '/api/v1/admin/auth/logout', undefined, { csrf: true }),

  listSites: () =>
    request<{ items: SiteSummary[] }>('GET', '/api/v1/admin/sites'),

  listVersions: (siteId: string) =>
    request<VersionsPayload>('GET', `/api/v1/admin/sites/${encode(siteId)}/versions`),

  versionDetail: (siteId: string, version: string) =>
    request<VersionDetailPayload>(
      'GET',
      `/api/v1/admin/sites/${encode(siteId)}/versions/${encode(version)}`,
    ),

  floorGeometry: (siteId: string, version: string, floorId: string) =>
    request<FloorGeometryPayload>(
      'GET',
      `/api/v1/admin/sites/${encode(siteId)}/versions/${encode(version)}/floors/${encode(
        floorId,
      )}/geometry`,
    ),

  getDraft: (siteId: string) =>
    request<DraftResponse>('GET', `/api/v1/admin/sites/${encode(siteId)}/draft`),

  saveDraft: (siteId: string, patch: DraftPatchBody) =>
    request<DraftPayload>('PUT', `/api/v1/admin/sites/${encode(siteId)}/draft`, patch, {
      csrf: true,
    }),

  resetDraft: (siteId: string) =>
    request<DraftResetPayload>(
      'POST',
      `/api/v1/admin/sites/${encode(siteId)}/draft/reset`,
      undefined,
      { csrf: true },
    ),

  validateDraft: (siteId: string) =>
    request<ValidatePayload>(
      'POST',
      `/api/v1/admin/sites/${encode(siteId)}/draft/validate`,
      undefined,
      { csrf: true },
    ),

  publishDraft: (siteId: string) =>
    request<PublishPayload>(
      'POST',
      `/api/v1/admin/sites/${encode(siteId)}/draft/publish`,
      undefined,
      { csrf: true },
    ),

  rollback: (siteId: string, version: string) =>
    request<RollbackPayload>(
      'POST',
      `/api/v1/admin/sites/${encode(siteId)}/versions/${encode(version)}/rollback`,
      undefined,
      { csrf: true },
    ),
};
