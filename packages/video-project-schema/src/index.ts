import type { ZodIssue } from "zod";
import { EditProjectSchema, type EditProject } from "./edit-project.zod";
import { semanticIssues, type Issue } from "./semantic";

export { EditProjectSchema, type EditProject } from "./edit-project.zod";
export { effectiveMs, semanticIssues, MAX_TOTAL_ITEMS, type Issue } from "./semantic";

export const SCHEMA_VERSION = "1.0";

export type ParseResult =
  | { ok: true; doc: EditProject; issues: [] }
  | { ok: false; issues: Issue[] };

const MAX_SCHEMA_ISSUES = 20;

/** Ruta con el mismo formato que el servidor: `timeline.tracks[0].items[1].start_ms`. */
function pathOf(parts: ReadonlyArray<PropertyKey>): string {
  let out = "";
  for (const p of parts) out += typeof p === "number" ? `[${p}]` : `.${String(p)}`;
  return out.replace(/^\./, "") || "$";
}

/** Baja por las uniones (`oneOf`) hasta el campo concreto que falla: la rama que el documento casi
 * cumple (la de menos errores), igual que el servidor. */
function leaves(issue: ZodIssue): ZodIssue[] {
  const branches = (issue as { errors?: ZodIssue[][] }).errors;
  if (issue.code === "invalid_union" && branches?.length) {
    const closest = branches.reduce((a, b) => (b.length < a.length ? b : a));
    // las rutas de las ramas son relativas a la unión: se anteponen para tener la ruta completa
    return closest.flatMap((sub) => leaves({ ...sub, path: [...issue.path, ...sub.path] }));
  }
  return [issue];
}

/** Valida un documento: esquema (Zod, generado) y reglas semánticas. Equivale a
 * `parse_edit_project` del servidor. */
export function validateEditProject(raw: unknown): ParseResult {
  const parsed = EditProjectSchema.safeParse(raw);
  if (!parsed.success) {
    const issues = parsed.error.issues
      .flatMap(leaves)
      .slice(0, MAX_SCHEMA_ISSUES)
      .map((i) => ({ code: "schema", path: pathOf(i.path), message: i.message.slice(0, 300) }));
    return { ok: false, issues };
  }
  const problems = semanticIssues(parsed.data);
  return problems.length
    ? { ok: false, issues: problems }
    : { ok: true, doc: parsed.data, issues: [] };
}
