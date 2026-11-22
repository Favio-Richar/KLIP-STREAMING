# Diseño de interfaz (reglas obligatorias)

Klick es una herramienta de trabajo para creadores. La interfaz debe verse **nítida, sólida y precisa**: superficies planas, bordes definidos, contraste alto y tipografía clara. El editor tiene prioridad sobre cualquier decoración del dashboard.

## 1. Reglas visuales

### Prohibido (se verifica en CI)
- `backdrop-filter` en cualquier forma (`backdrop-blur-*`, vidrio esmerilado, *glassmorphism*).
- `filter: blur(...)` y clases `blur-*` en elementos de interfaz.
- `box-shadow` con radio de desenfoque mayor que 0 (sombras difusas). Se permiten **sombras duras sin desenfoque** (`0 0 0 1px color`) para simular bordes.
- Degradados vaporosos o de "malla", resplandores (*glow*), auras y manchas de color borrosas de fondo.
- Fondos translúcidos que dejen ver contenido detrás de un panel (excepto el velo de los diálogos, que es un color plano y uniforme).
- Emojis como iconos de interfaz.
- Texto sobre imagen sin una superficie sólida detrás.

> Esta regla es de **interfaz**. El video renderizado (por ejemplo, el layout `fit` con fondo ampliado) es contenido del usuario y no está sujeto a ella.

### Obligatorio
- Superficies **sólidas** con la elevación expresada por tono de fondo + borde de 1 px (nada de sombras).
- Un único color de acento. Los colores semánticos (éxito, aviso, error, información) solo comunican estado y siempre van acompañados de icono o texto.
- Iconos SVG (Lucide). Tamaños 14, 16, 20 y 24 px.
- Contraste: texto normal ≥ 4,5:1, texto grande e iconos/bordes interactivos ≥ 3:1, en ambos temas.
- Foco visible en todo elemento interactivo: `outline: 2px solid var(--accent); outline-offset: 2px`.
- `cursor: pointer` en elementos clicables; áreas táctiles ≥ 40 px en móvil/PWA y ≥ 28 px en el editor de escritorio.
- Respeto de `prefers-reduced-motion` y `prefers-color-scheme` (el usuario puede fijar el tema).
- Estados completos en cada componente de datos: vacío, cargando (esqueleto sólido, sin brillo animado), error con acción de reintento, confirmación en acciones destructivas.
- Textos en español de Chile, tuteo, directos y sin jerga; sin signos de exclamación salvo celebraciones puntuales.

### Precedencia
1. Instrucciones directas de Favio.
2. Este documento.
3. Recomendaciones de las skills de diseño (si una skill sugiere sombras suaves, vidrio o desenfoque, **se ignora esa parte**).

## 2. Tokens (fuente: `packages/ui/src/tokens/*.css`; valores iniciales)

### Color — tema oscuro (predeterminado)
| Token | Valor | Uso |
|---|---|---|
| `--bg-0` | `#0B0D10` | Fondo de la aplicación |
| `--bg-1` | `#11141A` | Paneles y barras |
| `--bg-2` | `#181C23` | Tarjetas, campos, filas |
| `--bg-3` | `#20252E` | Menús, popovers, diálogos, hover |
| `--border-1` | `#262B35` | Separadores y bordes |
| `--border-2` | `#343B48` | Bordes de énfasis, campos en foco previo |
| `--text-1` | `#ECEEF2` | Texto principal |
| `--text-2` | `#A4ADBB` | Texto secundario |
| `--text-3` | `#7A8394` | Texto terciario, marcadores de posición |
| `--accent` | `#B8F43C` | Acción primaria, selección, foco |
| `--accent-hover` | `#C9FA5E` | |
| `--accent-press` | `#A3DB2E` | |
| `--on-accent` | `#0B0D10` | Texto sobre el acento |
| `--success` | `#2DD4A7` | |
| `--warning` | `#F5A524` | |
| `--danger` | `#F2495C` | |
| `--info` | `#4C9AFF` | |
| `--scrim` | `rgba(0,0,0,0.64)` | Velo plano de diálogos |

### Color — tema claro
| Token | Valor |
|---|---|
| `--bg-0` | `#F6F7F9` |
| `--bg-1` | `#FFFFFF` |
| `--bg-2` | `#F0F2F5` |
| `--bg-3` | `#E6E9EE` |
| `--border-1` | `#DDE1E8` |
| `--border-2` | `#C5CBD6` |
| `--text-1` | `#12151A` |
| `--text-2` | `#4A5362` |
| `--text-3` | `#6E7787` |
| `--accent` | `#B8F43C` (relleno con texto `--on-accent`) |
| `--accent-text` | `#4D7A00` (acento usado como color de texto o icono sobre claro) |

