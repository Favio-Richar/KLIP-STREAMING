"use client";

/**
 * Estado de los subtítulos del clip abierto. Tiene su propio guardado (la pista es otro recurso
 * con su propia revisión), pero el editor lo vacía junto con el documento antes de exportar.
 */
import { create } from "zustand";
import { validateCues, type Cue } from "./captions";

export type CaptionSave = "saved" | "dirty" | "saving" | "error" | "conflict" | "invalid";

interface CaptionsState {
  clipId: string | null;
  trackId: string | null;
  revision: number;
  cues: Cue[];
  save: CaptionSave;

  load: (clipId: string, track: { id: string; revision: number; cues: Cue[] } | null) => void;
  attach: (track: { id: string; revision: number; cues: Cue[] }) => void;
  edit: (fn: (cues: Cue[]) => Cue[]) => void;
  markSaved: (sent: Cue[], revision: number) => void;
  setSave: (s: CaptionSave) => void;
}

export const useCaptions = create<CaptionsState>((set, get) => ({
  clipId: null,
  trackId: null,
  revision: 1,
  cues: [],
  save: "saved",

  load: (clipId, track) =>
    set({
      clipId,
      trackId: track?.id ?? null,
      revision: track?.revision ?? 1,
      cues: track?.cues ?? [],
      save: "saved",
    }),
  attach: (track) =>
    set({ trackId: track.id, revision: track.revision, cues: track.cues, save: "saved" }),
  edit: (fn) => {
    const { cues, save } = get();
    const next = fn(cues);
    if (next === cues) return;
    set({
      cues: next,
      save: save === "conflict" ? "conflict" : validateCues(next).length ? "invalid" : "dirty",
    });
  },
  // Si se editó mientras se guardaba, `cues` ya no es lo enviado: queda sucio para otra vuelta.
  markSaved: (sent, revision) =>
    set((s) => ({ revision, save: s.cues === sent ? "saved" : "dirty" })),
  setSave: (save) => set({ save }),
}));
