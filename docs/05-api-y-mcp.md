# API y MCP

## 1. Convenciones de la API REST

- Base: `/api/v1`. Especificación OpenAPI publicada en `/api/v1/openapi.json`.
- **Contexto de organización:** cabecera `X-Org-ID` obligatoria en rutas de negocio. El servidor verifica la membresía del usuario (o la del token/concesión) antes de fijar `app.org_id`.
- **Autenticación:** cookie de sesión (web), `Authorization: Bearer <api_token>` (tokens personales) o `Bearer <token MCP>` solo en `/mcp`.
- **Errores:** `application/problem+json` (RFC 9457) con `type`, `title`, `status`, `detail`, `code` (estable, en inglés), `request_id` y, si aplica, `errors[]` por campo.
- **Paginación:** por cursor (`?limit=50&cursor=...`), respuesta `{ "items": [], "next_cursor": null }`. Máximo 200 por página.
- **Idempotencia:** cabecera `Idempotency-Key` en todo `POST` que cree recursos o dispare trabajo. Misma clave + mismo cuerpo = misma respuesta; mismo clave + cuerpo distinto = `409`.
- **Concurrencia optimista:** `PATCH` de `edit_projects` exige `If-Match: <revision>`; si no coincide, `412`.
- **Operaciones largas:** devuelven `202` con `{ "job_id": "...", "status_url": "/api/v1/jobs/{id}" }`.
- **Tiempo real:** `GET /api/v1/events/stream` (SSE) con eventos filtrados por organización: `job.progress`, `job.succeeded`, `job.failed`, `render.progress`, `agent.message`, `agent.step`, `agent.approval_requested`, `live.marker`, `publication.status`, `notification.created`.
- **Límites de tasa:** por usuario, token, IP y organización (Redis). Respuesta `429` con `Retry-After`.
- **Fechas:** ISO 8601 en UTC. **IDs:** UUID.
- **Versionado:** cambios incompatibles solo en `/api/v2`.

## 2. Endpoints por módulo

Formato: `MÉTODO ruta` — permiso requerido. Todas las rutas aplican RLS y `service` correspondiente.

**Autenticación (`identity`)**
`POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `POST /auth/refresh`, `POST /auth/password/forgot`, `POST /auth/password/reset`, `POST /auth/email/verify`, `GET /auth/google/start|callback`, `POST /auth/mfa/totp/enroll|confirm|disable`, `POST /auth/mfa/verify`, `GET /me`, `PATCH /me`, `GET /me/sessions`, `DELETE /me/sessions/{id}`, `GET/POST/DELETE /me/api-tokens`.

**Organizaciones (`orgs`)**
`GET/POST /orgs`, `GET/PATCH /orgs/{id}`, `GET /members`, `POST /invitations`, `POST /invitations/{token}/accept`, `PATCH /members/{id}` (rol), `DELETE /members/{id}` — `org:manage`, `members:manage`.

**Proyectos y biblioteca (`media`)**
`GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}`; `GET/POST /folders`; `GET/POST /tags`;
`POST /assets/uploads` → URLs firmadas; `POST /assets/uploads/{id}/complete|abort`;
`GET /assets` (filtros: proyecto, tipo, estado, texto, etiquetas), `GET/PATCH/DELETE /assets/{id}`, `GET /assets/{id}/download-url`, `GET /assets/{id}/derivatives` — `media:read|write`.

**Transcripciones (`transcripts`)**
`POST /assets/{id}/transcribe` (job), `GET /assets/{id}/transcripts`, `GET /transcripts/{id}/segments`, `GET /transcripts/search?q=&asset_id=` (híbrido FTS + vectorial) — `ai:run`, `media:read`.

**Highlights (`highlights`)**
`POST /assets/{id}/highlights/detect` (job), `GET /assets/{id}/highlights`, `PATCH /highlights/{id}` (aceptar/rechazar/calificar), `POST /highlights/{id}/to-clip` — `ai:run`, `clips:write`.

**Clips y render (`clips`, `render`)**
`GET/POST /clips`, `GET/PATCH/DELETE /clips/{id}`, `GET/PATCH /clips/{id}/edit-project` (`If-Match`), `GET /clips/{id}/versions`, `POST /clips/{id}/versions`, `POST /clips/{id}/render` (job), `GET /render-jobs/{id}`, `POST /render-jobs/{id}/cancel`, `GET /render-presets`, `GET/POST/PATCH /captions`, `POST /clips/{id}/captions/generate` — `clips:read|write`.

**Plantillas y marca**
`GET/POST/PATCH/DELETE /templates`, `POST /clips/{id}/apply-template`, `GET/POST/PATCH /brand-kits` — `templates:read|write`.

**Proveedores de IA (`ai_gateway`)**
`GET /ai/providers`, `POST /ai/providers`, `POST /ai/providers/{id}/credentials`, `POST /ai/providers/{id}/test`, `GET /ai/models`, `GET/PUT /ai/model-policies/{task_kind}`, `GET /ai/usage?from=&to=&group_by=` — `ai:manage`.

**Agente (`agents`)**
`GET/POST /agent/conversations`, `POST /agent/conversations/{id}/messages` (inicia una ejecución), `POST /agent/runs`, `GET /agent/runs/{id}`, `GET /agent/runs/{id}/steps`, `POST /agent/runs/{id}/cancel`, `GET /approvals?status=pending`, `POST /approvals/{id}/approve|reject`, `GET /agent/memory`, `DELETE /agent/memory/{id}`, `POST /feedback` — `agent:run`, `approvals:decide`.

**Directos (`live`)**
`GET/POST /live/sessions`, `GET /live/sessions/{id}`, `POST /live/sessions/{id}/end`, `POST /live/sessions/{id}/markers`, `GET /live/sessions/{id}/markers`, `POST /live/sessions/{id}/clip-now` (job), `GET /live/ingest-info` (URL RTMP/SRT y clave), `POST /live/obs/connect` — `live:read|write`.

**Conexiones sociales**
`GET /connections`, `GET /connections/{platform}/authorize`, `GET /connections/{platform}/callback`, `POST /connections/{id}/reconnect`, `PUT /connections/{id}/publish-rule`, `DELETE /connections/{id}` — `connections:manage`.

**Publicación (`publishing`)**
`POST /publication-batches`, `GET /publications` (calendario por rango), `GET/PATCH /publications/{id}`, `POST /publications/{id}/submit-for-approval|approve|schedule|cancel|retry` — `publish:draft`, `publish:schedule`.

**Automatizaciones (`automations`)**
`GET/POST/PATCH /workflows`, `POST /workflows/{id}/publish`, `POST /workflows/{id}/run`, `GET /workflow-runs`, `GET /workflow-runs/{id}`, `POST /workflow-runs/{id}/cancel`, `GET/POST/PATCH/DELETE /triggers`, `POST /hooks/{trigger_id}` (entrada de webhook firmada) — `automations:read|run|write`.

**Trabajos y eventos**
`GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, `GET /events/stream`.

