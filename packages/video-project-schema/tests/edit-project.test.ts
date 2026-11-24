import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { EditProjectSchema, effectiveMs, validateEditProject } from "../src";

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures");
// Documentos JSON arbitrarios (incluidos los inválidos a propósito): no tienen forma conocida.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = Record<string, any>;
const load = (name: string): Json => JSON.parse(readFileSync(join(FIXTURES, name), "utf8")) as Json;
const manifest: Array<{ file: string; valid: boolean; errors: string[] }> = load(
  "manifest.json",
) as never;

describe("fixtures compartidos con el servidor", () => {
  it("el manifiesto cubre todos los archivos", () => {
    const onDisk = ["valid", "invalid"].flatMap((d) =>
      readdirSync(join(FIXTURES, d)).map((f) => `${d}/${f}`),
    );
    expect(onDisk.sort()).toEqual(manifest.map((m) => m.file).sort());
  });

  for (const m of manifest) {
    it(`${m.valid ? "acepta" : "rechaza"} ${m.file}${
      m.valid ? "" : ` (${m.errors.join(", ")})`
    }`, () => {
      const result = validateEditProject(load(m.file));
      expect(result.ok).toBe(m.valid);
      expect(new Set(result.issues.map((i) => i.code))).toEqual(new Set(m.errors));
      for (const issue of result.issues) {
        expect(issue.path).toBeTruthy();
        expect(issue.message).toBeTruthy();
      }
    });
  }
});

describe("validateEditProject", () => {
  it("aplica los valores por defecto del esquema", () => {
    const result = validateEditProject(load("valid/minimal.json"));
    if (!result.ok) throw new Error("debería ser válido");
    const track = result.doc.timeline.tracks[0];
    expect(track?.kind).toBe("video");
    expect(result.doc.canvas.background).toBe("#000000");
    expect(result.doc.audio.normalize).toMatchObject({ target_lufs: -14, true_peak_db: -1.5 });
  });

  it("rechaza null explícito en campos opcionales (igual que el servidor)", () => {
    const doc = load("valid/minimal.json");
    doc.canvas.background = null;
    expect(validateEditProject(doc).ok).toBe(false);
    const doc2 = load("valid/minimal.json");
    doc2.timeline.tracks[0].items[0].speed = null;
    expect(validateEditProject(doc2).ok).toBe(false);
  });

  it("las rutas apuntan al campo exacto, también dentro de pistas (oneOf)", () => {
    const dangling = validateEditProject(load("invalid/dangling-source.json"));
    expect(dangling.issues[0]?.path).toBe("timeline.tracks[0].items[0].source_id");
    const negative = validateEditProject(load("invalid/negative-time.json"));
    expect(negative.issues[0]?.path).toBe("timeline.tracks[0].items[0].start_ms");
  });

  it("informa todos los problemas a la vez", () => {
    const doc = load("valid/minimal.json");
    Object.assign(doc.timeline.tracks[0].items[0], {
      source_id: "nope",
      fade_in_ms: 30000,
      crop: { x: 0.9, y: 0, w: 0.5, h: 1 },
    });
    const result = validateEditProject(doc);
    expect(new Set(result.issues.map((i) => i.code))).toEqual(
      new Set(["unknown_source", "fade_too_long", "crop_out_of_bounds"]),
    );
  });

  it("la duración efectiva considera la velocidad y redondea mitades hacia arriba", () => {
    const result = validateEditProject(load("valid/speed-up.json"));
    if (!result.ok) throw new Error("debería ser válido");
    const item = result.doc.timeline.tracks[0]?.items[0];
    expect(item && "speed" in item ? effectiveMs(item) : 0).toBe(28400);
    expect(effectiveMs({ source_in_ms: 0, source_out_ms: 5, speed: 2 } as never)).toBe(3); // 2,5 → 3
  });

  it("no se rompe con entradas basura", () => {
    for (const junk of [null, undefined, [], "texto", 5, {}, { schema_version: "1.0" }]) {
      const result = validateEditProject(junk);
      expect(result.ok).toBe(false);
      expect(result.issues.length).toBeGreaterThan(0);
    }
  });

  it("el esquema Zod es estricto: no admite propiedades desconocidas", () => {
    const doc = { ...load("valid/minimal.json"), org_id: "x" };
    expect(EditProjectSchema.safeParse(doc).success).toBe(false);
  });
});
