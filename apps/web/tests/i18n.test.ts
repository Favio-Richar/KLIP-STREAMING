import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

type Tree = { [k: string]: string | Tree };
const root = resolve(__dirname, "..");
const load = (l: string) =>
  JSON.parse(readFileSync(join(root, "messages", `${l}.json`), "utf8")) as Tree;
const es = load("es-CL");
const en = load("en");

function flatten(t: Tree, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of Object.entries(t)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out.set(key, v);
    else for (const [a, b] of flatten(v, key)) out.set(a, b);
  }
  return out;
}
const esFlat = flatten(es);
const enFlat = flatten(en);

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe("catálogos de mensajes", () => {
  it("es-CL y en tienen exactamente las mismas claves", () => {
    expect([...enFlat.keys()].sort()).toEqual([...esFlat.keys()].sort());
  });

  it("los marcadores {x} coinciden entre idiomas", () => {
    for (const [key, text] of esFlat) {
      expect(placeholders(enFlat.get(key) ?? ""), key).toEqual(placeholders(text));
    }
  });

  it("no hay textos vacíos", () => {
    for (const [key, text] of [...esFlat, ...enFlat])
      expect(text.trim().length, key).toBeGreaterThan(0);
  });

  it("el español no usa signos de exclamación (docs/08)", () => {
    for (const [key, text] of esFlat) expect(text, key).not.toMatch(/[!¡]/);
  });
});

function* sources(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) yield* sources(full);
    else if (/\.tsx?$/.test(name)) yield full;
  }
}

describe("uso de claves en el código", () => {
  it("toda clave estática t('x') existe en el catálogo", () => {
    const missing: string[] = [];
    for (const file of sources(join(root, "src"))) {
      const text = readFileSync(file, "utf8");
      // Cada `const <var> = useTranslations("ns")` / `await getTranslations("ns")` asocia una variable a un espacio.
      const bindings = [
        ...text.matchAll(
          /const\s+(\w+)\s*=\s*(?:await\s+)?(?:useTranslations|getTranslations)\(\s*(?:"([\w.]+)")?\s*\)/g,
        ),
      ];
      bindings.forEach((m, i) => {
        const [, variable, ns] = m;
        // Una variable `t` vale desde su declaración hasta la siguiente declaración de la misma variable.
        const start = m.index ?? 0;
        const next = bindings.slice(i + 1).find((o) => o[1] === variable)?.index ?? text.length;
        const region = text.slice(start, next);
        for (const [, key] of region.matchAll(
          new RegExp(String.raw`\b${variable}\(\s*"([\w.]+)"`, "g"),
        )) {
          const full = ns ? `${ns}.${key}` : key!;
          if (!esFlat.has(full)) missing.push(`${file.replace(root, "")}: ${full}`);
        }
      });
    }
    expect(missing).toEqual([]);
  });
});
