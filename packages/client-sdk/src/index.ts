import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type { components, paths } from "./schema";
export type Schemas = components["schemas"];

export const CSRF_COOKIE = "klick_csrf";
export const CSRF_HEADER = "X-CSRF-Token";
export const ORG_HEADER = "X-Org-ID";
const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

/** Error de la API (`application/problem+json`, RFC 9457). */
export class ApiProblem extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string | undefined;
  readonly requestId: string | undefined;
  readonly fields: Record<string, string>;

  constructor(status: number, body: unknown) {
    const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
    super(typeof b.title === "string" ? b.title : `HTTP ${status}`);
    this.name = "ApiProblem";
    this.status = status;
    this.code = typeof b.code === "string" ? b.code : `http_${status}`;
    this.detail = typeof b.detail === "string" ? b.detail : undefined;
    this.requestId = typeof b.request_id === "string" ? b.request_id : undefined;
    this.fields = {};
    if (Array.isArray(b.errors)) {
      for (const e of b.errors as Array<Record<string, unknown>>) {
        if (typeof e.field === "string" && e.field && typeof e.message === "string") {
          this.fields[e.field] ??= e.message;
        }
      }
    }
  }
}

export interface ClientOptions {
  baseUrl?: string;
  /** Organización activa; se envía como `X-Org-ID`. */
  getOrgId?: () => string | null | undefined;
  /** Se llama cuando la sesión no se puede renovar (hay que volver a iniciar sesión). */
  onSessionExpired?: () => void;
  fetch?: typeof fetch;
  /** Lectura de cookies (inyectable en pruebas). */
  getCookie?: (name: string) => string | undefined;
}

function browserCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  for (const part of document.cookie.split("; ")) {
    const eq = part.indexOf("=");
    if (eq > 0 && part.slice(0, eq) === name) return decodeURIComponent(part.slice(eq + 1));
  }
  return undefined;
}

const NO_REFRESH = /\/api\/v1\/auth\/(login|register|refresh|logout|password|email|mfa\/verify)/;

export function createApiClient(options: ClientOptions = {}) {
  const baseFetch: typeof fetch = options.fetch ?? ((...a) => fetch(...a));
  const cookie = options.getCookie ?? browserCookie;
  let refreshing: Promise<boolean> | null = null;

  /** Un único refresco a la vez: las peticiones concurrentes esperan al mismo. */
  const refresh = (): Promise<boolean> => {
    refreshing ??= (async () => {
      try {
        const csrf = cookie(CSRF_COOKIE);
        const res = await baseFetch(
          new Request(`${options.baseUrl ?? ""}/api/v1/auth/refresh`, {
            method: "POST",
            credentials: "include",
            headers: csrf ? { [CSRF_HEADER]: csrf } : {},
          }),
        );
        return res.ok;
      } catch {
        return false;
      } finally {
        setTimeout(() => (refreshing = null), 0);
      }
    })();
    return refreshing;
  };

  const signed = (req: Request): Request => {
    if (!SAFE.has(req.method.toUpperCase())) {
      const csrf = cookie(CSRF_COOKIE);
      if (csrf) req.headers.set(CSRF_HEADER, csrf);
    }
    const org = options.getOrgId?.();
    if (org && !req.headers.has(ORG_HEADER)) req.headers.set(ORG_HEADER, org);
    return req;
  };

  const customFetch = async (input: Request): Promise<Response> => {
    const retry = input.clone();
    // La cookie CSRF dura lo que la sesión: sin ella nunca hubo sesión (visitante anónimo).
    const hadSession = !!cookie(CSRF_COOKIE);
    const res = await baseFetch(signed(input));
    if (res.status !== 401 || NO_REFRESH.test(new URL(input.url, "http://x").pathname)) return res;
    if (await refresh()) return baseFetch(signed(retry));
    if (hadSession) options.onSessionExpired?.();
    return res;
  };

  return createClient<paths>({
    baseUrl: options.baseUrl ?? "",
    credentials: "include",
    fetch: customFetch,
  });
}

export type ApiClient = ReturnType<typeof createApiClient>;

/** Desenvuelve `{data, error}` de openapi-fetch: devuelve `data` o lanza `ApiProblem`. */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  if (response.ok) return undefined as T; // 204 sin cuerpo
  throw new ApiProblem(response.status, error);
}
