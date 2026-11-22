# Arquitectura

Complementa `docs/adr/00-DECISIONES-BLOQUEADAS.md`. Aquí se define **qué procesos existen, dónde vive cada dato y cómo fluyen las operaciones**.

## 1. Vista general

```
 Navegador (PWA)      Escritorio (Tauri)      Cliente MCP externo      OBS
        │                     │                       │                 │
        └──────────┬──────────┘                       │          RTMP/SRT + obs-websocket
                   ▼                                  ▼                 ▼
        ┌──────────────────────────────────────────────────┐     ┌──────────────┐
        │                    API (FastAPI)                 │     │  MediaMTX    │
        │  REST /api/v1 · SSE · MCP /mcp · OAuth server    │     └──────┬───────┘
        │  ┌────────────────────────────────────────────┐  │            ▼
        │  │ Capa de servicios de aplicación (única)    │  │     ┌──────────────┐
        │  │ identity · orgs · media · transcripts ·    │  │     │ live-ingest  │
        │  │ highlights · clips · render · ai_gateway · │  │     │ (segmentos,  │
        │  │ agents · live · publishing · automations · │  │     │  OBS ws,     │
        │  │ analytics · billing · usage · notifications│  │     │  Twitch)     │
        │  └────────────────────────────────────────────┘  │     └──────┬───────┘
        └───────┬──────────────┬───────────────┬───────────┘            │
                │              │               │                        │
        ┌───────▼──────┐ ┌─────▼─────┐   ┌─────▼──────┐          ┌──────▼──────┐
        │ PostgreSQL   │ │  Redis    │   │ Almacén S3 │◄─────────┤  segmentos  │
        │ + pgvector   │ │ colas,    │   │ (MinIO)    │          └─────────────┘
        │ + RLS        │ │ pub/sub   │   └─────▲──────┘
        └───────▲──────┘ └─────┬─────┘         │
                │              │               │
        ┌───────┴──────────────▼───────────────┴───────────────────┐
        │ Workers (Dramatiq): media · ai (GPU) · publish · default │
        │ scheduler (cron, reconciliador, particiones, GC)         │
        └───────────────────────────────────────────────────────────┘
```

## 2. Procesos y contenedores

| Servicio | Tecnología | Función | Perfil Compose |
|---|---|---|---|
| `postgres` | PostgreSQL 17 + pgvector | Datos relacionales, vectores, estado de jobs | core |
| `redis` | Redis 7 | Cola Dramatiq, pub/sub SSE, Redis Streams de eventos, limitación de tasa | core |
| `minio` | MinIO | Almacén S3 compatible | core |
| `mediamtx` | MediaMTX | Recibe RTMP/SRT | core |
| `api` | FastAPI | REST, SSE, MCP, servidor OAuth | core |
| `worker-default` | Dramatiq | Outbox, notificaciones, webhooks, tareas ligeras | core |
| `worker-media` | Dramatiq + FFmpeg | Probe, derivados, render, concatenado | core |
| `worker-ai` | Dramatiq + CUDA | Transcripción, embeddings, visión, highlights | gpu |
| `worker-publish` | Dramatiq | Subidas a redes, reconciliación | core |
| `scheduler` | Python | Cron, reconciliador de jobs, particiones, GC, renovación de tokens | core |
| `live-ingest` | Python | Segmentación, OBS websocket, EventSub de Twitch, buffer | core |
| `web` | Next.js | Interfaz (nativo en desarrollo) | — |
| `prometheus`, `grafana` | — | Métricas | observability |

Los workers y el `scheduler` usan el mismo paquete Python que la API. Cada uno es un punto de entrada distinto.

## 3. Dónde vive cada dato

