# Progreso

Generado desde `docs/10-roadmap-ejecucion.md`. Claude Code marca `[x]` y anota fecha y resultado al terminar cada tarea.
Formato: `- [ ] ID título` → `- [x] ID título — AAAA-MM-DD — resultado`


## F0 — Fundaciones

- [x] F0-01 Repositorio y monorepo — 2026-11-22 — git, pnpm+turbo, workspace uv (Python 3.12), pre-commit (ruff, gitleaks, prettier), estructura de docs/03 §7
- [x] F0-02 Infraestructura local — 2026-11-22 — compose perfil core (postgres17+pgvector, redis, minio+bucket, mediamtx) sano; puertos 5434/6381
- [x] F0-03 Skills de diseño — 2026-11-22 — skills verificadas (huashu-design ausente, no bloquea); MASTER.md generado y reconciliado con docs/08; DESIGN.md creado
- [x] F0-04 Scripts raíz — 2026-11-22 — comandos canónicos en package.json (task.mjs omite pasos aún inexistentes); pnpm check en verde
- [x] F0-05 Esqueleto de API — 2026-11-22 — FastAPI, settings, structlog, request_id, problem+json, /healthz y /readyz, OpenAPI, CORS; 10 pruebas
- [x] F0-06 Capa de datos — 2026-11-22 — motor async, tenant_session con app.org_id, roles app/system, Alembic, migración 0001 reversible; 18 pruebas (RLS real)
- [x] F0-07 Identidad — 2026-11-22 — registro, login, logout, refresco rotatorio con detección de reutilización, verificación de correo, recuperación, tasa y bloqueo; 45 pruebas
- [x] F0-08 MFA y acceso con Google — 2026-11-22 — TOTP con recuperación y anti-replay, secreto cifrado, desafío de login, Google OIDC (PKCE, nonce, fake en dev); 86 pruebas
- [x] F0-09 Organizaciones y RBAC — 2026-11-22 — organizaciones, roles de sistema sembrados, membresías, invitaciones, tokens de API, Principal y X-Org-ID con RLS; 105 pruebas
- [x] F0-10 Pruebas de aislamiento — 2026-11-22 — batería RLS genérica por tabla, arnés IDOR y matriz rol × endpoint auto-descubiertos; validadas con mutación; 126 pruebas
- [x] F0-11 Núcleo de ejecución — 2026-11-22 — jobs, worker con latido/progreso/reintentos, reconciliador, scheduler, outbox→Redis Streams, idempotencia, auditoría append-only particionada, SSE y system.ping verificado de extremo a extremo en Docker; 195 pruebas
- [x] F0-12 Almacenamiento — 2026-11-23 — StorageBackend S3 (multipart, URLs firmadas con ruta y content-type ligados), storage_objects con RLS y ref_count, recolector de huérfanos en el scheduler; 227 pruebas
- [x] F0-13 Interfaz base — 2026-11-24 — Next.js + tokens/temas + next-intl, acceso/MFA/organización/miembros/perfil, cliente OpenAPI, PWA, reglas de diseño y contraste; 227 pytest, 56 Vitest, 20 Playwright
- [ ] F0-14 Cascarón de escritorio
- [x] F0-15 Integración continua — 2026-11-24 — .github/workflows/ci.yml (static+gitleaks, audit pip/pnpm, pytest con servicios, Vitest, Playwright de humo); cada paso verificado en local, falta primera ejecución en GitHub (sin remoto configurado)
- [x] F0-16 Observabilidad base — 2026-11-24 — /metrics (HTTP por ruta, jobs por tipo/estado), trazas OTLP opcionales con trace_id en logs, perfil observability (Prometheus+Grafana) con panel de jobs verificado de extremo a extremo; pnpm obs:up
- [x] F0-17 Documentación operativa — 2026-11-24 — runbook de entorno verificado en clon limpio (halló y corrigió fallo de COOKIE_SECURE vacío), incidentes.md y backup-restauracion.md con restauración y mirror de MinIO probados; 239 pytest
- [ ] F0-ACEPTACION prueba de aceptación de la fase

## F1 — Media y Clip Studio