Una prueba automática (`packages/ui/tests/contrast.test.ts`) calcula todos los pares texto/fondo y falla si no se cumplen los mínimos. Si un valor no pasa, se ajusta el token y se anota en `DECISIONES-TOMADAS-EN-EJECUCION.md`.

Colores de pistas del editor: video `#5B8DEF`, audio `#2DD4A7`, texto `#F5A524`, imagen `#C792EA`, subtítulos `#B8F43C`.

### Tipografía
- Interfaz: **Geist**. Códigos de tiempo, IDs y código: **Geist Mono** con `font-variant-numeric: tabular-nums`. Paquete `geist` (licencia abierta), cargado localmente (sin CDN).
- Escala: 11 · 12 · 13 · 14 · 16 · 20 · 24 · 32 px. Base 14 px en aplicación; 12–13 px en editor y tablas densas. Interlineado 1,4–1,5; títulos 1,2.
- Pesos: 400, 500, 600. Sin cursivas decorativas.

### Espaciado, forma y tamaños
- Escala de 4 px: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64.
- Radios: 4 (etiquetas), 6 (controles), 8 (tarjetas), 12 (diálogos).
- Bordes: 1 px. Altura de controles: 28 (denso), 32 (estándar), 40 (táctil).
- Barra superior: 48 px. Contenedores de página: máximo 1440 px, relleno 24 px.

### Elevación (sin sombras)
| Nivel | Fondo | Borde | Uso |
|---|---|---|---|
| e0 | `--bg-0` | — | Lienzo de la aplicación |
| e1 | `--bg-1` | `--border-1` | Paneles, barras |
| e2 | `--bg-2` | `--border-1` | Tarjetas, campos, filas |
| e3 | `--bg-3` | `--border-2` | Menús, popovers, diálogos |

### Movimiento
- Duraciones: `--dur-fast: 90ms`, `--dur-base: 150ms`, `--dur-slow: 240ms`.
- Curva de entrada: `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)`; salida: `--ease-in: cubic-bezier(0.55, 0, 1, 0.45)`.
- Solo se animan `transform` y `opacity`. Nada de animar `width`, `height`, `top` o `left`.
- Entradas con `ease-out`; interacciones ≤ 150 ms; diálogos y paneles ≤ 240 ms.
- **No animar** acciones iniciadas con teclado (atajos), el arrastre de la línea de tiempo, ni la reproducción. El arrastre sigue al puntero 1:1 y se puede interrumpir.
- Esqueletos de carga sólidos con ligero cambio de opacidad; sin barridos de brillo.

## 3. Estructura de la aplicación

- **Navegación superior** (sin menú lateral global): `Klick` · Panel · Directos · Biblioteca · Clips · Agente · Publicaciones · Analítica | selector de organización · notificaciones · usuario.
- Los paneles laterales aparecen **solo dentro de estudios** (editor, agente).
- **Editor de clips** (pantalla de mayor prioridad):
  - Centro: vista previa 9:16 con controles de reproducción.
  - Izquierda: pestañas Recursos · Plantillas · Subtítulos · Texto.
  - Derecha: Inspector (propiedades del elemento, reencuadre, audio).
  - Abajo: línea de tiempo multipista con forma de onda, miniaturas y marcadores; zoom horizontal; ajuste magnético.
  - Paneles redimensionables y con tamaño recordado por usuario.
  - Atajos: `Espacio` reproducir/pausar · `J/K/L` retroceder/pausar/avanzar · `I/O` entrada/salida · `C` dividir · `Supr` eliminar · `Ctrl+Z / Ctrl+Shift+Z` deshacer/rehacer · `Ctrl+S` guardar versión · `M` marcador · `←/→` un fotograma · `Shift+←/→` un segundo · `Ctrl+E` exportar.
- **Estudio del agente:** conversación a la izquierda; plan, pasos con estado, costos y resultados a la derecha; tarjetas de aprobación con la acción exacta y botones Aprobar/Rechazar.
- **Directos:** monitor de la sesión, estado de la señal, línea de tiempo de marcadores, botón y atajo "Clip ahora", lista de clips generados.

## 4. Pantallas por fase

