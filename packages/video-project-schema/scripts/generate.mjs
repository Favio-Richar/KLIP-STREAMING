// Genera los modelos del documento de edición desde la fuente única `edit-project.schema.json`:
//   - Zod (frontend):     src/edit-project.zod.ts
//   - Pydantic (backend): services/api/src/klick/edit_project/models.py
// `--check` no escribe: falla si lo generado difiere de lo versionado (lo usa `pnpm lint`).
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { jsonSchemaToZod } from "json-schema-to-zod";

const pkg = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(pkg, "..", "..");
const SCHEMA = join(pkg, "edit-project.schema.json");
const ZOD_OUT = join(pkg, "src", "edit-project.zod.ts");
const PY_DIR = join(root, "services", "api", "src", "klick", "edit_project");
const PY_OUT = join(PY_DIR, "models.py");
// Copia del esquema dentro del paquete Python: el validador del servidor la usa en ejecución.
const PY_SCHEMA = join(PY_DIR, "edit-project.schema.json");
const check = process.argv.includes("--check");

// `pnpm` es un .cmd en Windows y necesita shell; `uv` no (y la shell partiría los argumentos).
const sh = (cmd, args, opts = {}) =>
  execFileSync(cmd, args, {
    cwd: root,
    encoding: "utf8",
    shell: cmd === "pnpm" && process.platform === "win32",
    ...opts,
  });

// ---- Zod: json-schema-to-zod no resuelve $ref, así que se inlinean (no hay ciclos) -------------
function inline(node, defs) {
  if (Array.isArray(node)) return node.map((n) => inline(n, defs));
  if (node === null || typeof node !== "object") return node;
  const { $ref, ...rest } = node;
  if ($ref) {
    const name = $ref.replace("#/$defs/", "");
    if (!defs[name]) throw new Error(`$ref sin definición: ${$ref}`);
    return inline({ ...structuredClone(defs[name]), ...rest }, defs); // los hermanos (default) ganan
  }
  // `oneOf` se genera como z.any() (sin tipos); las ramas son excluyentes por `kind`, así que una
  // unión (`anyOf`) es equivalente y conserva los tipos y los errores por campo.
  return Object.fromEntries(
    Object.entries(rest).map(([k, v]) => [k === "oneOf" ? "anyOf" : k, inline(v, defs)]),
  );
}

function buildZod() {
  const schema = JSON.parse(readFileSync(SCHEMA, "utf8"));
  const { $defs, ...top } = schema;
  const body = jsonSchemaToZod(inline(top, $defs), {
    name: "EditProjectSchema",
    module: "esm",
    type: "EditProject",
  });
  const text = `// GENERADO por scripts/generate.mjs desde edit-project.schema.json. No editar a mano.\n${body}`;
  return sh("pnpm", ["exec", "prettier", "--stdin-filepath", "edit-project.zod.ts"], {
    input: text,
  });
}

// ---- Pydantic: datamodel-code-generator ------------------------------------------------------
function buildPydantic() {
  const dir = mkdtempSync(join(tmpdir(), "klick-gen-"));
  const out = join(dir, "models.py");
  try {
    sh("uv", [
      "run",
      "datamodel-codegen",
      "--input",
      SCHEMA,
      "--input-file-type",
      "jsonschema",
      "--output",
      out,
      "--output-model-type",
      "pydantic_v2.BaseModel",
      "--target-python-version",
      "3.12",
      "--use-annotated",
      "--field-constraints",
      "--use-standard-collections",
      "--use-union-operator",
      "--enum-field-as-literal",
      "all",
      "--collapse-root-models",
      "--disable-timestamp",
      "--use-double-quotes",
      "--use-default-kwarg",
      "--formatters",
      "ruff-format",
    ]); // fmt: skip
    sh("uv", ["run", "ruff", "format", "--config", join(root, "pyproject.toml"), out]);
    const header =
      "# GENERADO por packages/video-project-schema/scripts/generate.mjs. No editar a mano.\n";
    return header + readFileSync(out, "utf8");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const outputs = [
  [ZOD_OUT, buildZod()],
  [PY_OUT, buildPydantic()],
  [PY_SCHEMA, readFileSync(SCHEMA, "utf8")],
];
let stale = false;
for (const [path, content] of outputs) {
  let current = "";
  try {
    current = readFileSync(path, "utf8");
  } catch {
    /* aún no existe */
  }
  if (check) {
    if (current.replace(/\r\n/g, "\n") !== content.replace(/\r\n/g, "\n")) {
      console.error(
        `Desactualizado: ${path}\nEjecuta: pnpm --filter @klick/video-project-schema generate`,
      );
      stale = true;
    }
  } else {
    writeFileSync(path, content);
    console.log(`Generado: ${path}`);
  }
}
if (stale) process.exit(1);
if (check) console.log("Modelos del documento de edición al día con el JSON Schema.");
