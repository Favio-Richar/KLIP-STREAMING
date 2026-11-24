"use client";

import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";
import { api, qk, unwrap, type Schemas } from "./api";
import { useOrgEvents, type StreamState } from "./events";
import { useOrgStore } from "./stores";

export const TERMINAL = new Set(["succeeded", "failed", "canceled", "expired"]);

export interface LiveProgress {
  progress: number;
  message: string | null;
}

/**
 * Estado de un job: la consulta HTTP es la verdad; el SSE añade progreso en vivo y dispara la
 * recarga al cambiar de estado. Si el SSE cae, el sondeo cada 4 s mantiene la pantalla al día.
 */
export function useJob(jobId: string | null): {
  job: UseQueryResult<Schemas["JobOut"]>;
  live: LiveProgress | null;
  stream: StreamState;
} {
  const qc = useQueryClient();
  const orgId = useOrgStore((s) => s.orgId);
  const [live, setLive] = useState<{ id: string; value: LiveProgress } | null>(null);

  const job = useQuery({
    queryKey: qk.job(jobId ?? ""),
    queryFn: () => fetchJob(jobId ?? ""),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && TERMINAL.has(q.state.data.status) ? false : 4000),
  });

  const stream = useOrgEvents(jobId ? orgId : null, (ev) => {
    if (!jobId || ev.payload.job_id !== jobId) return;
    if (ev.type === "job.progress") {
      const progress = Number(ev.payload.progress);
      const message = typeof ev.payload.message === "string" ? ev.payload.message : null;
      if (Number.isFinite(progress)) setLive({ id: jobId, value: { progress, message } });
    } else {
      void qc.invalidateQueries({ queryKey: qk.job(jobId) });
    }
  });

  return { job, live: live && live.id === jobId ? live.value : null, stream };
}

function fetchJob(id: string) {
  return unwrap(api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: id } } }));
}
