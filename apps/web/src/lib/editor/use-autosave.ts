"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";
import { ApiProblem } from "../api";
import { clipKeys, saveEditProject } from "../clips";
import { useEditor } from "./store";

export const AUTOSAVE_DELAY_MS = 1200;
export const RETRY_DELAY_MS = 5000;

/**
 * Guardado automático con control de concurrencia (docs/05): un solo guardado a la vez, `If-Match`
 * con la revisión vigente, y 412 → estado `conflict` (nadie pisa el trabajo de otro). Los cambios
 * hechos mientras se guarda dejan el editor "sucio" y se guardan en la vuelta siguiente.
 */
export function useAutosave(clipId: string, orgId: string | null): { flush: () => Promise<void> } {
  const qc = useQueryClient();
  const saving = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    const s = useEditor.getState();
    if (saving.current || s.save === "conflict" || s.save === "saved") return;
    saving.current = true;
    const doc = s.history.present;
    useEditor.getState().setSave("saving");
    try {
      const saved = await saveEditProject(clipId, doc, s.revision);
      // adopta la versión canónica del servidor solo si no hubo más cambios mientras se guardaba
      useEditor.getState().markSaved(doc, saved.revision, saved.doc);
      void qc.invalidateQueries({ queryKey: clipKeys.one(orgId, clipId) });
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 412) useEditor.getState().setSave("conflict");
      else useEditor.getState().setSave("error");
    } finally {
      saving.current = false;
    }
  }, [clipId, orgId, qc]);

  const save = useEditor((s) => s.save);
  const present = useEditor((s) => s.history.present);
  useEffect(() => {
    if (save !== "dirty" && save !== "error") return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(
      () => void flush(),
      save === "error" ? RETRY_DELAY_MS : AUTOSAVE_DELAY_MS,
    );
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [save, present, flush]);

  // Avisar antes de cerrar la pestaña con cambios sin guardar
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      const state = useEditor.getState().save;
      if (state !== "saved") e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  return { flush };
}