**Analítica (`analytics`)**
`GET /analytics/summary`, `GET /analytics/clips`, `GET /analytics/channels` — `analytics:read`.

**Notificaciones y webhooks**
`GET /notifications`, `POST /notifications/read`, `GET/PUT /notification-preferences`, `GET/POST/PATCH/DELETE /webhook-endpoints` — salida firmada con HMAC-SHA256 (`X-Klick-Signature`, `X-Klick-Timestamp`).

**Administración y privacidad**
`GET /admin/...` (solo `is_platform_admin`), `POST /privacy/export`, `POST /privacy/delete-request`, `GET/PUT /retention-policies`.

## 3. Permisos y roles

Permisos (clave → descripción corta): `org:manage`, `members:manage`, `media:read`, `media:write`, `clips:read`, `clips:write`, `templates:read`, `templates:write`, `ai:run`, `ai:manage`, `agent:run`, `approvals:decide`, `live:read`, `live:write`, `connections:manage`, `publish:draft`, `publish:schedule`, `automations:read`, `automations:run`, `automations:write`, `analytics:read`, `billing:manage`, `audit:read`.

| Rol | Permisos |
|---|---|
| `owner` | todos |
| `admin` | todos menos `billing:manage` |
| `editor` | `media:*`, `clips:*`, `templates:*`, `ai:run`, `agent:run`, `live:*`, `publish:draft`, `automations:read|run`, `analytics:read` |
| `reviewer` | `media:read`, `clips:read`, `approvals:decide`, `publish:schedule`, `analytics:read` |
| `viewer` | `media:read`, `clips:read`, `analytics:read` |

## 4. Servidor MCP

- Montado en la API en `/mcp`, transporte HTTP, especificación **2026-07-28**, con el SDK oficial de Python. Sin sesiones de protocolo; descubrimiento con `server/discover`.
- Las herramientas se publican **desde el registro único** (ver `docs/03` §6). Nombre `modulo.accion`.

### Catálogo de herramientas V1

| Herramienta | Riesgo | Scope | Operación |
|---|---|---|---|
| `streams.list` | R0 | `live:read` | Lista sesiones de directo |
| `streams.get_status` | R0 | `live:read` | Estado de un directo |
| `streams.create_marker` | R1 | `live:write` | Marca un momento |
| `videos.list` | R0 | `media:read` | Lista videos |
| `videos.get` | R0 | `media:read` | Metadatos de un video |
| `transcripts.get` | R0 | `media:read` | Transcripción de un video |
| `transcripts.search` | R0 | `media:read` | Búsqueda híbrida en transcripciones |
| `clips.list` | R0 | `clips:read` | Biblioteca de clips |
| `clips.create` | R1 | `clips:write` | Crea clip desde rango o candidato |
| `clips.get` | R0 | `clips:read` | Clip y estado |
| `clips.update` | R1 | `clips:write` | Cambia parámetros del documento de edición |
| `clips.render` | R2 | `clips:write` | Crea trabajo de exportación |
| `ai.analyze_video` | R2 | `ai:run` | Inicia análisis |
| `ai.detect_highlights` | R2 | `ai:run` | Detecta momentos |
| `highlights.list` | R0 | `clips:read` | Lista candidatos |
| `ai.generate_captions` | R2 | `ai:run` | Genera subtítulos |
| `templates.list` | R0 | `templates:read` | Plantillas |
| `templates.apply` | R1 | `clips:write` | Aplica plantilla |
| `publications.create_draft` | R1 | `publish:draft` | Prepara publicación |
| `publications.schedule` | R3 | `publish:schedule` | Programa (requiere aprobación salvo regla explícita) |
| `analytics.get_summary` | R0 | `analytics:read` | Métricas |
| `jobs.get_status` | R0 | (heredado) | Progreso de un trabajo |
| `jobs.cancel` | R1 | (heredado) | Cancela un trabajo propio |
| `automations.list` | R0 | `automations:read` | Lista flujos |
| `automations.run` | R2 | `automations:run` | Ejecuta un flujo publicado |

