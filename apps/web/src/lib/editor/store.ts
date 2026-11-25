"use client";

/**
 * Estado del editor (F1-10). El documento y su historial viven aquí; todo lo demás (cabezal,
 * selección, zoom) es estado de interfaz. Nada de esto sustituye al servidor: la fuente de verdad
 * es la revisión guardada en PostgreSQL, a la que se llega por guardado automático con `If-Match`.
 */
import { create } from "zustand";
import {
  canRedo,
  canUndo,
  commit,
  createHistory,
  redo as redoHistory,
  replacePresent,
  seal,
  undo as undoHistory,
  type History,
} from "./history";
import type { Doc } from "./ops";

export type SaveState = "saved" | "dirty" | "saving" | "error" | "conflict";

export const MIN_ZOOM = 0.005; // px por ms
export const MAX_ZOOM = 1;

interface EditorState {
  /** Clip cargado (evita mostrar el documento de otro clip mientras se carga). */
  clipId: string | null;
  history: History;
  /** Revisión del servidor que este editor cree vigente (para `If-Match`). */
  revision: number;
  save: SaveState;
  playheadMs: number;
  playing: boolean;
  selectedId: string | null;
  /** Píxeles por milisegundo de la línea de tiempo. */
  zoom: number;
  snapping: boolean;
  /** Marcadores de la sesión: no forman parte del documento (aún no existen en el esquema). */
  markers: number[];
  inMs: number | null;
  outMs: number | null;

  load: (clipId: string, doc: Doc, revision: number) => void;
  /** Aplica un cambio al documento. `key` funde cambios continuos en un paso de historial. */
  apply: (fn: (doc: Doc) => Doc, key?: string | null) => void;
  sealHistory: () => void;
  undo: () => void;
  redo: () => void;
  markSaved: (sent: Doc, revision: number, canonical: Doc) => void;
  setSave: (s: SaveState) => void;
  seek: (ms: number) => void;
  setPlaying: (p: boolean) => void;
  select: (id: string | null) => void;
  setZoom: (z: number) => void;
  toggleSnapping: () => void;
  toggleMarker: (ms: number) => void;
  setIn: (ms: number | null) => void;
  setOut: (ms: number | null) => void;
}

const clampZoom = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

export const useEditor = create<EditorState>((set, get) => ({
  clipId: null,
  history: createHistory(null as unknown as Doc),
  revision: 0,
  save: "saved",
  playheadMs: 0,
  playing: false,
  selectedId: null,
  zoom: 0.08,
  snapping: true,
  markers: [],
  inMs: null,
  outMs: null,

  load: (clipId, doc, revision) =>
    set({
      clipId,
      history: createHistory(doc),
      revision,
      save: "saved",
      playheadMs: 0,
      playing: false,
      selectedId: null,
      markers: [],
      inMs: null,
      outMs: null,
    }),

  apply: (fn, key = null) => {
    const { history, save } = get();
    const next = fn(history.present);
    if (next === history.present) return;
    set({
      history: commit(history, next, key),
      save: save === "conflict" ? "conflict" : "dirty",
    });
  },
  sealHistory: () => set((s) => ({ history: seal(s.history) })),
  undo: () =>
    set((s) =>
      canUndo(s.history)
        ? { history: undoHistory(s.history), save: s.save === "conflict" ? "conflict" : "dirty" }
        : s,
    ),
  redo: () =>
    set((s) =>
      canRedo(s.history)
        ? { history: redoHistory(s.history), save: s.save === "conflict" ? "conflict" : "dirty" }
        : s,
    ),
  // Tras guardar: `sent` es lo que se envió y `canonical` la versión que devuelve el servidor
  // (defaults aplicados). Si el usuario siguió editando mientras se guardaba, `present` ya no es
  // `sent`: se queda "sucio" y el siguiente guardado lleva el resto.
  markSaved: (sent, revision, canonical) =>
    set((s) =>
      s.history.present === sent
        ? { revision, save: "saved", history: replacePresent(s.history, canonical) }
        : { revision, save: "dirty" },
    ),
  setSave: (save) => set({ save }),
  seek: (ms) => set({ playheadMs: Math.max(0, Math.round(ms)) }),
  setPlaying: (playing) => set({ playing }),
  select: (selectedId) => set({ selectedId }),
  setZoom: (z) => set({ zoom: clampZoom(z) }),
  toggleSnapping: () => set((s) => ({ snapping: !s.snapping })),
  toggleMarker: (ms) =>
    set((s) => {
      const at = Math.round(ms);
      const has = s.markers.some((m) => Math.abs(m - at) < 50);
      return {
        markers: has
          ? s.markers.filter((m) => Math.abs(m - at) >= 50)
          : [...s.markers, at].sort((a, b) => a - b),
      };
    }),
  setIn: (inMs) => set({ inMs }),
  setOut: (outMs) => set({ outMs }),
}));

export const selectDoc = (s: EditorState): Doc => s.history.present;
