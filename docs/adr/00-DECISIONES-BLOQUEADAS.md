# Decisiones de arquitectura bloqueadas

Estas decisiones están cerradas. No se reabren durante la ejecución. Si una resulta imposible de cumplir, se documenta en `DECISIONES-TOMADAS-EN-EJECUCION.md` y se avisa a Favio.

Fecha de cierre: 22 de noviembre de 2026.

---

## Correcciones respecto al plan original (ChatGPT)

El plan original es sólido en arquitectura. Tras auditar arquitectura, base de datos y almacenamiento se corrigieron estos puntos. Si hay diferencia con `docs/anexos/plan-original-chatgpt.md`, **manda este documento**.

| # | Punto del plan original | Problema | Decisión final |
|---|---|---|---|
| C1 | Lista de entidades sin campos ni relaciones | No es un esquema ejecutable | Esquema completo en `docs/04-base-de-datos.md` |
| C2 | Faltan tablas | Sin `projects`, `transcripts`, `transcript_segments`, `highlight_candidates`, `caption_tracks`, `media_derivatives`, `storage_objects`, `outbox_events`, `idempotency_keys`, `api_tokens`, `prompt_templates`, `devices`, `retention_policies` no se puede construir el flujo | Todas definidas |
| C3 | `social_accounts` + `platform_connections` + `oauth_grants` | Tres tablas para lo mismo; `oauth_grants` se confundía con concesiones MCP | Una sola tabla `platform_connections`. Las concesiones MCP son `mcp_grants` |
| C4 | `destinations` + `schedules` | Redundantes | `publications` lleva conexión destino y `scheduled_for`; `publication_batches` agrupa una publicación en varias redes |
| C5 | Búsqueda: PostgreSQL FTS y vectorial "cuando exista un caso" | La búsqueda semántica de transcripciones y la memoria del agente son casos desde la fase 2 | `pgvector` activo desde la primera migración; FTS más vectorial combinados |
| C6 | RLS "defensa adicional opcional" | Añadirla después obliga a reescribir consultas y pruebas | RLS obligatoria desde la primera tabla con `org_id` |
| C7 | Celery o Dramatiq; "motor durable a evaluar" | Ambigüedad | Dramatiq + Redis como transporte. La tabla `jobs` en PostgreSQL es la fuente de verdad |
| C8 | Eventos sin garantía de entrega | Un cambio en BD y su evento pueden desincronizarse | Patrón *transactional outbox* (`outbox_events`) |
| C9 | Sin canal de progreso en tiempo real | La interfaz necesitaría sondeo | SSE para progreso de jobs y respuestas del agente |
| C10 | Solo aplicación web | Falta PWA y escritorio | PWA instalable más Tauri 2 |
| C11 | Almacenamiento solo "S3 compatible" | Faltan abstracción local, claves, deduplicación y ciclo de vida | `StorageBackend` con S3/MinIO; claves por hash; limpieza por referencias |
| C12 | Diseño en Figma antes del frontend | Doble trabajo y desfase | Sistema de diseño directamente como tokens y componentes en código |
| C13 | `ai_requests` guarda todo | Riesgo de privacidad y volumen | Métricas siempre; cargas completas solo si la organización lo activa y con retención corta |
| C14 | Tablas de alto volumen sin particionado | Crecimiento sin control | Particionado mensual en `stream_segments`, `ai_requests`, `tool_calls`, `mcp_tool_audits`, `audit_events`, `clip_metrics` |
| C15 | Embeddings sin dimensión definida | Cambiar de modelo obliga a recalcular todo | BGE-M3, 1024 dimensiones, fijo |
| C16 | MCP: fecha de especificación sin comprobar | Verificada: la especificación **2026-07-28** existe y es la vigente | Se fija en esa versión (ADR-016) |
| C17 | Restos de interfaz copiados ("GitHub+2", "Copiar checklist", "0 de 12 completadas") | Ruido | Eliminados |

---

## ADR-001 Nombre y estructura
- Nombre del producto: **Klick**. Repositorio: `klick`. Carpeta de trabajo: `C:\Users\favio\KLICK-STREAMING`.
- Monorepo con `pnpm` + Turborepo para JavaScript y `uv` (workspace) para Python.
- Verificación de dominio y marca pendiente de Favio; no bloquea el desarrollo.

## ADR-002 Modelo de despliegue
- **Autoalojado primero**: todo corre en el PC de Favio con Docker Compose (Windows 11 + Docker Desktop con WSL2 y GPU NVIDIA) y también se puede desplegar en un servidor Linux con el mismo Compose.
- Multi-tenant desde el inicio (`org_id` + RLS). Facturación y planes se implementan al final (fase 8).
- Un solo backend con una sola base PostgreSQL. No existe modo SQLite ni base local duplicada.