Recursos MCP de solo lectura: `klick://projects/{id}`, `klick://formats`, `klick://tools-guide`. Prompts MCP: opcionales, fuera de la V1.

### Reglas de las herramientas
1. Entrada y salida con esquema JSON estricto; cadenas largas con límite; nada de texto libre que se ejecute.
2. Toda herramienta recibe un `Principal` (usuario, organización, scopes, origen) y llama al servicio de aplicación. No hay atajos.
3. Las que superan 2 s devuelven `job_id` y el cliente consulta `jobs.get_status`.
4. Cada llamada escribe `mcp_tool_audits` y, si hay efecto, `audit_events`.
5. Los resultados que contienen texto de terceros (transcripciones, títulos importados) llevan el campo `untrusted: true` y se entregan encapsulados como datos.
6. `R3/R4` nunca se ejecutan directamente desde MCP: crean una solicitud en `approvals` y la herramienta devuelve `status: "awaiting_approval"` salvo que la organización haya definido una regla de publicación explícita para esa conexión.

## 5. Autorización MCP (OAuth 2.1)

La API de Klick es el servidor de autorización de su propio MCP.

| Elemento | Detalle |
|---|---|
| Metadatos del recurso | `GET /.well-known/oauth-protected-resource` (RFC 9728): `resource`, `authorization_servers`, `scopes_supported` |
| Metadatos del servidor | `GET /.well-known/oauth-authorization-server` (RFC 8414) incluyendo `issuer` |
| Registro de clientes | Registro dinámico (RFC 7591) en `POST /oauth/register` y soporte de documentos de metadatos de cliente (CIMD) |
| Flujo | Código de autorización + **PKCE (S256)** obligatorio; pantalla de consentimiento por organización y scopes |
| Indicador de recurso | Parámetro `resource` obligatorio (RFC 8707); el token emitido lleva `aud` = URL del MCP |
| Tokens | Acceso de 15 min (JWT firmado con `iss`, `aud`, `sub`, `org_id`, `scope`, `jti`); refresco rotatorio de 30 días almacenado como hash en `mcp_grants` |
| Validación | El MCP rechaza tokens con `aud` distinto, `iss` desconocido, expirados, revocados (`mcp_grants.revoked_at`) o sin el scope de la herramienta |
| Revocación | `POST /oauth/revoke` y desde Configuración → Conexiones MCP; las nuevas llamadas fallan de inmediato (consulta de `jti`/`grant` en caché de 30 s) |
| Prohibido | Aceptar tokens emitidos para otras APIs (*token passthrough*); reenviar el token recibido a servicios externos |

Scopes: `media:read`, `media:write`, `clips:read`, `clips:write`, `ai:run`, `live:read`, `live:write`, `templates:read`, `publish:draft`, `publish:schedule`, `automations:read`, `automations:run`, `analytics:read`. Los scopes de un token nunca superan los permisos del usuario que consintió.

### Prueba de integración MCP (obligatoria en la fase 6)
1. Un usuario autoriza un cliente MCP con scopes `clips:read`, `media:read`, `clips:write`, `ai:run`.
2. El cliente descubre solo las herramientas permitidas por esos scopes.
3. Lista clips de su organización.
4. Selecciona un video, crea un clip y lanza `clips.render`.
5. Consulta `jobs.get_status` hasta completarse y obtiene la referencia del resultado.
6. Intenta leer un recurso de otra organización → `404`/denegado.
7. Intenta `publications.schedule` sin scope → denegado; con scope → queda `awaiting_approval`.
8. El usuario revoca la concesión → las siguientes llamadas fallan.
9. Se repite con **dos clientes MCP independientes** (el cliente de referencia del SDK y un cliente de terceros).

## 6. Cliente MCP (saliente, fase 6)
- Tabla `mcp_servers` por organización con URL, autenticación cifrada y `allowed_tools` explícita (lista blanca).
- Las herramientas externas se exponen al agente solo si están en la lista blanca y con `risk_level` mínimo `R1`; las que declaran efectos se tratan como `R3`.
- Sus resultados son **datos no confiables** (regla 6 de `CLAUDE.md`).
