"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap, type Schemas } from "./api";

export type Asset = Schemas["AssetOut"];
export type Tag = Schemas["TagOut"];

export interface AssetFilters {
  q: string;
  kind: "" | "video" | "audio" | "image";
  status: "" | "uploading" | "processing" | "ready" | "failed";
  tagIds: string[];
}

export const EMPTY_FILTERS: AssetFilters = { q: "", kind: "", status: "", tagIds: [] };
export const PAGE_SIZE = 24;
/** Mientras haya archivos en proceso, las pantallas se refrescan solas. */
export const PENDING = new Set(["uploading", "processing"]);

export const hasFilters = (f: AssetFilters): boolean =>
  !!(f.q.trim() || f.kind || f.status || f.tagIds.length);

/** Valor que se actualiza tras `delay` ms sin cambios (búsqueda sin una petición por tecla). */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

export function useAssetList(orgId: string | null, f: AssetFilters) {
  return useInfiniteQuery({
    queryKey: ["assets", orgId, f],
    enabled: !!orgId,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/assets", {
          params: {
            query: {
              limit: PAGE_SIZE,
              ...(pageParam ? { cursor: pageParam } : {}),
              ...(f.q.trim() ? { q: f.q.trim() } : {}),
              ...(f.kind ? { kind: f.kind } : {}),
              ...(f.status ? { status: f.status } : {}),
              ...(f.tagIds.length ? { tag_id: f.tagIds } : {}),
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: (q) =>
      q.state.data?.pages.some((p) => p.items.some((a) => PENDING.has(a.status))) ? 3000 : false,
  });
}

export function useAsset(orgId: string | null, id: string) {
  return useQuery({
    queryKey: ["asset", orgId, id],
    enabled: !!orgId,
    queryFn: () =>
      unwrap(api.GET("/api/v1/assets/{asset_id}", { params: { path: { asset_id: id } } })),
    refetchInterval: (q) => (q.state.data && PENDING.has(q.state.data.status) ? 3000 : false),
    retry: (count, err) => !(err instanceof Error && err.name === "ApiProblem") && count < 2,
  });
}

export function useTags(orgId: string | null) {
  return useQuery({
    queryKey: ["tags", orgId],
    enabled: !!orgId,
    queryFn: () => unwrap(api.GET("/api/v1/tags")),
  });
}

/** URL firmada para reproducir: el proxy de un video, o el original de audio e imágenes.
 * Dura 15 minutos; se refresca antes de que caduque. */
export function usePlaybackUrl(orgId: string | null, asset: Asset | undefined) {
  const ready = asset?.status === "ready";
  return useQuery({
    queryKey: ["playback", orgId, asset?.id, asset?.kind],
    enabled: !!orgId && ready,
    staleTime: 10 * 60_000,
    refetchInterval: 10 * 60_000,
    queryFn: async () => {
      const id = (asset as Asset).id;
      const res =
        (asset as Asset).kind === "video"
          ? await unwrap(
              api.GET("/api/v1/assets/{asset_id}/derivatives/{kind}/download-url", {
                params: { path: { asset_id: id, kind: "proxy_720p" } },
              }),
            )
          : await unwrap(
              api.GET("/api/v1/assets/{asset_id}/download-url", {
                params: { path: { asset_id: id } },
              }),
            );
      return res.url;
    },
  });
}