## ADR-003 Backend
- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 asíncrono, Alembic.
- **Monolito modular**: `services/api` con un paquete por módulo (`identity`, `orgs`, `media`, `transcripts`, `highlights`, `clips`, `render`, `ai_gateway`, `agents`, `mcp`, `live`, `publishing`, `automations`, `analytics`, `billing`, `admin`, `notifications`, `usage`).
- Cada módulo expone una capa `service.py` (casos de uso), `repo.py` (acceso a datos), `schemas.py`, `router.py` y, si aplica, `tools.py` (herramientas del agente/MCP).
- Los workers (`media-worker`, `ai-worker`, `publishing-worker`, `scheduler`) importan los mismos módulos; son procesos distintos, no copias de código.

## ADR-004 Base de datos
- PostgreSQL 17 con extensiones `pgvector`, `citext`, `pg_trgm`.
- IDs UUIDv7 generados en la aplicación (librería `uuid-utils`). Fechas `timestamptz`. Dinero en `numeric`.
- Roles de base de datos: `klick_owner` (migraciones), `klick_app` (API y workers, **sin** `BYPASSRLS`) y `klick_system` (scheduler/reconciliador, con `BYPASSRLS`, uso mínimo y auditado).
- RLS: cada transacción ejecuta `SET LOCAL app.org_id = '<uuid>'`; las políticas comparan `org_id` con ese valor. Detalle en `docs/04`.
- Particionado mensual declarativo en tablas de alto volumen; el `scheduler` crea particiones futuras y borra las vencidas según retención.

## ADR-005 Almacenamiento de archivos
- Interfaz `StorageBackend` con una implementación S3 (MinIO en local y autoalojado; S3 o Cloudflare R2 en nube).
- Las URL de subida y descarga son firmadas y de corta duración. El navegador sube directo al almacenamiento (multipart).
- Claves: `org/{org_id}/assets/{asset_id}/original`, `.../derivatives/{kind}`, `org/{org_id}/renders/{render_job_id}.mp4`, `org/{org_id}/live/{session_id}/seg_{seq}.m4s`.
- Deduplicación por `sha256` dentro de la organización mediante `storage_objects`.
- Limpieza: un job recolector borra objetos sin referencias tras un periodo de gracia de 24 horas.
- La base solo guarda metadatos y referencias; nunca binarios.

## ADR-006 Trabajos y colas
- Tabla `jobs` = fuente de verdad (estado, progreso, intentos, latido). Dramatiq + Redis = transporte.
- Colas: `default`, `media` (CPU/FFmpeg), `ai` (GPU), `publish`.
- Un **reconciliador** (en `scheduler`) reencola jobs con latido vencido y marca `EXPIRED` los que superan su límite.
- Reintentos con *backoff* exponencial y límite. Las tareas con efecto externo comprueban el estado remoto antes de reintentar.

## ADR-007 Eventos y tiempo real
- Eventos de dominio versionados (`stream.started`, `video.ingested`, `highlights.detected`, `clip.rendered`, `agent.approval_requested`, `publication.completed`, `job.failed`, etc.) con `id`, `schema_version`, `source`, `org_id`, `correlation_id`, `occurred_at`, `payload`.
- Se escriben en `outbox_events` dentro de la misma transacción que el cambio; un publicador los envía a Redis Streams.
- Tiempo real hacia el navegador: **SSE** (`/api/v1/events/stream`) alimentado por Redis pub/sub. No se usa WebSocket para la interfaz.

## ADR-008 Frontend
- Next.js (App Router), React, TypeScript estricto, Tailwind, shadcn/ui sobre Radix, TanStack Query, Zustand, `next-intl` (es-CL por defecto, en secundario).
- Cliente de API TypeScript generado desde OpenAPI (`openapi-typescript` + `openapi-fetch`) en `packages/client-sdk`. No se escriben llamadas HTTP a mano.
- PWA: manifiesto y *service worker* con Serwist. Instalable; la interfaz funciona en línea (no se promete modo sin conexión para edición).
- Navegación superior (sin menú lateral global). Paneles laterales solo dentro de estudios (editor, agente).

## ADR-009 Escritorio
- **Tauri 2** como cascarón que carga la aplicación web (local o alojada).
- Funciones nativas incluidas: selector y arrastre de archivos del sistema, bandeja del sistema, notificaciones nativas, enlaces profundos `klick://`, actualización automática y **atajos globales** (por ejemplo `Ctrl+Alt+K` = "clip ahora" durante un directo).
- No incluye render local ni base local. El render lo hacen los workers del stack, que en el PC de Favio usan su GPU. La extensión "agente local" queda fuera del alcance de la V1.

