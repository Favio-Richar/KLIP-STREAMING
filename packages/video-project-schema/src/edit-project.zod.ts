// GENERADO por scripts/generate.mjs desde edit-project.schema.json. No editar a mano.
import { z } from "zod";

export const EditProjectSchema = z
  .object({
    schema_version: z.literal("1.0"),
    canvas: z
      .object({
        width: z.number().int().multipleOf(2).gte(16).lte(4096),
        height: z.number().int().multipleOf(2).gte(16).lte(4096),
        fps: z.union([z.literal(24), z.literal(25), z.literal(30), z.literal(50), z.literal(60)]),
        background: z.string().regex(new RegExp("^#[0-9a-fA-F]{6}$")).default("#000000"),
      })
      .strict(),
    sources: z
      .array(
        z
          .object({
            id: z
              .string()
              .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
              .describe(
                "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
              ),
            asset_id: z
              .string()
              .regex(new RegExp("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")),
            kind: z.enum(["video", "audio", "image"]),
          })
          .strict(),
      )
      .min(1)
      .max(16),
    layout: z
      .object({
        mode: z.enum(["follow", "split", "fit", "manual"]),
        split: z
          .object({
            top_source: z
              .string()
              .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
              .describe(
                "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
              ),
            bottom_source: z
              .string()
              .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
              .describe(
                "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
              ),
            ratio: z.number().gte(0.1).lte(0.9),
          })
          .strict()
          .optional(),
        fit: z
          .object({
            background: z
              .object({
                type: z.literal("solid"),
                color: z.string().regex(new RegExp("^#[0-9a-fA-F]{6}$")),
              })
              .strict(),
          })
          .strict()
          .optional(),
        follow: z
          .object({
            smoothing: z.number().gte(0).lte(1).default(0.85),
            subject: z.enum(["face", "none"]).default("face"),
          })
          .strict()
          .optional(),
      })
      .strict(),
    timeline: z
      .object({
        duration_ms: z.number().int().gte(1).lte(14400000),
        tracks: z
          .array(
            z.union([
              z
                .object({
                  id: z
                    .string()
                    .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                    .describe(
                      "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                    ),
                  kind: z.literal("video"),
                  items: z
                    .array(
                      z
                        .object({
                          id: z
                            .string()
                            .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                            .describe(
                              "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                            ),
                          source_id: z
                            .string()
                            .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                            .describe(
                              "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                            ),
                          source_in_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          source_out_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          start_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          speed: z.number().gte(0.25).lte(4).default(1),
                          volume: z.number().gte(0).lte(2).default(1),
                          fade_in_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h).")
                            .default(0),
                          fade_out_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h).")
                            .default(0),
                          crop: z
                            .object({
                              x: z.number().gte(0).lte(1),
                              y: z.number().gte(0).lte(1),
                              w: z.number().gt(0).lte(1),
                              h: z.number().gt(0).lte(1),
                            })
                            .strict()
                            .optional(),
                          keyframes: z
                            .array(
                              z
                                .object({
                                  t_ms: z
                                    .number()
                                    .int()
                                    .gte(0)
                                    .lte(86400000)
                                    .describe("Milisegundos enteros (máximo 24 h)."),
                                  scale: z.number().gte(1).lte(8).default(1),
                                  x: z.number().gte(0).lte(1).default(0.5),
                                  y: z.number().gte(0).lte(1).default(0.5),
                                  ease: z.enum(["linear", "in", "out", "inout"]).default("linear"),
                                })
                                .strict(),
                            )
                            .max(200)
                            .optional(),
                        })
                        .strict(),
                    )
                    .max(500),
                })
                .strict(),
              z
                .object({
                  id: z
                    .string()
                    .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                    .describe(
                      "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                    ),
                  kind: z.literal("audio"),
                  items: z
                    .array(
                      z
                        .object({
                          id: z
                            .string()
                            .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                            .describe(
                              "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                            ),
                          source_id: z
                            .string()
                            .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                            .describe(
                              "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                            ),
                          source_in_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          source_out_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          start_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          speed: z.number().gte(0.25).lte(4).default(1),
                          volume: z.number().gte(0).lte(2).default(1),
                          fade_in_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h).")
                            .default(0),
                          fade_out_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h).")
                            .default(0),
                        })
                        .strict(),
                    )
                    .max(500),
                })
                .strict(),
              z
                .object({
                  id: z
                    .string()
                    .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                    .describe(
                      "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                    ),
                  kind: z.literal("text"),
                  items: z
                    .array(
                      z
                        .object({
                          id: z
                            .string()
                            .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                            .describe(
                              "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                            ),
                          start_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          end_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          text: z.string().min(1).max(500),
                          style_ref: z
                            .string()
                            .regex(
                              new RegExp(
                                "^tpl:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                              ),
                            )
                            .describe("Referencia a una plantilla; se resuelve al renderizar.")
                            .optional(),
                          position: z
                            .object({ x: z.number().gte(0).lte(1), y: z.number().gte(0).lte(1) })
                            .strict(),
                          align: z.enum(["left", "center", "right"]).default("center"),
                        })
                        .strict(),
                    )
                    .max(500),
                })
                .strict(),
              z
                .object({
                  id: z
                    .string()
                    .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                    .describe(
                      "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                    ),
                  kind: z.literal("image"),
                  items: z
                    .array(
                      z
                        .object({
                          id: z
                            .string()
                            .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                            .describe(
                              "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                            ),
                          source_id: z
                            .string()
                            .regex(new RegExp("^[a-z][a-z0-9_-]{0,31}$"))
                            .describe(
                              "Identificador local del documento (minúsculas, dígitos, guion y guion bajo).",
                            ),
                          start_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          end_ms: z
                            .number()
                            .int()
                            .gte(0)
                            .lte(86400000)
                            .describe("Milisegundos enteros (máximo 24 h)."),
                          position: z
                            .object({ x: z.number().gte(0).lte(1), y: z.number().gte(0).lte(1) })
                            .strict(),
                          scale: z.number().gte(0.01).lte(4).default(1),
                        })
                        .strict(),
                    )
                    .max(500),
                })
                .strict(),
            ]),
          )
          .min(1)
          .max(32),
      })
      .strict(),
    captions: z
      .object({
        track_id: z
          .string()
          .regex(new RegExp("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")),
        style_ref: z
          .string()
          .regex(new RegExp("^tpl:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"))
          .describe("Referencia a una plantilla; se resuelve al renderizar.")
          .optional(),
        safe_area: z.enum(["shorts", "none"]).default("shorts"),
      })
      .strict()
      .optional(),
    audio: z
      .object({
        normalize: z
          .object({
            enabled: z.boolean(),
            target_lufs: z.number().gte(-30).lte(-5).default(-14),
            true_peak_db: z.number().gte(-9).lte(0).default(-1.5),
          })
          .strict(),
        ducking: z.object({ enabled: z.boolean() }).strict().optional(),
      })
      .strict(),
  })
  .strict()
  .describe(
    "Documento de edición de un clip (docs/07 §2). Fuente única de la vista previa y del render. Tiempos en milisegundos enteros; coordenadas normalizadas 0-1 respecto al lienzo. Las reglas que cruzan campos (referencias, solapes, duraciones) las validan las funciones semánticas de Python y TypeScript, comprobadas con los mismos fixtures.",
  );
export type EditProject = z.infer<typeof EditProjectSchema>;