| Dato | Lugar | Notas |
|---|---|---|
| Usuarios, organizaciones, permisos | PostgreSQL | RLS por `org_id` |
| Metadatos de videos, clips, transcripciones, candidatos | PostgreSQL | Transcripciones por segmento con `tsvector` y `vector(1024)` |
| Documento de edición | PostgreSQL (`jsonb`) | Versionado; instantáneas inmutables en `clip_versions` |
| Estado de jobs, ejecuciones del agente, publicaciones | PostgreSQL | Fuente de verdad; Redis solo transporta |
| Videos originales, derivados, renders, logs de render | Almacén S3 | Referenciados por `storage_objects` |
| Segmentos del buffer en vivo | Almacén S3 | Expiran por `buffer_retention` |
| Credenciales de plataformas y de proveedores de IA | PostgreSQL cifradas (AES-256-GCM, cifrado de sobre) | La clave maestra no está en la base |
| Sesiones de login | PostgreSQL (`sessions`) | Refresco rotatorio; caché opcional en Redis |
| Códigos de autorización OAuth de corta vida | Redis (TTL 60 s) | |
| Mensajes de progreso y tokens de respuesta del agente | Redis pub/sub → SSE | No se persisten aquí; el estado final queda en PostgreSQL |
| Límites de tasa | Redis | |
| Cargas completas de IA (prompts y respuestas) | PostgreSQL (`ai_request_payloads`), solo si la organización lo activa | Retención 7 días por defecto |
| Registros de auditoría | PostgreSQL particionado | Retención configurable, mínimo 365 días |

## 4. Flujos principales

### 4.1 Subida → clip exportado
1. Cliente: `POST /assets/uploads` → la API crea `uploads` + `storage_objects(status=uploading)` y devuelve URLs firmadas multipart.
2. Cliente sube directo al almacén y llama `POST /assets/uploads/{id}/complete`.
3. API verifica tamaño y `sha256` (o lo calcula en worker), crea `media_assets(status=processing)` y encola `media.probe`.
4. `worker-media`: FFprobe → metadatos; genera `proxy_720p`, miniaturas, *sprite* de línea de tiempo, forma de onda y audio 16 kHz mono. Marca `ready`.
5. (Fase 2) Se encolan `ai.transcribe` y `ai.detect_highlights` si la configuración del proyecto lo pide.
6. Usuario crea un clip: `POST /clips` con rango y plantilla → se crea `clips` + `edit_projects` con el documento inicial.
7. El editor hace *autosave* con control de concurrencia optimista (`revision`).
8. `POST /clips/{id}/render` → crea `clip_versions` (instantánea) + `render_jobs` + `jobs(kind=render)`.
9. `worker-media` traduce el documento a FFmpeg, publica progreso por Redis, sube el MP4 y marca `succeeded`.
10. La interfaz recibe SSE `render.progress` y `render.completed` y ofrece la descarga firmada.

### 4.2 Orden al agente
Ejemplo: *"Toma mi último directo, saca los 5 mejores momentos, hazlos verticales con mi plantilla y subtítulos en español, y déjalos para revisar."*
1. `POST /agent/runs` (o herramienta MCP): se crea `agent_runs(CREATED)` con presupuesto.
2. Orquestador (worker `default`) pasa a `PLANNING`: genera un plan estructurado (`agent_steps`) validado por esquema.
3. `QUEUED/RUNNING`: ejecuta pasos. Cada herramienta se invoca a través del servicio de aplicación con la identidad y permisos del usuario que inició la ejecución.
4. Herramientas largas devuelven `job_id`; el paso queda en espera y se reanuda por evento `job.succeeded`.
5. `VALIDATING`: comprobaciones técnicas (duración, formato, audio, sincronía) y de calidad.
6. `AWAITING_APPROVAL` cuando una herramienta es `R3/R4` o el usuario pidió revisión. Crea `approvals` y notificación.
7. Con la aprobación, continúa; si se rechaza, la ejecución termina `PARTIAL_SUCCESS` o `CANCELLED` según corresponda.
8. `COMPLETED`: resultados, costos y trazas quedan consultables.

### 4.3 "Clip ahora" durante un directo
1. OBS emite hacia MediaMTX; `live-ingest` crea `live_sessions(status=live)` y segmentos.
2. Favio pulsa el atajo global (escritorio) o el botón (web) → `POST /live/sessions/{id}/markers` con `kind=hotkey`.
3. `live-ingest` toma los segmentos de `[t − pre_roll, t + post_roll]` (por defecto 30 s y 15 s; configurables).
4. Se une sin recodificar, se crea `media_assets(source_type=live_clip)` y una propuesta de clip.
5. Si hay regla de automatización, se transcribe, se subtitula y se deja en revisión.

