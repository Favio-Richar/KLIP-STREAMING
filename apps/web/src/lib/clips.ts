"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { validateEditProject } from "@klick/video-project-schema";
import { api, unwrap, type Schemas } from "./api";
import type { Doc } from "./editor/ops";

export type Clip = Schemas["ClipOut"];
export type RenderJob = Schemas["RenderJobOut"];

export const clipKeys = {
  list: (org: string | null) => ["clips", org] as const,
  one: (org: string | null, id: string) => ["clip", org, id] as const,
  project: (org: string | null, id: string) => ["edit-project", org, id] as const,
};

export function useClips(orgId: string | null) {
  return useInfiniteQuery({
    queryKey: clipKeys.list(orgId),
    enabled: !!orgId,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/clips", {
          params: { query: { limit: 24, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

export function useClip(orgId: string | null, id: string) {
  return useQuery({
    queryKey: clipKeys.one(orgId, id),
    enabled: !!orgId,
    queryFn: () =>
      unwrap(api.GET("/api/v1/clips/{clip_id}", { params: { path: { clip_id: id } } })),
  });
}

export interface LoadedProject {
  doc: Doc;
  revision: number;
}

/** El documento se valida en el borde: si el servidor enviara algo inválido, no entra al editor. */
export function parseDocument(raw: unknown): Doc {
  const parsed = validateEditProject(raw);
  if (!parsed.ok) throw new Error("invalid_document");
  return parsed.doc;
}

export function useEditProject(orgId: string | null, id: string) {
  return useQuery({
    queryKey: clipKeys.project(orgId, id),
    enabled: !!orgId,
    // el editor es dueño de su copia: no se refresca por debajo mientras se trabaja
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 0,
    queryFn: async (): Promise<LoadedProject> => {
      const res = await unwrap(
        api.GET("/api/v1/clips/{clip_id}/edit-project", { params: { path: { clip_id: id } } }),
      );
      return { doc: parseDocument(res.document), revision: res.revision };
    },
  });
}

/** Guarda con control de concurrencia: `If-Match` lleva la revisión que el editor cree vigente. */
export async function saveEditProject(
  id: string,
  doc: Doc,
  revision: number,
): Promise<LoadedProject> {
  const res = await unwrap(
    api.PATCH("/api/v1/clips/{clip_id}/edit-project", {
      params: { path: { clip_id: id }, header: { "If-Match": `"${revision}"` } },
      body: { document: doc as unknown as Record<string, unknown> },
    }),
  );
  return { doc: parseDocument(res.document), revision: res.revision };
}