- [ ] F1-01 Módulo `media`
- [ ] F1-02 Subida multipart
- [ ] F1-03 Imagen `worker-media`
- [ ] F1-04 Trabajo `media.probe`
- [ ] F1-05 Biblioteca
- [ ] F1-06 Esquema `EditProject`
- [ ] F1-07 Módulo `clips`
- [ ] F1-08 Generador del grafo de render
- [ ] F1-09 Worker de render
- [ ] F1-10 Editor
- [ ] F1-11 Texto, imagen y subtítulos manuales
- [ ] F1-12 Plantillas y kits de marca
- [ ] F1-13 Exportaciones
- [ ] F1-14 Paridad vista previa ↔ render
- [ ] F1-15 Panel v1
- [ ] F1-16 Escritorio
- [ ] F1-ACEPTACION prueba de aceptación de la fase

## F2 — IA de video

- [ ] F2-01 Núcleo del AI Gateway
- [ ] F2-02 Adaptadores reales
- [ ] F2-03 Imagen `worker-ai` (CUDA) y transcripción
- [ ] F2-04 Embeddings y búsqueda
- [ ] F2-05 Señales de audio y escenas
- [ ] F2-06 Highlights
- [ ] F2-07 Subtítulos automáticos
- [ ] F2-08 Reencuadre
- [ ] F2-09 Evaluación de highlights
- [ ] F2-10 Costos por video
- [ ] F2-ACEPTACION prueba de aceptación de la fase

## F3 — Agente

- [ ] F3-01 Registro de herramientas
- [ ] F3-02 Orquestación
- [ ] F3-03 Perfiles y delegación
- [ ] F3-04 Aprobaciones
- [ ] F3-05 Validación
- [ ] F3-06 Memoria y feedback
- [ ] F3-07 Defensas ante inyección
- [ ] F3-08 Estudio del agente
- [ ] F3-09 Evaluación del agente
- [ ] F3-10 Flujo canónico de punta a punta
- [ ] F3-ACEPTACION prueba de aceptación de la fase

## F4 — Directos

- [ ] F4-01 Migraciones del dominio de directos
- [ ] F4-02 Servicio `live-ingest`
- [ ] F4-03 Integración con OBS
- [ ] F4-04 Clip ahora
- [ ] F4-05 Fin de sesión
- [ ] F4-06 Twitch
- [ ] F4-07 Escritorio
- [ ] F4-08 Marcadores automáticos
- [ ] F4-09 Pruebas de resiliencia
- [ ] F4-ACEPTACION prueba de aceptación de la fase

## F5 — Publicación

- [ ] F5-01 Conexiones
- [ ] F5-02 `PublishAdapter`
- [ ] F5-03 Publicaciones
- [ ] F5-04 Interfaz
- [ ] F5-05 Sugerencias creativas
- [ ] F5-06 TikTok, Instagram y Facebook
- [ ] F5-07 Pruebas de caos
- [ ] F5-ACEPTACION prueba de aceptación de la fase

## F6 — MCP y automatizaciones

- [ ] F6-01 Servidor MCP
- [ ] F6-02 Servidor de autorización
- [ ] F6-03 Controles MCP
- [ ] F6-04 Centro MCP
- [ ] F6-05 Prueba de integración MCP
- [ ] F6-06 Cliente MCP saliente
- [ ] F6-07 Motor de automatizaciones
- [ ] F6-08 Diseñador de automatizaciones y plantillas
- [ ] F6-09 Notificaciones y webhooks salientes
- [ ] F6-ACEPTACION prueba de aceptación de la fase

## F7 — Analítica

- [ ] F7-05 Bucle de feedback
- [ ] F7-ACEPTACION prueba de aceptación de la fase

## F8 — SaaS comercial

- [ ] F8-00 Decisión de proveedor de pago
- [ ] F8-02 Cuotas
- [ ] F8-03 Facturación
- [ ] F8-04 Panel de administración
- [ ] F8-ACEPTACION prueba de aceptación de la fase

## F9 — Endurecimiento y lanzamiento

- [ ] F9-02 Revisión de seguridad completa
- [ ] F9-03 Prueba de restauración
- [ ] F9-04 SLO y alertas
- [ ] F9-05 Guía de despliegue en servidor
- [ ] F9-ACEPTACION prueba de aceptación de la fase