### 4.4 Publicación
1. Se crea `publication_batches` y una `publications(status=draft)` por red destino, con título, descripción y etiquetas adaptados.
2. Aprobación humana (`awaiting_approval → scheduled`) o regla explícita de la organización.
3. A la hora, el `scheduler` encola `publish.send` con `idempotency_key`.
4. `worker-publish`: refresca token si hace falta, **reconcilia** (¿ya existe esta publicación en la plataforma?), envía, pasa a `processing`.
5. Un job de reconciliación consulta el estado remoto hasta `published` o `error`. Cada intento queda en `delivery_attempts`.

## 5. Eventos y outbox
- Los servicios insertan en `outbox_events` dentro de la misma transacción que su cambio.
- `worker-default` publica los pendientes en Redis Streams (`klick:events`) y marca `published_at`. Entrega al menos una vez; los consumidores son idempotentes.
- Consumidores: reanudación de pasos del agente, automatizaciones, notificaciones, webhooks salientes, SSE.
- Cada evento: `id`, `type`, `schema_version`, `source`, `org_id`, `correlation_id`, `occurred_at`, `payload`. Los esquemas viven en `packages/contracts/events/*.json`.

## 6. Contratos
- La API genera OpenAPI. De ahí salen el cliente TypeScript y la validación de contratos en CI.
- Los esquemas de eventos y del documento de edición se mantienen como JSON Schema en `packages/contracts` y `packages/video-project-schema`; Pydantic y Zod se generan.
- Las herramientas del agente y de MCP se definen **una vez** en `klick/<modulo>/tools.py` con un decorador que registra nombre, descripción, esquema de entrada/salida, `risk_level` y `required_scopes`. De ese registro se publican ambas superficies.

## 7. Estructura final del repositorio

```
klick/                               (C:\Users\favio\KLICK-STREAMING)
├── CLAUDE.md
├── README.md
├── package.json  pnpm-workspace.yaml  turbo.json
├── pyproject.toml  uv.lock           (workspace Python)
├── docker-compose.yml  .env.example
├── apps/
│   ├── web/                          Next.js
│   └── desktop/                      Tauri 2
├── services/
│   ├── api/                          paquete Python `klick` (módulos del ADR-003)
│   │   ├── src/klick/<modulo>/{service,repo,schemas,router,tools}.py
│   │   ├── src/klick/core/           config, db, rls, seguridad, errores, logging, eventos, jobs
│   │   ├── alembic/
│   │   └── tests/
│   ├── worker/                       puntos de entrada: default, media, ai, publish, scheduler
│   └── live-ingest/
├── packages/
│   ├── ui/                           tokens, componentes, reglas de diseño
│   ├── client-sdk/                   cliente generado
│   ├── contracts/                    esquemas de eventos y herramientas
│   ├── video-project-schema/         esquema del documento de edición
│   └── shared-config/                tsconfig, eslint, prettier
├── infra/
│   ├── docker/                       Dockerfiles (api, worker-media, worker-ai, live-ingest)
│   ├── compose/                      compose por perfil y producción
│   ├── monitoring/
│   └── deploy/                       proxy inverso y guía de despliegue
├── design-system/                    salida de las skills de diseño (MASTER.md, tokens)
├── docs/
├── tests/
│   ├── e2e/  eval/  perf/  security/
├── scripts/                          utilidades (verificación de reglas de diseño, generadores)
└── .github/workflows/
```

## 8. Variables de entorno (contrato; valores reales fuera del repo)
```
DATABASE_URL, DATABASE_URL_SYSTEM, REDIS_URL
S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, S3_PUBLIC_ENDPOINT
APP_SECRET_KEY, ENCRYPTION_MASTER_KEY, JWT_ISSUER, PUBLIC_API_URL, PUBLIC_WEB_URL, MCP_PUBLIC_URL
GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET
TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET
YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET
TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET
META_APP_ID, META_APP_SECRET
OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_AI_API_KEY      (opcionales; también por BYOK en BD)
WHISPER_MODEL, WHISPER_DEVICE, EMBEDDING_MODEL
SENTRY_DSN, OTEL_EXPORTER_OTLP_ENDPOINT                   (opcionales)
FEATURE_FAKE_ADAPTERS=true|false
```
