/**
 * Historial deshacer/rehacer (F1-10), puro y sin React. Guarda documentos completos (son
 * inmutables y pequeños, con estructura compartida), así deshacer es exacto.
 *
 * `coalesceKey`: los cambios continuos (arrastrar, mover un deslizador) comparten clave y se
 * funden en UN solo paso de historial mientras lleguen seguidos, para que "deshacer" revierta el
 * gesto entero y no un fotograma del arrastre.
 */
import type { Doc } from "./ops";

export const HISTORY_LIMIT = 200;

export interface History {
  past: Doc[];
  present: Doc;
  future: Doc[];
  /** Clave del último cambio, para fundir cambios continuos. */
  lastKey: string | null;
}

export function createHistory(doc: Doc): History {
  return { past: [], present: doc, future: [], lastKey: null };
}

export function commit(h: History, next: Doc, coalesceKey: string | null = null): History {
  if (next === h.present) return h;
  const coalesce = coalesceKey !== null && coalesceKey === h.lastKey && h.past.length > 0;
  const past = coalesce ? h.past : [...h.past, h.present].slice(-HISTORY_LIMIT);
  return { past, present: next, future: [], lastKey: coalesceKey };
}

/** Cierra la fusión: el siguiente cambio, aunque tenga la misma clave, abre un paso nuevo. */
export function seal(h: History): History {
  return h.lastKey === null ? h : { ...h, lastKey: null };
}

export function undo(h: History): History {
  const previous = h.past[h.past.length - 1];
  if (previous === undefined) return h;
  return {
    past: h.past.slice(0, -1),
    present: previous,
    future: [h.present, ...h.future],
    lastKey: null,
  };
}

export function redo(h: History): History {
  const [next, ...rest] = h.future;
  if (next === undefined) return h;
  return { past: [...h.past, h.present], present: next, future: rest, lastKey: null };
}

export const canUndo = (h: History): boolean => h.past.length > 0;
export const canRedo = (h: History): boolean => h.future.length > 0;

/** Reemplaza el documento sin tocar el historial (p. ej. tras guardar y recibir el ETag nuevo). */
export function replacePresent(h: History, doc: Doc): History {
  return { ...h, present: doc };
}
