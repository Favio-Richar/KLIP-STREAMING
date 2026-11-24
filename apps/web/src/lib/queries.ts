"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, qk, unwrap, type Schemas } from "./api";
import { useOrgStore } from "./stores";

export type User = Schemas["UserOut"];
export type Org = Schemas["OrgOut"];

/** Usuario autenticado. Un 401 (sesión no renovable) se refleja como `error`. */
export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: () => unwrap(api.GET("/api/v1/me")),
    retry: false,
    staleTime: 60_000,
  });
}

export function useOrgs(enabled = true) {
  return useQuery({
    queryKey: qk.orgs,
    queryFn: () => unwrap(api.GET("/api/v1/orgs")),
    enabled,
  });
}

/**
 * Organización activa: la elegida si sigue existiendo; si no, la primera. Mantiene el almacén
 * coherente cuando la organización guardada ya no pertenece al usuario.
 */
export function useActiveOrg() {
  const orgs = useOrgs();
  const orgId = useOrgStore((s) => s.orgId);
  const setOrgId = useOrgStore((s) => s.setOrgId);
  const list = orgs.data ?? [];
  const active = list.find((o) => o.id === orgId) ?? list[0] ?? null;
  useEffect(() => {
    if (orgs.isSuccess && (active?.id ?? null) !== orgId) setOrgId(active?.id ?? null);
  }, [orgs.isSuccess, active?.id, orgId, setOrgId]);
  return { ...orgs, orgs: list, active };
}

export const can = (org: Org | null, permission: string): boolean =>
  !!org && org.permissions.includes(permission);
