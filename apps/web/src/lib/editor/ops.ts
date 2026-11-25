/**
 * Operaciones puras sobre el documento de edición (F1-10). Son inmutables, no tocan el DOM y no
 * dependen de React: el editor, los atajos y las pruebas las usan tal cual. Toda operación devuelve
 * un documento NUEVO (o el mismo si no hubo cambio) y respeta las reglas semánticas del servidor
 * (sin solapes en una pista, duración que cubre todos los elementos).
 *
 * Unidades: milisegundos enteros. Con `speed`, un tramo de fuente dura `Δfuente / speed` en la
 * línea de tiempo (igual que `effectiveMs` del paquete de esquema, que es la fuente de verdad).
 */
import { effectiveMs, type EditProject } from "@klick/video-project-schema";

export type Doc = EditProject;
export type Track = Doc["timeline"]["tracks"][number];
export type Item = Track["items"][number];
type MediaItem = Extract<Track, { kind: "video" | "audio" }>["items"][number];

export const MIN_ITEM_MS = 100;

export function isMediaItem(track: Track): track is Extract<Track, { kind: "video" | "audio" }> {
  return track.kind === "video" || track.kind === "audio";
}

/** Inicio y fin de un elemento en la línea de tiempo. */
export function span(track: Track, item: Item): { start: number; end: number } {
  if (isMediaItem(track)) {
    const media = item as MediaItem;
    return { start: media.start_ms, end: media.start_ms + effectiveMs(media) };
  }
  const timed = item as { start_ms: number; end_ms: number };
  return { start: timed.start_ms, end: timed.end_ms };
}

export interface Located {
  trackIndex: number;
  itemIndex: number;
  track: Track;
  item: Item;
}

export function find(doc: Doc, itemId: string): Located | null {
  for (const [trackIndex, track] of doc.timeline.tracks.entries()) {
    const itemIndex = (track.items as Array<{ id: string }>).findIndex((i) => i.id === itemId);
    if (itemIndex >= 0) {
      return { trackIndex, itemIndex, track, item: track.items[itemIndex] as Item };
    }
  }
  return null;
}

/** Duración total = el fin más tardío de cualquier elemento (mínimo 1 ms). */
export function contentDuration(doc: Doc): number {
  let end = 0;
  for (const track of doc.timeline.tracks) {
    for (const item of track.items) end = Math.max(end, span(track, item).end);
  }
  return Math.max(1, end);
}

function replaceItem(doc: Doc, loc: Located, next: Item | null | Item[]): Doc {
  const tracks = doc.timeline.tracks.map((track, ti) => {
    if (ti !== loc.trackIndex) return track;
    const items = [...track.items] as Item[];
    const replacement = next === null ? [] : Array.isArray(next) ? next : [next];
    items.splice(loc.itemIndex, 1, ...replacement);
    items.sort((a, b) => span(track, a).start - span(track, b).start);
    return { ...track, items } as Track;
  });
  const timeline = { ...doc.timeline, tracks };
  const next2 = { ...doc, timeline } as Doc;
  return { ...next2, timeline: { ...timeline, duration_ms: contentDuration(next2) } };
}

