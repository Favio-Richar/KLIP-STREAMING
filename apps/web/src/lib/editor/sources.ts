"use client";

import { useQueries } from "@tanstack/react-query";
import { useMemo } from "react";
import { api, unwrap } from "../api";
import type { Doc } from "./ops";

export interface SourceMedia {
  id: string;
  kind: "video" | "audio" | "image";
  url: string | null;
  durationMs: number | null;
  width: number | null;
  height: number | null;
  title: string;
  ready: boolean;
}

/** Id local del documento que no choca con ninguno existente (`^[a-z][a-z0-9_-]{0,31}$`). */
export function uniqueId(doc: Doc, prefix = "i"): string {
  const used = new Set<string>();
  for (const s of doc.sources) used.add(s.id);
  for (const t of doc.timeline.tracks) {
    used.add(t.id);
    for (const i of t.items) used.add((i as { id: string }).id);
  }
  for (;;) {
    const id = `${prefix}${Math.random().toString(36).slice(2, 8)}`;
    if (!used.has(id)) return id;
  }
}

/** URLs firmadas (proxy 720p para video) y metadatos de cada fuente del documento. */
export function useSourceMedia(orgId: string | null, doc: Doc | null): Map<string, SourceMedia> {
  const sources = doc?.sources ?? [];
  const results = useQueries({
    queries: sources.map((s) => ({
      queryKey: ["source-media", orgId, s.asset_id],
      enabled: !!orgId,
      staleTime: 10 * 60_000,
      refetchInterval: 10 * 60_000,
      queryFn: async () => {
        const asset = await unwrap(
          api.GET("/api/v1/assets/{asset_id}", { params: { path: { asset_id: s.asset_id } } }),
        );
        let url: string | null = null;
        if (asset.status === "ready") {
          const res =
            asset.kind === "video"
              ? await unwrap(
                  api.GET("/api/v1/assets/{asset_id}/derivatives/{kind}/download-url", {
                    params: { path: { asset_id: asset.id, kind: "proxy_720p" } },
                  }),
                )
              : await unwrap(
                  api.GET("/api/v1/assets/{asset_id}/download-url", {
                    params: { path: { asset_id: asset.id } },
                  }),
                );
          url = res.url;
        }
        return {
          url,
          durationMs: asset.duration_ms ?? null,
          width: asset.width ?? null,
          height: asset.height ?? null,
          title: asset.title,
          ready: asset.status === "ready",
        };
      },
    })),
  });
  const key = results.map((r) => r.dataUpdatedAt).join(",");
  return useMemo(() => {
    const map = new Map<string, SourceMedia>();
    sources.forEach((s, i) => {
      const data = results[i]?.data;
      map.set(s.id, {
        id: s.id,
        kind: s.kind,
        url: data?.url ?? null,
        durationMs: data?.durationMs ?? null,
        width: data?.width ?? null,
        height: data?.height ?? null,
        title: data?.title ?? s.id,
        ready: data?.ready ?? false,
      });
    });
    return map;
    // `key` resume los resultados; `sources` cambia solo cuando cambia el documento
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, doc?.sources]);
}
