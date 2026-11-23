#!/usr/bin/env node
// Ejecuta un comando solo si existe el archivo requerido; si no, avisa y termina bien.
// Uso: node scripts/task.mjs <ruta-requerida> -- <comando> [args...]
// Multiplataforma (Windows PowerShell / Linux). Los pasos pendientes de tareas futuras no rompen `pnpm check`.
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";

const argv = process.argv.slice(2);
const sep = argv.indexOf("--");
if (sep < 1 || sep === argv.length - 1) {
  console.error("uso: task.mjs <ruta-requerida> -- <comando> [args...]");
  process.exit(2);
}
const required = argv.slice(0, sep);
const cmd = argv.slice(sep + 1);
const missing = required.filter((p) => !existsSync(p));
if (missing.length > 0) {
  console.log(
    `[task] omitido: aún no existe ${missing.join(", ")} (lo crea una tarea posterior del roadmap)`,
  );
  process.exit(0);
}
const res = spawnSync(cmd[0], cmd.slice(1), {
  stdio: "inherit",
  shell: process.platform === "win32",
});
process.exit(res.status ?? 1);
