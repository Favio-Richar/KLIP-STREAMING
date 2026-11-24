/**
 * Reglas semánticas del documento de edición: lo que un JSON Schema no puede expresar. Es el
 * equivalente exacto de `services/api/src/klick/edit_project/validate.py`; ambos se prueban con los
 * mismos fixtures (`fixtures/manifest.json`) para que no se desvíen.
 */
import type { EditProject } from "./edit-project.zod";

export interface Issue {
  /** Código estable: la interfaz y el agente dependen de él. */
  code: string;
  path: string;
  message: string;
}

export const MAX_TOTAL_ITEMS = 2000;
const EPS = 1e-9;

type Track = EditProject["timeline"]["tracks"][number];
type VideoItem = Extract<Track, { kind: "video" }>["items"][number];
type AudioItem = Extract<Track, { kind: "audio" }>["items"][number];

/** Duración del ítem en la línea de tiempo (recorte de la fuente ÷ velocidad). Mitades hacia
 * arriba, igual que el servidor. */
export function effectiveMs(item: VideoItem | AudioItem): number {
  return Math.floor((item.source_out_ms - item.source_in_ms) / (item.speed ?? 1) + 0.5);
}

export function semanticIssues(doc: EditProject): Issue[] {
  const issues: Issue[] = [];
  const add = (code: string, path: string, message: string) => issues.push({ code, path, message });
  const sources = new Map(doc.sources.map((s) => [s.id, s]));
  const duration = doc.timeline.duration_ms;

  const seenSources = new Set<string>();
  doc.sources.forEach((s, i) => {
    if (seenSources.has(s.id)) add("duplicate_id", `sources[${i}].id`, `Fuente repetida: ${s.id}`);
    seenSources.add(s.id);
  });
  const seenTracks = new Set<string>();
  const seenItems = new Set<string>();
  let total = 0;
  doc.timeline.tracks.forEach((track, ti) => {
    if (seenTracks.has(track.id)) {
      add("duplicate_id", `timeline.tracks[${ti}].id`, `Pista repetida: ${track.id}`);
    }
    seenTracks.add(track.id);
    for (const [ii, item] of (track.items as Array<{ id: string }>).entries()) {
      total++;
      if (seenItems.has(item.id)) {
        add(
          "duplicate_id",
          `timeline.tracks[${ti}].items[${ii}].id`,
          `Elemento repetido: ${item.id}`,
        );
      }
      seenItems.add(item.id);
    }
  });
  if (total > MAX_TOTAL_ITEMS)
    add("too_many_items", "timeline", `Máximo ${MAX_TOTAL_ITEMS} elementos.`);

  const checkSource = (sourceId: string, kinds: string[], where: string) => {
    const source = sources.get(sourceId);
    if (!source) add("unknown_source", `${where}.source_id`, `No existe la fuente ${sourceId}.`);
    else if (!kinds.includes(source.kind)) {
      add(
        "source_kind_mismatch",
        `${where}.source_id`,
        `La fuente ${sourceId} es ${source.kind}; se esperaba ${kinds.join(" o ")}.`,
      );
    }
  };
  const checkSpan = (start: number, end: number, where: string) => {
    if (end <= start) add("invalid_range", where, "El fin debe ser posterior al inicio.");
    else if (end > duration) {
      add("exceeds_duration", where, "El elemento termina después de la línea de tiempo.");
    }
  };

  doc.timeline.tracks.forEach((track, ti) => {
    const base = `timeline.tracks[${ti}]`;
    const spans: Array<[number, number, number]> = [];
    if (track.kind === "video" || track.kind === "audio") {
      const kinds = track.kind === "video" ? ["video"] : ["audio", "video"];
      (track.items as Array<VideoItem | AudioItem>).forEach((item, ii) => {
        const where = `${base}.items[${ii}]`;
        checkSource(item.source_id, kinds, where);
        if (item.source_out_ms <= item.source_in_ms) {
          add("invalid_range", where, "La salida de la fuente debe ser posterior a la entrada.");
          return;
        }
        const length = effectiveMs(item);
        const end = item.start_ms + length;
        spans.push([item.start_ms, end, ii]);
        if (end > duration) {
          add("exceeds_duration", where, "El elemento termina después de la línea de tiempo.");
        }
        if ((item.fade_in_ms ?? 0) + (item.fade_out_ms ?? 0) > length) {
          add("fade_too_long", where, "Los fundidos suman más que el elemento.");
        }
        if (track.kind === "video") {
          const video = item as VideoItem;
          const crop = video.crop;
          if (crop && (crop.x + crop.w > 1 + EPS || crop.y + crop.h > 1 + EPS)) {
            add("crop_out_of_bounds", `${where}.crop`, "El recorte se sale del cuadro.");
          }
          let previous = -1;
          (video.keyframes ?? []).forEach((kf, ki) => {
            if (kf.t_ms <= previous) {
              add("keyframes_unordered", `${where}.keyframes[${ki}]`, "Los tiempos deben crecer.");
            }
            if (kf.t_ms > length) {
              add("keyframe_out_of_range", `${where}.keyframes[${ki}]`, "Fuera del elemento.");
            }
            previous = Math.max(previous, kf.t_ms);
          });
        }
      });
      spans.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
      for (let k = 1; k < spans.length; k++) {
        const prev = spans[k - 1];
        const cur = spans[k];
        if (prev && cur && cur[0] < prev[1]) {
          add(
            "track_overlap",
            `${base}.items[${cur[2]}]`,
            "Los elementos de una pista no pueden solaparse.",
          );
        }
      }
    } else if (track.kind === "text") {
      track.items.forEach((item, ii) =>
        checkSpan(item.start_ms, item.end_ms, `${base}.items[${ii}]`),
      );
    } else {
      track.items.forEach((item, ii) => {
        const where = `${base}.items[${ii}]`;
        checkSource(item.source_id, ["image"], where);
        checkSpan(item.start_ms, item.end_ms, where);
      });
    }
  });

  const { layout } = doc;
  if (layout.mode === "split") {
    if (!layout.split) add("layout_missing_config", "layout.split", "El modo split exige `split`.");
    else {
      const { top_source: top, bottom_source: bottom } = layout.split;
      const isVideo = (id: string) => sources.get(id)?.kind === "video";
      if (top === bottom || !isVideo(top) || !isVideo(bottom)) {
        add(
          "layout_unknown_source",
          "layout.split",
          "Las fuentes superior e inferior deben ser dos videos distintos del documento.",
        );
      }
    }
  }
  if (layout.mode === "fit" && !layout.fit) {
    add("layout_missing_config", "layout.fit", "El modo fit exige `fit`.");
  }
  return issues;
}
