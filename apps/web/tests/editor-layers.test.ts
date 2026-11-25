import { semanticIssues, validateEditProject } from "@klick/video-project-schema";
import { describe, expect, it } from "vitest";

import {
  cueAt,
  insertCue,
  normalizeText,
  removeCue,
  toPayload,
  updateCue,
  validateCues,
  type Cue,
} from "@/lib/editor/captions";
import { addImageLayer, addTextLayer, find, setCaptionsTrack, type Doc } from "@/lib/editor/ops";

function makeDoc(): Doc {
  return {
    schema_version: "1.0",
    canvas: { width: 1080, height: 1920, fps: 30, background: "#000000" },
    sources: [{ id: "s1", asset_id: "0190a1b2-c3d4-7e5f-8a6b-7c8d9e0f1a2b", kind: "video" }],
    layout: { mode: "fit", fit: { background: { type: "solid", color: "#0B0D10" } } },
    audio: { normalize: { enabled: true } },
    timeline: {
      duration_ms: 9000,
      tracks: [
        {
          id: "v1",
          kind: "video",
          items: [{ id: "a", source_id: "s1", source_in_ms: 0, source_out_ms: 9000, start_ms: 0 }],
        },
      ],
    },
  } as unknown as Doc;
}

let n = 0;
const newId = (prefix: string) => `${prefix}${++n}`;

describe("capas de texto e imagen", () => {
  it("crea la pista de texto la primera vez y reutiliza la existente", () => {
    const first = addTextLayer(makeDoc(), 1000, "Hola", newId);
    const second = addTextLayer(first.doc, 5000, "Chao", newId);
    const texts = second.doc.timeline.tracks.filter((t) => t.kind === "text");
    expect(texts).toHaveLength(1);
    expect(texts[0]?.items).toHaveLength(2);
    const item = find(first.doc, first.id)!.item as {
      start_ms: number;
      end_ms: number;
      text: string;
    };
    expect([item.start_ms, item.end_ms, item.text]).toEqual([1000, 3500, "Hola"]);
    expect(semanticIssues(second.doc)).toEqual([]);
    expect(validateEditProject(second.doc).ok).toBe(true); // cumple el esquema completo
  });

  it("no se sale de la línea de tiempo ni la alarga", () => {
    const { doc, id } = addTextLayer(makeDoc(), 8900, "Final", newId);
    const item = find(doc, id)!.item as { start_ms: number; end_ms: number };
    expect(item.end_ms).toBeLessThanOrEqual(9000);
    expect(item.end_ms - item.start_ms).toBeGreaterThan(0);
    expect(doc.timeline.duration_ms).toBe(9000);
    expect(semanticIssues(doc)).toEqual([]);
  });

  it("agrega la fuente de imagen una sola vez por archivo", () => {
    const asset = "0190a1b2-c3d4-7e5f-8a6b-7c8d9e0f1a99";
    const one = addImageLayer(makeDoc(), asset, 500, newId);
    const two = addImageLayer(one.doc, asset, 4000, newId);
    expect(two.doc.sources.filter((s) => s.asset_id === asset)).toHaveLength(1);
    const images = two.doc.timeline.tracks.find((t) => t.kind === "image");
    expect(images?.items).toHaveLength(2);
    expect(semanticIssues(two.doc)).toEqual([]);
    expect(validateEditProject(two.doc).ok).toBe(true);
    const item = find(one.doc, one.id)!.item as { scale: number; position: { x: number } };
    expect([item.scale, item.position.x]).toEqual([1, 0.5]);
  });

  it("enlaza y desenlaza la pista de subtítulos", () => {
    const id = "0190a1b2-c3d4-7e5f-8a6b-7c8d9e0f1a77";
    const linked = setCaptionsTrack(makeDoc(), id);
    expect(linked.captions).toEqual({ track_id: id, safe_area: "shorts" });
    expect(setCaptionsTrack(linked, id)).toBe(linked);
    const removed = setCaptionsTrack(linked, null);
    expect("captions" in removed).toBe(false);
    expect(setCaptionsTrack(removed, null)).toBe(removed);
  });
});

