#!/usr/bin/env node
// Verifica las reglas visuales de docs/08-diseno-ui.md §1 y §7.
// Recorre CSS, TS/TSX/JS/MJS (clases de Tailwind y estilos en línea) de apps/* y packages/*.
// Excepciones: comentario `design-rule-exception: motivo` en la misma línea o en la anterior.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const EXTENSIONS = new Set([".css", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]);
const SKIP_DIRS = new Set([
  "node_modules",
  ".next",
  "dist",
  "build",
  "coverage",
  ".turbo",
  "out",
  "target",
  "gen",
]);
const SKIP_FILE = /\.(test|spec)\.[cm]?[jt]sx?$/;

// Una clase de Tailwind aparece delimitada por espacio, comillas, `:` (variantes) o `!`.
const CLS_START = "(?:^|[\\s\"'`:!(\\[])";
const CLS_END = "(?=$|[\\s\"'`)\\]])";

const RULES = [
  {
    id: "backdrop-filter",
    re: /backdrop-filter|backdropFilter/,
    msg: "backdrop-filter está prohibido",
  },
  {
    id: "backdrop-blur",
    re: new RegExp(`${CLS_START}backdrop-(?:blur|filter)\\b`),
    msg: "backdrop-blur está prohibido",
  },
  {
    id: "filter-blur",
    re: /filter\s*:\s*[^;}]*blur\s*\(|\bfilter\s*:\s*["'`][^"'`]*blur\s*\(/,
    msg: "filter: blur(...) está prohibido",
  },
  {
    id: "blur-class",
    re: new RegExp(`${CLS_START}-?blur(?:-[\\w\\[\\]./-]+)?${CLS_END}`),
    msg: "las clases blur-* están prohibidas",
  },
  {
    id: "shadow-class",
    re: new RegExp(`${CLS_START}(?:inset-)?shadow(?:-(?!none\\b)[\\w\\[\\]./,#()%:-]+)?${CLS_END}`),
    msg: "solo se permite shadow-none (la elevación se expresa con tono y borde)",
  },
  { id: "drop-shadow", re: /drop-shadow|dropShadow/, msg: "drop-shadow está prohibido" },
  {
    id: "bg-opacity",
    re: /\bbg-opacity-|\bbackground-opacity\b/,
    msg: "los fondos translúcidos están prohibidos",
  },
  {
    id: "bg-alpha-modifier",
    re: new RegExp(`${CLS_START}bg-[\\w\\[\\]#()-]+\\/\\d+${CLS_END}`),
    msg: "los fondos translúcidos (bg-x/NN) están prohibidos; usa un token sólido",
  },
  {
    id: "radial-gradient",
    re: /radial-gradient|conic-gradient|\bbg-radial\b|\bbg-conic\b/,
    msg: "los degradados radiales/cónicos decorativos están prohibidos",
  },
];

/** ¿Algún `box-shadow` tiene radio de desenfoque mayor que 0? */
export function blurredBoxShadow(line) {
  const m = line.match(/(?:box-shadow|boxShadow)\s*:\s*["'`]?([^;}"'`]*)/);
  if (!m) return null;
  const value = m[1].trim();
  if (!value || /^(none|inherit|initial|unset|var\()/.test(value)) return null;
  // Separa por comas fuera de paréntesis.
  const layers = [];
  let depth = 0;
  let cur = "";
  for (const ch of value) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (ch === "," && depth === 0) {
      layers.push(cur);
      cur = "";
    } else cur += ch;
  }
  layers.push(cur);
  for (const layer of layers) {
    const stripped = layer
      .replace(/(?:rgba?|hsla?|oklch|oklab|color-mix|var)\([^)]*\)/g, " ")
      .replace(/#[0-9a-f]{3,8}\b/gi, " ");
    const lengths = stripped.match(/-?\d*\.?\d+(?:px|rem|em)?(?=\s|$)/g) ?? [];
    if (lengths.length >= 3 && parseFloat(lengths[2]) > 0) return layer.trim();
    if (/var\(/.test(layer) && lengths.length === 0) return layer.trim(); // no verificable: se rechaza
  }
  return null;
}

export function checkSource(text, file = "<memoria>") {
  const lines = text.split(/\r?\n/);
  const found = [];
  lines.forEach((line, i) => {
    const prev = i > 0 ? lines[i - 1] : "";
    if (/design-rule-exception:/.test(line) || /design-rule-exception:/.test(prev)) return;
    for (const rule of RULES) {
      if (rule.re.test(line))
        found.push({ file, line: i + 1, rule: rule.id, msg: rule.msg, text: line.trim() });
    }
    const bad = blurredBoxShadow(line);
    if (bad)
      found.push({
        file,
        line: i + 1,
        rule: "box-shadow-blur",
        msg: `box-shadow con desenfoque (${bad})`,
        text: line.trim(),
      });
  });
  return found;
}

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) yield* walk(full);
    else if (EXTENSIONS.has(name.slice(name.lastIndexOf("."))) && !SKIP_FILE.test(name)) yield full;
  }
}

export function checkTree(root, scanRoots = ["apps", "packages"]) {
  const out = [];
  for (const sub of scanRoots) {
    const base = join(root, sub);
    try {
      statSync(base);
    } catch {
      continue;
    }
    for (const file of walk(base)) {
      const rel = relative(root, file).split(sep).join("/");
      out.push(...checkSource(readFileSync(file, "utf8"), rel));
    }
  }
  return out;
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const root = resolve(fileURLToPath(import.meta.url), "..", "..");
  const violations = checkTree(root);
  if (violations.length) {
    for (const v of violations)
      console.error(`${v.file}:${v.line}  [${v.rule}] ${v.msg}\n    ${v.text}`);
    console.error(
      `\n${violations.length} violación(es) de las reglas de diseño (docs/08-diseno-ui.md).`,
    );
    process.exit(1);
  }
  console.log("Reglas de diseño: sin violaciones.");
}
