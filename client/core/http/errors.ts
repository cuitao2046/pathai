/**
 * 错误模型：ApiClientError（客户端按稳定 code 分支，不依赖 message）
 * 与服务端错误信封解析（backend/pathai_backend/errors.py 对齐）。
 */
import type { ApiErrorCode, ApiErrorEnvelope } from '../../typings/api';

/** 401 时需要作废令牌、重登并重放一次的错误码（docs/24 §8 认证规则）。 */
export const AUTH_RETRYABLE_CODES: ReadonlySet<string> = new Set([
  'UNAUTHENTICATED',
  'TOKEN_EXPIRED',
]);

export interface ApiClientErrorInit {
  code: ApiErrorCode;
  message: string;
  retryable?: boolean;
  statusCode?: number | null;
  requestId?: string | null;
  details?: Record<string, unknown>;
}

export class ApiClientError extends Error {
  readonly code: ApiErrorCode;
  readonly retryable: boolean;
  readonly statusCode: number | null;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;

  constructor(init: ApiClientErrorInit) {
    super(init.message);
    this.name = 'ApiClientError';
    this.code = init.code;
    this.retryable = init.retryable ?? false;
    this.statusCode = init.statusCode ?? null;
    this.requestId = init.requestId ?? null;
    this.details = init.details ?? {};
  }
}

/** 供错误解析的响应切片。 */
export interface ErrorSource {
  statusCode: number;
  data: unknown;
  requestId: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/** 从响应体提取错误信封的 code；无法解析返回 null。 */
export function extractErrorCode(data: unknown): string | null {
  if (!isRecord(data) || !isRecord(data.error)) {
    return null;
  }
  const code = (data.error as Record<string, unknown>).code;
  return typeof code === 'string' ? code : null;
}

/**
 * 响应（>=400）→ ApiClientError。
 * 信封缺失/损坏时退化为 HTTP_ERROR，保留状态码与 requestId。
 */
export function parseErrorEnvelope(source: ErrorSource): ApiClientError {
  const body = source.data as Partial<ApiErrorEnvelope> | null;
  const error = isRecord(body) && isRecord(body.error) ? body.error : null;
  if (error === null || typeof error.code !== 'string') {
    return new ApiClientError({
      code: 'HTTP_ERROR',
      message: `HTTP ${source.statusCode}`,
      retryable: source.statusCode >= 500,
      statusCode: source.statusCode,
      requestId: source.requestId,
    });
  }
  return new ApiClientError({
    code: error.code as ApiErrorCode,
    message: typeof error.message === 'string' ? error.message : `HTTP ${source.statusCode}`,
    retryable: error.retryable === true || source.statusCode >= 500,
    statusCode: source.statusCode,
    requestId: typeof body?.requestId === 'string' ? body.requestId : source.requestId,
    details: isRecord(error.details) ? error.details : {},
  });
}
