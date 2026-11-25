"use client";

import { useCallback, useEffect, useRef } from "react";
import { ApiProblem } from "../api";
import { saveCaptionCues } from "../clips";
import { toPayload, validateCues } from "./captions";
import { useCaptions } from "./captions-store";

export const CAPTIONS_AUTOSAVE_MS = 1000;
export const CAPTIONS_RETRY_MS = 5000;

/** Guardado automático de los subtítulos; no guarda mientras haya errores de validación. */
export function useCaptionsAutosave(): { flush: () => Promise<boolean> } {
  const inFlight = useRef<Promise<void> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const saveOnce = useCallback(async () => {
    const s = useCaptions.getState();
    if (!s.trackId) return;
    if (s.save !== "dirty" && s.save !== "error") return;
    if (validateCues(s.cues).length) {
      useCaptions.getState().setSave("invalid");
      return;
    }
    const sent = s.cues;
    useCaptions.getState().setSave("saving");
    try {
      const saved = await saveCaptionCues(s.trackId, toPayload(sent), s.revision);
      useCaptions.getState().markSaved(sent, saved.revision);
    } catch (err) {
      const status = err instanceof ApiProblem ? err.status : 0;
      // 412: otra sesión guardó antes; 422: el servidor no acepta el contenido (no se reintenta)
      useCaptions
        .getState()
        .setSave(status === 412 ? "conflict" : status === 422 ? "invalid" : "error");
    }
  }, []);

  /**
   * Guarda lo pendiente y devuelve `true` solo si al terminar los subtítulos están guardados.
   * Si ya hay un guardado en curso lo espera y comprueba de nuevo (no se salta ediciones).
   */
  const flush = useCallback(async (): Promise<boolean> => {
    for (let round = 0; round < 3; round++) {
      if (inFlight.current) await inFlight.current;
      const state = useCaptions.getState();
      if (!state.trackId || state.save === "saved") return true;
      if (state.save !== "dirty" && state.save !== "error") return false; // invalid / conflict
      const run = saveOnce().finally(() => {
        inFlight.current = null;
      });
      inFlight.current = run;
      await run;
    }
    return useCaptions.getState().save === "saved";
  }, [saveOnce]);

  const save = useCaptions((s) => s.save);
  const cues = useCaptions((s) => s.cues);
  useEffect(() => {
    if (save !== "dirty" && save !== "error") return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(
      () => void flush(),
      save === "error" ? CAPTIONS_RETRY_MS : CAPTIONS_AUTOSAVE_MS,
    );
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [save, cues, flush]);

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (useCaptions.getState().save !== "saved") e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  return { flush };
}
