# @klick/video-project-schema

Esquema del **documento de edición** (`EditProject`, docs/07 §2). `edit-project.schema.json` es la fuente
única de la vista previa y del render.

| Qué | Dónde | Cómo se obtiene |
|---|---|---|
| Fuente única | `edit-project.schema.json` | a mano |
| Zod (frontend) | `src/edit-project.zod.ts` | generado |
| Pydantic (backend) | `services/api/src/klick/edit_project/models.py` | generado |
| Copia del esquema para el servidor | `services/api/src/klick/edit_project/edit-project.schema.json` | generado |
| Reglas que cruzan campos | `src/semantic.ts` y `services/api/.../validate.py` | a mano, **idénticas** |

`pnpm --filter @klick/video-project-schema generate` regenera todo; `pnpm lint` falla si algo quedó
desactualizado. Nunca se editan a mano los archivos generados.

## Validación del servidor (`parse_edit_project`)
1. JSON Schema (estructura, rangos, `null`).
2. Pydantic generado (tipos).
3. Reglas semánticas: referencias a fuentes, tipos de fuente, rangos, duraciones, solapes, fundidos,
   *keyframes*, recortes y layouts.

Un documento inválido nunca se guarda y se informan **todos** los problemas a la vez, cada uno con un
código estable (`unknown_source`, `track_overlap`, ...) y la ruta exacta.

## Fixtures compartidos
`fixtures/manifest.json` lista cada documento y los códigos que debe producir. **Python (pytest) y
TypeScript (Vitest) prueban los mismos archivos**: así las dos implementaciones de las reglas no pueden
desviarse. Al añadir una regla, añade primero su fixture inválido.

## Migraciones
`services/api/.../migrate.py` (`migrate_edit_project`): cadena de funciones por versión. Un cambio mayor
de `schema_version` registra su migración y un fixture. Hoy solo existe la 1.0.