## ADR-010 Editor de video
- Línea de tiempo propia en React. Vista previa con `<video>` y compuesta en `<canvas>`.
- El **documento de edición** (`EditProject`) es un JSON versionado, definido una sola vez en `packages/video-project-schema` (JSON Schema como fuente; modelos Pydantic y Zod generados).
- El render traduce el mismo documento a un grafo de filtros de FFmpeg. Los subtítulos se generan como ASS y se queman con libass.
- Prueba obligatoria de paridad: fotogramas de referencia de vista previa frente a render, con umbral SSIM definido en `docs/11`.
- Alcance V1 del editor: recorte, división, unión, reencuadre y zoom con *keyframes*, velocidad, capas de texto e imagen, subtítulos, mezcla de audio con normalización, layouts webcam+gameplay, deshacer/rehacer, guardado automático y presets 9:16, 16:9, 1:1 y 4:5. Transiciones y filtros avanzados: fase posterior a la V1.

## ADR-011 Transcripción
- `faster-whisper` en GPU con modelo multilingüe grande y marcas de tiempo por palabra; VAD previo. Se ejecuta en el `ai-worker`.
- El AI Gateway puede enrutar la tarea `transcription` a un proveedor externo como alternativa configurable por organización.
- Diarización de hablantes: fuera de la V1.

## ADR-012 Detección de momentos destacados
Pipeline por señales combinadas con ponderación configurable por organización:
1. Energía y picos de audio (loudness EBU R128, risas/gritos por energía y espectro).
2. Contenido del texto (puntuación por LLM sobre ventanas de transcripción).
3. Actividad del chat (cuando existe: mensajes por minuto, emotes).
4. Cambios de escena (PySceneDetect).
5. Visión por muestreo de fotogramas con modelo multimodal (opcional por tarea).
6. Marcadores manuales y de atajo (peso máximo).
Salida: candidatos con `start_ms`/`end_ms` ajustados a límites de frase, puntaje 0–100, señales desglosadas, categoría y motivo. Separación mínima entre candidatos configurable. El puntaje es una estimación, no una promesa de viralidad.

## ADR-013 Reencuadre vertical
- MediaPipe (licencia Apache-2.0) para rostro/persona con suavizado temporal. **No** se usa Ultralytics/YOLO por su licencia AGPL.
- Layouts: `follow` (recorte que sigue al sujeto), `split` (webcam arriba, gameplay abajo), `fit` (video completo centrado con fondo configurable: color sólido, imagen o copia ampliada). Estas opciones son de **salida de video**, no de interfaz; las reglas visuales de la UI no se aplican al contenido renderizado.

## ADR-014 AI Gateway
- Módulo propio `ai_gateway` con adaptadores finos: OpenAI, Anthropic, Google y "compatible con OpenAI" (Ollama, vLLM, LM Studio). Los SDK oficiales solo se importan dentro de los adaptadores. No se usa LiteLLM.
- Contrato interno único: `chat`, `structured`, `vision`, `embed`, `transcribe`, con streaming y llamadas a herramientas normalizadas.
- Registro de capacidades en `ai_models.capabilities` y política por tarea en `model_policies` (modelo primario + alternos compatibles).
- *Fallback* solo entre modelos con capacidades requeridas equivalentes y nunca después de un efecto externo ya producido.
- Presupuestos por ejecución, día y organización; medición de tokens, latencia y costo en `ai_requests` y `usage_records`.
- Claves BYOK y de plataforma cifradas con cifrado de sobre (AES-256-GCM); la clave maestra viene de variable de entorno/secret manager.
- Prompts versionados en `prompt_templates` con esquema de salida; pruebas de contrato por adaptador con un adaptador `fake` determinista para desarrollo y CI.

## ADR-015 Agente
- Orquestador propio con bucle de herramientas y **máquina de estados persistida** (`agent_runs`, `agent_steps`, `tool_calls`).
- Los "agentes especialistas" (Live, Highlights, Editing, Captions, Creative, Publishing, Analytics, Automation) son **perfiles**: prompt versionado + lista blanca de herramientas + política de modelo. No son procesos separados. El orquestador delega mediante la herramienta interna `delegate(profile, goal)`, que crea una ejecución hija con presupuesto propio.
- Niveles de riesgo de herramienta: `R0` lectura, `R1` escritura reversible, `R2` consumo costoso (GPU/IA), `R3` efecto externo (publicar), `R4` destructiva. `R3` y `R4` exigen aprobación humana salvo regla explícita de la organización; `R2` se limita por presupuesto.
- Estados: `CREATED → PLANNING → QUEUED → RUNNING → VALIDATING → AWAITING_APPROVAL → COMPLETED`; alternos `FAILED`, `RETRYING`, `CANCELLED`, `EXPIRED`, `PARTIAL_SUCCESS`.
- Memoria: entradas por usuario/organización/proyecto con embedding; solo se guardan preferencias y decisiones aprobadas, nunca contenido de videos como instrucciones.