| Fase | Pantallas |
|---|---|
| 0 | Acceso (ingresar, registrarse, recuperar, verificar correo, MFA), crear organización, miembros e invitaciones, perfil y seguridad, estructura base con navegación |
| 1 | Panel (recientes), Biblioteca (cuadrícula/lista, filtros, búsqueda), subida con progreso, detalle de video, editor de clips, exportaciones |
| 2 | Visor de transcripción con búsqueda, revisión de momentos destacados (aceptar/rechazar/calificar), proveedores y modelos de IA, consumo |
| 3 | Estudio del agente, historial de ejecuciones, bandeja de aprobaciones, memoria |
| 4 | Directos (lista y detalle de sesión), configuración de OBS, ajustes de buffer |
| 5 | Conexiones, calendario de publicaciones, borradores y aprobación, estado por red |
| 6 | Centro MCP (clientes autorizados, scopes, auditoría, servidores externos), automatizaciones (lista, diseñador, ejecuciones) |
| 7 | Analítica (resumen, clips, canales) |
| 8 | Planes y facturación, administración (usuarios, organizaciones, colas, costos) |

## 5. Componentes base (`packages/ui`)
Button, IconButton, Input, Textarea, Select, Combobox, Checkbox, Switch, Slider, Tabs, Tooltip, Popover, Menu, Dialog, Sheet, Toast (Sonner), Table (densa, virtualizada), DataList, Badge, StatusDot, ProgressBar, Skeleton, EmptyState, ErrorState, ConfirmDialog, Kbd, Timecode, ResizablePanels, VideoPlayer, WaveformView, TimelineRuler, TrackLane, ClipBlock, KeyframeHandle, CaptionEditor, JobProgressCard, ApprovalCard, PlanStepList, ConnectionBadge.

Cada componente: variantes, estados (hover, foco, activo, deshabilitado, cargando, error), pruebas de accesibilidad (axe) y entrada en la guía visual en `apps/web/app/(dev)/ui` (solo desarrollo).

## 6. Uso de las skills de diseño

Instalación (la ejecuta Claude Code en la tarea F0-03; si un comando cambió, usar el README del repositorio correspondiente):

| Skill | Instalación | Para qué se usa en Klick |
|---|---|---|
| `ui-ux-pro-max` | `npx ui-ux-pro-max-cli init --ai claude` (proyecto) | Generar y persistir el sistema de diseño: `python .claude/skills/ui-ux-pro-max/scripts/search.py "creator video editing tool dark dashboard" --design-system --persist -p "Klick"`. La salida (`design-system/klick/MASTER.md`) se **reconcilia** con este documento: gana este documento. Se descarta el estilo *glassmorphism* y cualquier sombra difusa |
| `emilkowalski/skills` | `npx skills@latest add emilkowalski/skills` | Revisión de animaciones (`review-animations`), coreografía de paneles (`choreograph`), rendimiento (`animation-performance`), pruebas de datos extremos (`break-ui`), y registro de correcciones de gusto en `DESIGN.md` (`learn-taste`). Sus recomendaciones de sombras suaves **no aplican** |
| `huashu-design` | `npx skills add alchaincyf/huashu-design` | Prototipos HTML navegables de las pantallas clave y la página pública del producto, y animaciones de demostración. Requiere Node, ffmpeg y Playwright. No se usa para generar código de producción |
| `frontend-design` (Anthropic) | Disponible como skill del repositorio `anthropics/skills` | Evitar estéticas genéricas al construir cada pantalla: jerarquía, tipografía y composición con intención |

Orden de uso por pantalla: (1) `ui-ux-pro-max` define patrones y estructura → (2) `frontend-design` guía la composición → (3) se implementa con los tokens → (4) `emilkowalski` revisa movimiento y bordes extremos → (5) la verificación automática de este documento da el visto bueno.

## 7. Verificación automática
- `scripts/check-design-rules.mjs` (parte de `pnpm lint` y CI) recorre CSS, TSX y configuración de Tailwind y **falla** si encuentra: `backdrop-filter`, `backdrop-blur`, `filter: blur`, clases `blur-*`, `shadow-*` distintas de `shadow-none`, `box-shadow` con desenfoque > 0, `drop-shadow`, `bg-opacity` aplicado a paneles, o degradados `radial-gradient` decorativos. Excepciones solo con comentario `/* design-rule-exception: motivo */` revisado por humano.
- Prueba de contraste de tokens (ver §2).
- Playwright: capturas de referencia por pantalla en ambos temas y tres anchos (375, 1024, 1440 px) con tolerancia de píxeles baja; `axe` sin violaciones graves.
- Cada fase deja capturas en `docs/entregas/F<n>.md` para que Favio las revise cuando quiera; **la ejecución no espera su respuesta**. Sus correcciones de gusto se registran con la skill `learn-taste` en `DESIGN.md`.
