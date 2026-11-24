"use client";

import { ApiProblem, createApiClient, unwrap, type Schemas } from "@klick/client-sdk";
import { useOrgStore } from "./stores";

export const SESSION_EXPIRED_EVENT = "klick:session-expired";
export const SESSION_EXPIRED_FLAG = "klick.expired";

export const api = createApiClient({
  getOrgId: () => useOrgStore.getState().orgId,
  // La sesión murió a mitad de uso: se avisa a la guardia del área autenticada, que vuelve a
  // consultar `/me`, redirige al ingreso conservando el destino y muestra el aviso.
  onSessionExpired: () => {
    if (typeof window === "undefined") return;
    sessionStorage.setItem(SESSION_EXPIRED_FLAG, "1");
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  },
});

export { ApiProblem, unwrap };
export type { Schemas };

/** Claves de TanStack Query centralizadas para invalidar sin errores de tipeo. */
export const qk = {
  me: ["me"] as const,
  orgs: ["orgs"] as const,
  members: (org: string | null) => ["members", org] as const,
  invitations: (org: string | null) => ["invitations", org] as const,
  sessions: ["sessions"] as const,
  tokens: (org: string | null) => ["api-tokens", org] as const,
  job: (id: string) => ["job", id] as const,
};

/** Mensaje de la API → clave del catálogo de errores (`errors.<code>`), con respaldo. */
export function problemOf(err: unknown): ApiProblem | null {
  return err instanceof ApiProblem ? err : null;
}