## ADR-016 MCP
- SDK oficial de Python, especificación **2026-07-28** (núcleo sin estado, `server/discover`, sin `Mcp-Session-Id`), transporte HTTP.
- El servidor MCP es *resource server* OAuth 2.1. La propia API de Klick actúa como servidor de autorización: PKCE, metadatos de servidor de autorización (RFC 8414), metadatos de recurso protegido (RFC 9728), indicadores de recurso (RFC 8707, el token lleva `aud` = URL del MCP), registro dinámico de clientes (RFC 7591) y documentos de metadatos de cliente (CIMD).
- Prohibido el *token passthrough*: el MCP solo acepta tokens emitidos para su propia audiencia.
- Cada herramienta MCP es una envoltura fina sobre un servicio de aplicación (regla 1). Operaciones largas devuelven `job_id`; se adopta la extensión *Tasks* de la especificación cuando el SDK la ofrezca estable.
- Cliente MCP (conexiones salientes a servidores de terceros) en la fase 6, con lista blanca de herramientas por organización.

## ADR-017 Identidad
- Autenticación propia en FastAPI: `argon2id`, sesiones web con cookie `HttpOnly`+`Secure`+`SameSite=Lax` y refresco rotatorio, MFA TOTP con códigos de recuperación, inicio de sesión con Google (OIDC), tokens personales de API (almacenados como hash).
- RBAC con roles de sistema `owner`, `admin`, `editor`, `reviewer`, `viewer` sobre permisos granulares; además comprobación sobre el recurso concreto.

## ADR-018 Directos y captura
- Ingesta propia: **MediaMTX** recibe RTMP/SRT desde OBS; `live-ingest` corta segmentos fMP4 hacia un buffer circular en almacenamiento y registra `stream_segments`.
- Control de OBS por **obs-websocket v5** (buffer de repetición, marcadores, estado de grabación).
- Twitch: API oficial (EventSub para inicio/fin de directo y creación de clips). YouTube y Kick: solo canal propio mediante sus APIs oficiales y permisos del usuario. Sin descarga de transmisiones ajenas.
- Al terminar un directo, los segmentos se unen sin recodificar en una grabación completa que pasa a ser un `media_asset`. Los segmentos se conservan `buffer_retention` (24 h por defecto) y luego se limpian.

## ADR-019 Publicación
- Interfaz `PublishAdapter` por red: `validate`, `publish`, `get_status`, `reconcile`, `refresh_token`.
- Orden de implementación: YouTube (Shorts y videos) → TikTok → Instagram Reels → Facebook. Cada adaptador tiene su versión `fake`.
- Estados: `draft`, `awaiting_approval`, `scheduled`, `sending`, `processing`, `published`, `error`, `canceled`.
- Nunca se publica automáticamente en una conexión sin regla de publicación explícita y aprobada.
- Las solicitudes de auditoría/revisión de TikTok y Meta se inician en la fase 0; mientras tanto se trabaja con modo sandbox/privado y el adaptador `fake`.

## ADR-020 Observabilidad
- Logs JSON con `structlog`; trazas y métricas con OpenTelemetry; Prometheus y Grafana como perfil opcional de Compose; Sentry opcional por variable de entorno.
- Cada request, job y ejecución del agente lleva `request_id`/`correlation_id`.

## ADR-021 Entorno de desarrollo
- Infraestructura y workers en Docker Compose (perfiles: `core` = PostgreSQL, Redis, MinIO, MediaMTX; `gpu` = ai-worker con NVIDIA; `observability`).
- API con recarga en vivo, `web` y `desktop` se ejecutan nativos en Windows para tener HMR rápido.
- FFmpeg y todas las dependencias de IA van **dentro de las imágenes de contenedor** con versiones fijadas.
- CI en GitHub Actions sobre Linux.

## ADR-022 Diseño
- Reglas visuales obligatorias en `docs/08-diseno-ui.md`: **prohibido** `backdrop-filter`, `filter: blur`, vidrio esmerilado, sombras difusas y degradados vaporosos.
- Tema oscuro predeterminado y tema claro desde el inicio, ambos por tokens.
- Tipografías: Geist (interfaz) y Geist Mono (códigos de tiempo).
- Precedencia ante conflicto: reglas del usuario → `docs/08` → skills de diseño.

## ADR-023 Pruebas y calidad
- pytest, Vitest, Playwright. Pruebas de aislamiento RLS, paridad vista previa/render, evaluación del agente y evaluación de highlights son obligatorias y bloquean CI.

## ADR-024 Alcance de la V1
- Incluye: fases 0 a 6 (núcleo, clips, IA, agente, directos, publicación, MCP y automatizaciones).
- Después: analítica avanzada, facturación y administración (fase 7 y 8) y endurecimiento (fase 9).
- Fuera de la V1: render local en escritorio, diarización, transiciones y filtros avanzados, residencia de datos configurable, IA privada empresarial.
