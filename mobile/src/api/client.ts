/**
 * Typed fetch wrapper around the Hinterland API.
 *
 * - Reads the bearer token from secure storage and injects it on every call.
 * - Resolves URLs against `env.apiBaseUrl` (env-switched per APP_ENV).
 * - Raises a typed `ApiError` on non-2xx, with the server's error envelope.
 */
import { env } from "@/src/config/env";
import { getBearerToken } from "@/src/auth/token";

export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    request_id: string | null;
    details?: unknown;
  };
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: ApiErrorBody | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Method = "GET" | "POST" | "PATCH" | "DELETE";

type RequestOptions = {
  method?: Method;
  body?: unknown;
  signal?: AbortSignal;
  /** Skip the bearer-token header (for /health, /ready, /v1/meta). */
  unauthenticated?: boolean;
  /** Extra request headers (e.g. the dev-login shared key). */
  headers?: Record<string, string>;
};

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...options.headers,
  };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  if (!options.unauthenticated) {
    const token = await getBearerToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
  }

  const url = `${env.apiBaseUrl}${path}`;
  const res = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  });

  if (!res.ok) {
    let body: ApiErrorBody | null = null;
    try {
      body = (await res.json()) as ApiErrorBody;
    } catch {
      // Non-JSON response (proxy 502, etc.) -- leave body null.
    }
    const message = body?.error?.message ?? `${options.method ?? "GET"} ${path} -> ${res.status}`;
    throw new ApiError(res.status, body, message);
  }

  // 204 No Content
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}
