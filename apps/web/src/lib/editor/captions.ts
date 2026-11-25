/**
 * Subtítulos manuales: reglas y operaciones puras sobre la lista de cues. Reflejan
 * `klick/captions/cues.py` del servidor (la API es la autoridad; aquí se valida para avisar antes).
 */
export interface Cue {
  start_ms: number;
  end_ms: number;
  text: string;
  words?: Array<{ start_ms: number; end_ms: number; text: string }>;
}

export const MAX_CUES = 1000;
export const MAX_TEXT_CHARS = 200;
export const MAX_LINES = 2;
export const MAX_LINE_CHARS = 60;
export const DEFAULT_CUE_MS = 2000;
export const MIN_CUE_MS = 300;

export type CueIssueCode =
  | "empty_text"
  | "text_too_long"
  | "invalid_range"
  | "cues_overlap"
  | "too_many_lines"
  | "line_too_long";

export interface CueIssue {
  code: CueIssueCode;
  index: number;
}

/** Texto como lo normaliza el servidor: líneas recortadas y sin espacios alrededor. */
export function normalizeText(text: string): string {
  return text
    .replace(/\t/g, " ")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((l) => l.trim())
    .join("\n")
    .trim();
}

export function validateCues(cues: Cue[]): CueIssue[] {
  const issues: CueIssue[] = [];
  let previousEnd = 0;
  cues.forEach((cue, index) => {
    const text = normalizeText(cue.text);
    if (!text) issues.push({ code: "empty_text", index });
    if (text.length > MAX_TEXT_CHARS) issues.push({ code: "text_too_long", index });
    if (cue.end_ms <= cue.start_ms) issues.push({ code: "invalid_range", index });
    if (cue.start_ms < previousEnd) issues.push({ code: "cues_overlap", index });
    previousEnd = Math.max(previousEnd, cue.end_ms);
    const lines = text.split("\n");
    if (lines.length > MAX_LINES) issues.push({ code: "too_many_lines", index });
    else if (lines.some((l) => l.length > MAX_LINE_CHARS)) {
      issues.push({ code: "line_too_long", index });
    }
  });
  if (cues.length > MAX_CUES) issues.push({ code: "too_many_lines", index: 0 });
  return issues;
}

/** Cue activo en `t` (inicio incluido, fin excluido). */
export function cueAt(cues: Cue[], t: number): Cue | null {
  return cues.find((c) => t >= c.start_ms && t < c.end_ms) ?? null;
}

/**
 * Inserta un cue en `atMs` sin solaparse. Si el cabezal cae dentro de uno existente, empieza al
 * terminar ese. Devuelve `null` si no hay hueco de al menos `MIN_CUE_MS`.
 */
export function insertCue(
  cues: Cue[],
  atMs: number,
  totalMs: number,
  text = "",
): { cues: Cue[]; index: number } | null {
  let start = Math.max(0, Math.round(atMs));
  const containing = cues.find((c) => start >= c.start_ms && start < c.end_ms);
  if (containing) start = containing.end_ms;
  const next = cues.find((c) => c.start_ms >= start);
  const limit = Math.min(next ? next.start_ms : totalMs, totalMs);
  const end = Math.min(start + DEFAULT_CUE_MS, limit);
  if (end - start < MIN_CUE_MS || cues.length >= MAX_CUES) return null;
  const created: Cue = { start_ms: start, end_ms: end, text };
  const index = cues.findIndex((c) => c.start_ms > start);
  const at = index === -1 ? cues.length : index;
  return { cues: [...cues.slice(0, at), created, ...cues.slice(at)], index: at };
}

export function removeCue(cues: Cue[], index: number): Cue[] {
  return cues.filter((_, i) => i !== index);
}

export function updateCue(cues: Cue[], index: number, patch: Partial<Cue>): Cue[] {
  return cues.map((c, i) => {
    if (i !== index) return c;
    const next = { ...c, ...patch };
    // al editar el texto o los tiempos, los tiempos por palabra ya no son fiables
    if ("text" in patch || "start_ms" in patch || "end_ms" in patch) delete next.words;
    return next;
  });
}

/** Lo que se envía al servidor: texto normalizado. */
export function toPayload(cues: Cue[]): Cue[] {
  return cues.map((c) => ({ ...c, text: normalizeText(c.text) }));
}
