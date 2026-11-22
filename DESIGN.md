# DESIGN.md — Klick

Registro vivo de decisiones de diseño. La fuente normativa es `docs/08-diseno-ui.md`; los tokens vivirán en `packages/ui/src/tokens/*.css`.

## Precedencia
1. Instrucciones directas de Favio.
2. `docs/08-diseno-ui.md`.
3. Skills de diseño (`ui-ux-pro-max`, `frontend-design`, set de emilkowalski): se ignora cualquier recomendación de sombras suaves, vidrio o desenfoque.

## Reconciliación de `ui-ux-pro-max` (F0-03)
Salida persistida en `design-system/klick/MASTER.md`.

| Propuesta de la skill | Decisión | Motivo |
|---|---|---|
| Acento rosa `#EC4899` + azul CTA | Descartada. Acento único `#B8F43C` | docs/08: un solo color de acento |
| Fira Code / Fira Sans (Google Fonts) | Descartada. Geist y Geist Mono locales | docs/08: sin CDN |
| "Improved shadows" | Descartada | Prohibidas las sombras difusas |
| Checklist previa a entrega | Adoptada | Compatible con docs/08 |

## Skills (estado verificado el 2026-11-22; no se reinstalan)
- `ui-ux-pro-max`: en `~/.codex/skills/ui-ux-pro-max`. Se ejecuta con `python ~/.codex/skills/ui-ux-pro-max/scripts/search.py ...`.
- emilkowalski (`review-animations`, `animation-performance`, `learn-taste`, `improve-animations`): en `~/.claude/skills/synced`.
- `frontend-design`: plugin `claude-plugins-official`.
- `huashu-design`: **no encontrada**. Solo se usa para prototipos HTML, no bloquea nada.

## Correcciones de gusto (`learn-taste`)
_(vacío: se registran aquí a medida que Favio corrige pantallas)_