/** Vecinos del elemento en su pista (para impedir solapes). */
function neighbours(track: Track, itemId: string): { prevEnd: number; nextStart: number } {
  let prevEnd = 0;
  let nextStart = Number.POSITIVE_INFINITY;
  const me = (track.items as Array<{ id: string }>).find((i) => i.id === itemId);
  if (!me) return { prevEnd, nextStart };
  const mine = span(track, me as Item);
  for (const other of track.items) {
    if ((other as { id: string }).id === itemId) continue;
    const s = span(track, other);
    if (s.end <= mine.start) prevEnd = Math.max(prevEnd, s.end);
    else if (s.start >= mine.end) nextStart = Math.min(nextStart, s.start);
  }
  return { prevEnd, nextStart };
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

// ---------------------------------------------------------------------------------- mover

/** Mueve el elemento a `startMs` sin salir de sus vecinos ni de 0. */
export function moveItem(doc: Doc, itemId: string, startMs: number): Doc {
  const loc = find(doc, itemId);
  if (!loc) return doc;
  const { start, end } = span(loc.track, loc.item);
  const length = end - start;
  const { prevEnd, nextStart } = neighbours(loc.track, itemId);
  const target = Math.round(clamp(startMs, prevEnd, Math.max(prevEnd, nextStart - length)));
  if (target === start) return doc;
  const moved = { ...loc.item, start_ms: target } as Item;
  if (!isMediaItem(loc.track)) (moved as { end_ms: number }).end_ms = target + length;
  return replaceItem(doc, loc, moved);
}

// ---------------------------------------------------------------------------------- recortar

export interface TrimLimits {
  /** Duración de la fuente (ms); impide recortar más allá del archivo. */
  sourceDurationMs?: number | undefined;
}

/** Recorta por el borde izquierdo: el nuevo inicio en la línea de tiempo. */
export function trimStart(
  doc: Doc,
  itemId: string,
  newStartMs: number,
  _limits: TrimLimits = {},
): Doc {
  const loc = find(doc, itemId);
  if (!loc) return doc;
  const { start, end } = span(loc.track, loc.item);
  const { prevEnd } = neighbours(loc.track, itemId);
  let target = Math.round(clamp(newStartMs, prevEnd, end - MIN_ITEM_MS));
  if (isMediaItem(loc.track)) {
    const media = loc.item as MediaItem;
    const speed = media.speed ?? 1;
    // no se puede extender antes del inicio del archivo
    const minStart = start - Math.floor(media.source_in_ms / speed);
    target = Math.max(target, minStart);
    if (target === start) return doc;
    const delta = target - start;
    const sourceIn = clamp(
      media.source_in_ms + Math.round(delta * speed),
      0,
      media.source_out_ms - 1,
    );
    const next = { ...media, start_ms: target, source_in_ms: sourceIn } as MediaItem;
    if ("keyframes" in next && next.keyframes) {
      (next as { keyframes?: unknown }).keyframes = shiftKeyframes(
        next.keyframes,
        -delta,
        effectiveMs(next),
      );
    }
    return replaceItem(doc, loc, next as Item);
  }
  if (target === start) return doc;
  return replaceItem(doc, loc, { ...loc.item, start_ms: target } as Item);
}

/** Recorta por el borde derecho: el nuevo fin en la línea de tiempo. */
export function trimEnd(doc: Doc, itemId: string, newEndMs: number, limits: TrimLimits = {}): Doc {
  const loc = find(doc, itemId);
  if (!loc) return doc;
  const { start, end } = span(loc.track, loc.item);
  const { nextStart } = neighbours(loc.track, itemId);
  let target = Math.round(clamp(newEndMs, start + MIN_ITEM_MS, nextStart));
  if (isMediaItem(loc.track)) {
    const media = loc.item as MediaItem;
    const speed = media.speed ?? 1;
    if (limits.sourceDurationMs !== undefined) {
      const maxEnd = start + Math.floor((limits.sourceDurationMs - media.source_in_ms) / speed);
      target = Math.min(target, maxEnd);
    }
    if (target === end || target <= start) return doc;
    const sourceOut = media.source_in_ms + Math.round((target - start) * speed);
    if (sourceOut <= media.source_in_ms) return doc;
    const next = { ...media, source_out_ms: sourceOut } as MediaItem;
    if ("keyframes" in next && next.keyframes) {
      (next as { keyframes?: unknown }).keyframes = next.keyframes.filter(
        (k: { t_ms: number }) => k.t_ms <= effectiveMs(next),
      );
    }
    return replaceItem(doc, loc, next as Item);
  }
  if (target === end) return doc;
  return replaceItem(doc, loc, { ...loc.item, end_ms: target } as Item);
}

function shiftKeyframes<K extends { t_ms: number }>(kfs: K[], delta: number, length: number): K[] {
  return kfs
    .map((k) => ({ ...k, t_ms: k.t_ms + delta }))
    .filter((k) => k.t_ms >= 0 && k.t_ms <= length);
}

// ----------------------------------------------------------------------------------- dividir

/** Divide el elemento en `atMs` (línea de tiempo). Devuelve el documento y el id de la mitad derecha. */
export function splitItem(
  doc: Doc,
  itemId: string,
  atMs: number,
  newId: () => string,
): { doc: Doc; rightId: string | null } {
  const loc = find(doc, itemId);
  if (!loc) return { doc, rightId: null };
  const { start, end } = span(loc.track, loc.item);
  const at = Math.round(atMs);
  if (at - start < MIN_ITEM_MS || end - at < MIN_ITEM_MS) return { doc, rightId: null };
  const rightId = newId();

  if (!isMediaItem(loc.track)) {
    const left = { ...loc.item, end_ms: at } as Item;
    const right = { ...loc.item, id: rightId, start_ms: at } as Item;
    return { doc: replaceItem(doc, loc, [left, right]), rightId };
  }
  const media = loc.item as MediaItem;
  const speed = media.speed ?? 1;
  const cut = clamp(
    media.source_in_ms + Math.round((at - start) * speed),
    media.source_in_ms + 1,
    media.source_out_ms - 1,
  );
  const left = { ...media, source_out_ms: cut } as MediaItem;
  const right = { ...media, id: rightId, start_ms: at, source_in_ms: cut } as MediaItem;
  // el fundido de entrada queda en la mitad izquierda y el de salida en la derecha
  delete (left as { fade_out_ms?: number }).fade_out_ms;
  delete (right as { fade_in_ms?: number }).fade_in_ms;
  if ("keyframes" in media && media.keyframes) {
    const leftLength = at - start;
    (left as { keyframes?: unknown }).keyframes = media.keyframes.filter(
      (k: { t_ms: number }) => k.t_ms <= leftLength,
    );
    (right as { keyframes?: unknown }).keyframes = shiftKeyframes(
      media.keyframes,
      -leftLength,
      effectiveMs(right),
    );
  }
  return { doc: replaceItem(doc, loc, [left as Item, right as Item]), rightId };
}

// ----------------------------------------------------------------------------------- borrar

/** Elimina el elemento. Con `ripple` cierra el hueco desplazando lo posterior de la misma pista. */
export function deleteItem(doc: Doc, itemId: string, ripple = false): Doc {
  const loc = find(doc, itemId);
  if (!loc) return doc;
  const { start, end } = span(loc.track, loc.item);
  let next = replaceItem(doc, loc, null);
  if (ripple) {
    const gap = end - start;
    const tracks = next.timeline.tracks.map((track, ti) => {
      if (ti !== loc.trackIndex) return track;
      const items = track.items.map((it) => {
        const s = span(track, it);
        if (s.start < end) return it;
        const moved = { ...it, start_ms: s.start - gap } as Item;
        if (!isMediaItem(track)) (moved as { end_ms: number }).end_ms = s.end - gap;
        return moved;
      });
      return { ...track, items } as Track;
    });
    next = { ...next, timeline: { ...next.timeline, tracks } } as Doc;
    next = { ...next, timeline: { ...next.timeline, duration_ms: contentDuration(next) } };
  }
  return next;
}

// ------------------------------------------------------------------------------- propiedades

/** Cambia propiedades simples de un elemento (volumen, velocidad, texto…). */
export function patchItem(doc: Doc, itemId: string, patch: Record<string, unknown>): Doc {
  const loc = find(doc, itemId);
  if (!loc) return doc;
  const next = { ...loc.item, ...patch } as Item;
  if (isMediaItem(loc.track) && "speed" in patch) {
    // cambiar la velocidad cambia la duración: no puede pisar al siguiente
    const { nextStart } = neighbours(loc.track, itemId);
    const media = next as MediaItem;
    if (media.start_ms + effectiveMs(media) > nextStart) return doc;
  }
  return replaceItem(doc, loc, next);
}

// -------------------------------------------------------------------------------------- imán

/** Puntos a los que se pega el arrastre: 0, cabezal y bordes de los demás elementos. */
export function snapPoints(doc: Doc, excludeId: string | null, playheadMs: number): number[] {
  const points = new Set<number>([0, Math.round(playheadMs)]);
  for (const track of doc.timeline.tracks) {
    for (const item of track.items) {
      if ((item as { id: string }).id === excludeId) continue;
      const s = span(track, item);
      points.add(s.start);
      points.add(s.end);
    }
  }
  return [...points].sort((a, b) => a - b);
}

/** Devuelve el punto más cercano si está dentro del umbral; si no, el valor tal cual. */
export function snap(valueMs: number, points: number[], thresholdMs: number): number {
  let best = valueMs;
  let bestDistance = thresholdMs + 1;
  for (const p of points) {
    const d = Math.abs(p - valueMs);
    if (d <= thresholdMs && d < bestDistance) {
      best = p;
      bestDistance = d;
    }
  }
  return best;
}

/** Ajusta el INICIO o el FIN (el más cercano a un punto) de un bloque que se arrastra. */
export function snapBlock(
  startMs: number,
  lengthMs: number,
  points: number[],
  thresholdMs: number,
): number {
  const snappedStart = snap(startMs, points, thresholdMs);
  if (snappedStart !== startMs) return snappedStart;
  const snappedEnd = snap(startMs + lengthMs, points, thresholdMs);
  return snappedEnd !== startMs + lengthMs ? snappedEnd - lengthMs : startMs;
}
