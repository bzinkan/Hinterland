/**
 * Typed fetch wrapper around the Hinterland API.
 *
 * - Reads the bearer token from secure storage and injects it on every call.
 * - Resolves URLs against `env.apiBaseUrl` (env-switched per APP_ENV).
 * - Raises a typed `ApiError` on non-2xx, with the server's error envelope.
 */
import { env } from "@/src/config/env";
import {
  bearerTokenSnapshotIsCurrent,
  getBearerToken,
  getBearerTokenSnapshot,
} from "@/src/auth/token";
import { runImperativeRequest } from "@/src/auth/requestBoundary";

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
  if ((options.method ?? "GET") !== "GET") {
    return runImperativeRequest((boundarySignal) =>
      performApiRequest<T>(path, options, boundarySignal),
    );
  }
  return performApiRequest<T>(path, options);
}

async function performApiRequest<T>(
  path: string,
  options: RequestOptions,
  boundarySignal?: AbortSignal,
): Promise<T> {
  const combined = combineSignals(options.signal, boundarySignal);
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...options.headers,
  };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  if (!options.unauthenticated) {
    try {
      const snapshot = boundarySignal
        ? await getBearerTokenSnapshot()
        : { token: await getBearerToken(), generation: null };
      if (
        combined.signal?.aborted ||
        (snapshot.generation !== null && !bearerTokenSnapshotIsCurrent(snapshot))
      ) {
        throw abortError();
      }
      const token = snapshot.token;
      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
      }
    } catch (error) {
      combined.cleanup();
      throw error;
    }
  }

  const url = `${env.apiBaseUrl}${path}`;
  try {
    const res = await fetch(url, {
      method: options.method ?? "GET",
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: combined.signal,
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
  } finally {
    combined.cleanup();
  }
}

function combineSignals(
  first?: AbortSignal,
  second?: AbortSignal,
): { signal: AbortSignal | undefined; cleanup: () => void } {
  const signals = [first, second].filter(
    (signal): signal is AbortSignal => signal !== undefined,
  );
  if (signals.length <= 1) {
    return { signal: signals[0], cleanup: () => undefined };
  }
  if (signals[0] === signals[1]) {
    return { signal: signals[0], cleanup: () => undefined };
  }
  const controller = new AbortController();
  const abort = () => controller.abort();
  for (const signal of signals) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", abort, { once: true });
  }
  return {
    signal: controller.signal,
    cleanup: () => {
      for (const signal of signals) signal.removeEventListener("abort", abort);
    },
  };
}

function abortError(): Error {
  const error = new Error("The authenticated request was superseded.");
  error.name = "AbortError";
  return error;
}
