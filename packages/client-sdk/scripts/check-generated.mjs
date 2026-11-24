// Falla si el contrato de la API cambió y el cliente no se regeneró (`pnpm --filter @klick/client-sdk generate`).
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const before = readFileSync(join(root, "openapi.json"), "utf8");
execFileSync("uv", ["run", "--package", "klick", "python", "scripts/dump_openapi.py"], {
  cwd: root,
  stdio: "pipe",
});
const after = readFileSync(join(root, "openapi.json"), "utf8");
if (before !== after) {
  // Se restaura el archivo para no dejar el árbol modificado por una simple comprobación.
  writeFileSync(join(root, "openapi.json"), before);
  console.error(
    "openapi.json está desactualizado: ejecuta `pnpm --filter @klick/client-sdk generate`.",
  );
  process.exit(1);
}
console.log("Cliente generado al día con la API.");