describe("subtítulos", () => {
  const cues: Cue[] = [
    { start_ms: 1000, end_ms: 2000, text: "uno" },
    { start_ms: 3000, end_ms: 4000, text: "dos" },
  ];

  it("inserta en el cabezal, después del cue que lo contiene y sin invadir al siguiente", () => {
    const a = insertCue(cues, 0, 9000)!;
    expect(a.cues[0]).toEqual({ start_ms: 0, end_ms: 1000, text: "" }); // cabe hasta el siguiente
    expect(a.index).toBe(0);
    const b = insertCue(cues, 1500, 9000)!; // dentro de "uno": empieza en su fin
    expect(b.cues[1]).toMatchObject({ start_ms: 2000, end_ms: 3000 });
    const c = insertCue(cues, 5000, 9000)!;
    expect(c.cues[2]).toMatchObject({ start_ms: 5000, end_ms: 7000 });
    expect(validateCues(c.cues.map((q) => ({ ...q, text: q.text || "x" })))).toEqual([]);
  });

  it("no inserta si no hay hueco", () => {
    const tight: Cue[] = [
      { start_ms: 0, end_ms: 4000, text: "a" },
      { start_ms: 4100, end_ms: 5000, text: "b" },
    ];
    expect(insertCue(tight, 100, 9000)).toBeNull(); // hueco de 100 ms
    expect(insertCue(tight, 8900, 9000)).toBeNull(); // fin de la línea de tiempo
  });

  it("valida con los mismos códigos que el servidor", () => {
    const codes = (c: Cue[]) => validateCues(c).map((i) => i.code);
    expect(codes([{ start_ms: 0, end_ms: 0, text: "a" }])).toContain("invalid_range");
    expect(
      codes([
        { start_ms: 0, end_ms: 2000, text: "a" },
        { start_ms: 1500, end_ms: 3000, text: "b" },
      ]),
    ).toContain("cues_overlap");
    expect(codes([{ start_ms: 0, end_ms: 1, text: "a\nb\nc" }])).toContain("too_many_lines");
    expect(codes([{ start_ms: 0, end_ms: 1, text: "x".repeat(61) }])).toContain("line_too_long");
    expect(codes([{ start_ms: 0, end_ms: 1, text: "  \n " }])).toContain("empty_text");
    expect(codes([{ start_ms: 0, end_ms: 1, text: "x".repeat(201) }])).toContain("text_too_long");
    expect(validateCues(cues)).toEqual([]);
  });

  it("busca el cue activo con inicio incluido y fin excluido", () => {
    expect(cueAt(cues, 1000)?.text).toBe("uno");
    expect(cueAt(cues, 1999)?.text).toBe("uno");
    expect(cueAt(cues, 2000)).toBeNull();
  });

  it("edita y elimina sin mutar y descarta tiempos por palabra al cambiar", () => {
    const withWords: Cue[] = [
      {
        start_ms: 0,
        end_ms: 1000,
        text: "hola",
        words: [{ start_ms: 0, end_ms: 500, text: "hola" }],
      },
    ];
    expect(updateCue(withWords, 0, { text: "chao" })[0]).not.toHaveProperty("words");
    expect(withWords[0]).toHaveProperty("words");
    expect(removeCue(cues, 0)).toEqual([cues[1]]);
    expect(cues).toHaveLength(2);
  });

  it("normaliza el texto como el servidor", () => {
    expect(normalizeText("  hola  \r\n  mundo \n")).toBe("hola\nmundo");
    expect(normalizeText("Hola\tmundo")).toBe("Hola mundo");
    expect(toPayload([{ start_ms: 0, end_ms: 1, text: " x " }])[0]?.text).toBe("x");
  });
});
