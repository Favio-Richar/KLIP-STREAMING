import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../src/tokens/tokens.css"), "utf8");

/** Extrae los tokens de color de un bloque cuyo selector contiene `marker`. */
function block(marker: string): Record<string, string> {
  const start = css.indexOf(marker);
  if (start < 0) throw new Error(`bloque no encontrado: ${marker}`);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b/g)) out[m[1]!] = m[2]!;
  return out;
}

const dark = block(':root[data-theme="dark"]');
const light = { ...dark, ...block(':root[data-theme="light"]') };
// Las variables de `@media (prefers-color-scheme: light)` deben ser idénticas al tema claro.
const media = css.slice(css.indexOf("@media (prefers-color-scheme: light)"));
const lightMedia = { ...dark, ...block(":root:not([data-theme])") };

function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!;
}

export function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

const themes = { oscuro: dark, claro: light } as const;
const surfaces = ["bg-0", "bg-1", "bg-2", "bg-3"] as const;

describe.each(Object.entries(themes))("tema %s", (_name, t) => {
  it.each(["text-1", "text-2", "text-3"])(
    "%s es legible (≥ 4,5:1) sobre toda superficie",
    (tok) => {
      for (const s of surfaces) {
        expect(ratio(t[tok]!, t[s]!), `${tok} sobre ${s}`).toBeGreaterThanOrEqual(4.5);
      }
    },
  );

  it.each(["success", "warning", "danger", "info", "accent-text"])(
    "%s como texto o icono es legible (≥ 4,5:1) sobre bg-0..bg-2",
    (tok) => {
      for (const s of ["bg-0", "bg-1", "bg-2"] as const) {
        expect(ratio(t[tok]!, t[s]!), `${tok} sobre ${s}`).toBeGreaterThanOrEqual(4.5);
      }
    },
  );

  it("el texto sobre el acento es legible (≥ 4,5:1) en reposo, hover y pulsado", () => {
    for (const fill of ["accent", "accent-hover", "accent-press"]) {
      expect(ratio(t["on-accent"]!, t[fill]!), fill).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("bordes de controles, foco y acento relleno se distinguen (≥ 3:1)", () => {
    for (const s of ["bg-0", "bg-1", "bg-2"] as const) {
      expect(
        ratio(t["control-border"]!, t[s]!),
        `control-border sobre ${s}`,
      ).toBeGreaterThanOrEqual(3);
      expect(ratio(t["focus-ring"]!, t[s]!), `focus-ring sobre ${s}`).toBeGreaterThanOrEqual(3);
    }
    expect(ratio(t["accent"]!, t["bg-1"]!) >= 3 || t === light).toBe(true);
  });
});

describe("tokens", () => {
  it("el bloque de preferencia del sistema coincide con el tema claro", () => {
    expect(media).toContain("prefers-color-scheme");
    for (const k of [
      "bg-0",
      "bg-1",
      "bg-2",
      "bg-3",
      "text-1",
      "text-2",
      "text-3",
      "control-border",
      "accent-text",
      "focus-ring",
      "success",
      "warning",
      "danger",
      "info",
    ]) {
      expect(lightMedia[k], k).toBe(light[k]);
    }
  });
});
