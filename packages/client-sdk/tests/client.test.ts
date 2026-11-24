import { describe, expect, it, vi } from "vitest";
import { ApiProblem, createApiClient, unwrap } from "../src";

const BASE = "http://api.test";
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": status >= 400 ? "application/problem+json" : "application/json",
      ...headers,
    },
  });

function setup(
  handler: (req: Request) => Response | Promise<Response>,
  org: string | null = "org-1",
  hasSession = true,
) {
  const seen: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(input, init);
    seen.push(req.clone());
    return handler(req);
  });
  const onSessionExpired = vi.fn();
  const client = createApiClient({
    baseUrl: BASE,
    fetch: fetchMock as unknown as typeof fetch,
    getOrgId: () => org,
    getCookie: (n) => (n === "klick_csrf" && hasSession ? "csrf-123" : undefined),
    onSessionExpired,
  });
  return { client, seen, fetchMock, onSessionExpired };
}

describe("cabeceras", () => {
  it("envía X-Org-ID siempre y X-CSRF-Token solo en métodos que escriben", async () => {
    const { client, seen } = setup(() => json({ items: [] }));
    await client.GET("/api/v1/members");
    await client.POST("/api/v1/invitations", { body: { email: "a@b.cl", role: "viewer" } });
    expect(seen[0]!.headers.get("x-org-id")).toBe("org-1");
    expect(seen[0]!.headers.get("x-csrf-token")).toBeNull();
    expect(seen[1]!.headers.get("x-csrf-token")).toBe("csrf-123");
    expect(seen[1]!.headers.get("x-org-id")).toBe("org-1");
  });

  it("omite X-Org-ID cuando no hay organización activa", async () => {
    const { client, seen } = setup(() => json([]), null);
    await client.GET("/api/v1/orgs");
    expect(seen[0]!.headers.get("x-org-id")).toBeNull();
  });
});

describe("renovación de sesión", () => {
  it("ante 401 renueva una vez y reintenta la petición original con su cuerpo", async () => {
    let calls = 0;
    const { client, seen } = setup((req) => {
      const path = new URL(req.url).pathname;
      if (path === "/api/v1/auth/refresh") return json({ ok: true });
      calls += 1;
      return calls === 1
        ? json({ code: "unauthorized", title: "x" }, 401)
        : json({ id: "i1" }, 200);
    });
    const res = await client.POST("/api/v1/invitations", {
      body: { email: "a@b.cl", role: "viewer" },
    });
    expect(res.response.status).toBe(200);
    const paths = seen.map((r) => new URL(r.url).pathname);
    expect(paths).toEqual(["/api/v1/invitations", "/api/v1/auth/refresh", "/api/v1/invitations"]);
    expect(await seen[2]!.text()).toContain("a@b.cl");
    expect(seen[1]!.headers.get("x-csrf-token")).toBe("csrf-123");
  });

  it("peticiones concurrentes con 401 comparten un único refresco", async () => {
    let refreshes = 0;
    const authed = new Set<string>();
    const { client } = setup(async (req) => {
      const path = new URL(req.url).pathname;
      if (path === "/api/v1/auth/refresh") {
        refreshes += 1;
        await new Promise((r) => setTimeout(r, 10));
        authed.add("ok");
        return json({ ok: true });
      }
      return authed.size ? json([]) : json({ code: "unauthorized", title: "x" }, 401);
    });
    const all = await Promise.all([
      client.GET("/api/v1/members"),
      client.GET("/api/v1/orgs"),
      client.GET("/api/v1/me/sessions"),
    ]);
    expect(all.every((r) => r.response.ok)).toBe(true);
    expect(refreshes).toBe(1);
  });

  it("si el refresco falla avisa de la sesión caducada y devuelve el 401", async () => {
    const { client, onSessionExpired } = setup((req) =>
      json(
        { code: "unauthorized", title: "x" },
        new URL(req.url).pathname.endsWith("refresh") ? 401 : 401,
      ),
    );
    const res = await client.GET("/api/v1/members");
    expect(res.response.status).toBe(401);
    expect(onSessionExpired).toHaveBeenCalledTimes(1);
  });

  it("un visitante sin sesión recibe el 401 sin aviso de sesión caducada", async () => {
    const { client, onSessionExpired } = setup(
      () => json({ code: "unauthorized", title: "x" }, 401),
      null,
      false,
    );
    const res = await client.GET("/api/v1/me");
    expect(res.response.status).toBe(401);
    expect(onSessionExpired).not.toHaveBeenCalled();
  });

  it("no intenta renovar en los endpoints de acceso (login con contraseña errónea)", async () => {
    const { client, seen, onSessionExpired } = setup(() =>
      json({ code: "invalid_credentials", title: "x" }, 401),
    );
    await client.POST("/api/v1/auth/login", {
      body: { email: "a@b.cl", password: "mala-clave-123" },
    });
    expect(seen).toHaveLength(1);
    expect(onSessionExpired).not.toHaveBeenCalled();
  });
});

describe("errores problem+json", () => {
  it("unwrap lanza ApiProblem con código, detalle y errores por campo", async () => {
    const { client } = setup(() =>
      json(
        {
          code: "validation_error",
          title: "Validation failed",
          detail: "Revisa los datos.",
          request_id: "req-9",
          errors: [{ field: "email", message: "correo inválido" }],
        },
        422,
      ),
    );
    const err = await unwrap(client.GET("/api/v1/members")).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiProblem);
    const p = err as ApiProblem;
    expect([p.status, p.code, p.detail, p.requestId, p.fields.email]).toEqual([
      422,
      "validation_error",
      "Revisa los datos.",
      "req-9",
      "correo inválido",
    ]);
  });

  it("unwrap devuelve undefined en un 204", async () => {
    const { client } = setup(() => new Response(null, { status: 204 }));
    await expect(unwrap(client.POST("/api/v1/auth/logout"))).resolves.toBeUndefined();
  });
});
