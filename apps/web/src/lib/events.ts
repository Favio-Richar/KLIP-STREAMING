"use client";

import { useEffect, useRef, useState } from "react";

export interface OrgEvent {
  id: string;
  type: string;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export type StreamState = "connecting" | "open" | "closed";

/** Tipos de evento que la API emite por SSE y que la interfaz sabe escuchar. */
export const EVENT_TYPES = [
  "job.created",
  "job.progress",
  "job.retrying",
  "job.succeeded",
  "job.failed",
  "job.canceled",
  "job.expired",
] as const;

export function parseEvent(raw: string): OrgEvent | null {
  try {
    const v: unknown = JSON.parse(raw);
    if (!v || typeof v !== "object") return null;
    const e = v as Record<string, unknown>;
    if (typeof e.type !== "string" || typeof e.payload !== "object" || e.payload === null)
      return null;
    return {
      id: String(e.id ?? ""),
      type: e.type,
      occurred_at: String(e.occurred_at ?? ""),
      payload: e.payload as Record<string, unknown>,
    };
  } catch {
    return null;
  }
}

/**
 * Suscripción SSE a los eventos de la organización. El navegador reconecta solo y reenvía
 * `Last-Event-ID`, de modo que no se pierden eventos durables tras un corte.
 */
export function useOrgEvents(orgId: string | null, onEvent: (e: OrgEvent) => void): StreamState {
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });
  const supported = typeof EventSource !== "undefined";
  // El estado se guarda junto a la organización a la que pertenece: al cambiar de organización
  // el estado anterior deja de valer sin necesidad de reiniciarlo dentro del efecto.
  const [conn, setConn] = useState<{ orgId: string; state: StreamState } | null>(null);

  useEffect(() => {
    if (!orgId || !supported) return;
    const source = new EventSource(`/api/v1/events/stream?org_id=${encodeURIComponent(orgId)}`);
    source.onopen = () => setConn({ orgId, state: "open" });
    source.onerror = () =>
      setConn({ orgId, state: source.readyState === EventSource.CLOSED ? "closed" : "connecting" });
    const listener = (m: MessageEvent<string>) => {
      const ev = parseEvent(m.data);
      if (ev) handler.current(ev);
    };
    for (const type of EVENT_TYPES) source.addEventListener(type, listener as EventListener);
    return () => {
      for (const type of EVENT_TYPES) source.removeEventListener(type, listener as EventListener);
      source.close();
    };
  }, [orgId, supported]);

  if (!orgId || !supported) return "closed";
  return conn && conn.orgId === orgId ? conn.state : "connecting";
}
