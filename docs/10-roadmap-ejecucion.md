# Roadmap de ejecución

Lista ordenada de tareas para Claude Code. Se ejecutan **en orden**, una por una, según el protocolo de `CLAUDE.md`. Cada tarea termina con pruebas y commit. Cada fase termina con una **prueba de aceptación** automatizada y un informe en `docs/entregas/F<n>.md` (qué se construyó, capturas de pantalla generadas con Playwright, métricas). Favio revisa los informes cuando quiera; **no se espera su respuesta para continuar**.

Hitos de producto: **MVP técnico** = F0–F3 · **Beta privada** = F4–F5 · **V1** = F0–F6 · **Comercial** = F7–F8 · **Lanzamiento controlado** = F9.

Formato de tarea: `**ID** título — alcance`. La tabla de seguimiento `docs/PROGRESS.md` se genera desde este archivo (`scripts/gen-progress.py`).

---

## F0 — Fundaciones
Objetivo: base multi-tenant segura, jobs, eventos en tiempo real, interfaz base y CI.

**F0-01** Repositorio y monorepo — `git init` (con `.gitattributes` `* text=auto eol=lf` y `core.autocrlf false` antes del primer commit), `.gitignore`, `.editorconfig`, `pnpm-workspace.yaml`, `turbo.json`, `pyproject.toml` (workspace `uv`), estructura de `docs/03` §7, `pre-commit` (ruff, gitleaks, prettier).
**F0-02** Infraestructura local — `docker-compose.yml` con perfiles; PostgreSQL 17 con `pgvector`, Redis, MinIO (crea bucket al iniciar), MediaMTX; *healthchecks*, volúmenes con nombre, `.env.example`.
**F0-03** Skills de diseño — instalar las 4 skills en el proyecto (ver `docs/08` §6), generar y persistir el sistema de diseño con `ui-ux-pro-max`, reconciliar con `docs/08`, crear `DESIGN.md`. Si una instalación no es posible de forma no interactiva, continuar con `docs/08` y anotarlo.
**F0-04** Scripts raíz — los comandos canónicos de `CLAUDE.md`, funcionando en Windows (PowerShell) y Linux.
**F0-05** Esqueleto de API — FastAPI, `pydantic-settings`, `structlog`, `request_id`, errores `problem+json`, `/healthz` y `/readyz`, OpenAPI, CORS.
**F0-06** Capa de datos — motor asíncrono, contexto de organización por transacción, roles de BD (`owner`, `app`, `system`), Alembic, migración 0001 (extensiones, función `apply_tenant_rls`, trigger `updated_at`, ayudante UUIDv7).
**F0-07** Identidad — migración, registro, ingreso, cierre de sesión, refresco rotatorio con detección de reutilización, verificación de correo y recuperación de contraseña (correo a consola en desarrollo, SMTP por configuración), limitación de tasa.
**F0-08** MFA y acceso con Google — TOTP con códigos de recuperación; OIDC de Google con proveedor `fake` en desarrollo.
**F0-09** Organizaciones y RBAC — organizaciones, permisos, roles de sistema, membresías, invitaciones, tokens de API, `Principal`, cabecera `X-Org-ID`, semillas.
**F0-10** Pruebas de aislamiento — batería genérica RLS para todas las tablas con `org_id`, arnés de IDOR por endpoint, matriz rol × endpoint. Bloquean CI.
**F0-11** Núcleo de ejecución — tabla `jobs`, despachador, Dramatiq, base de worker con latido y progreso, reconciliador y `scheduler`; `outbox_events` y publicador a Redis Streams; `idempotency_keys` y middleware; `audit_events` particionada y gestor de particiones; endpoint SSE `/events/stream`; job de demostración `system.ping`.
**F0-12** Almacenamiento — `StorageBackend` S3, `storage_objects`, URL firmadas, esqueleto del recolector de huérfanos.
**F0-13** Interfaz base — Next.js, tokens y temas (`packages/ui`), `next-intl`, navegación superior, pantallas de acceso, creación de organización, miembros e invitaciones, perfil y seguridad (MFA), cliente generado desde OpenAPI, PWA (manifiesto y *service worker*), `check-design-rules.mjs` y prueba de contraste.
**F0-14** Cascarón de escritorio — Tauri 2 que carga la aplicación web, bandeja, selector de archivos nativo, enlaces `klick://`, configuración de actualización, estructura para atajos globales.
**F0-15** Integración continua — GitHub Actions: lint, tipos, pytest con servicios, Vitest, Playwright de humo, gitleaks, `pip-audit`, `pnpm audit`, reglas de diseño.
**F0-16** Observabilidad base — OpenTelemetry, `/metrics`, perfil `observability` de Compose con Prometheus y Grafana, panel de jobs.
**F0-17** Documentación operativa — `docs/runbooks/entorno-local.md` verificado paso a paso en limpio, `docs/runbooks/incidentes.md`, `docs/runbooks/backup-restauracion.md`.

**Aceptación F0:** un usuario se registra, activa MFA, crea una organización, invita a otro usuario con rol `viewer` y este no puede ejecutar acciones de escritura; la batería de aislamiento pasa; el job `system.ping` muestra progreso en vivo en la interfaz por SSE; la aplicación se instala como PWA; `pnpm check` en verde en CI.

---

## F1 — Media y Clip Studio
Objetivo: subir un video, editarlo y exportar un clip profesional.

**F1-01** Módulo `media` — migraciones y servicios de proyectos, carpetas, etiquetas, `media_assets`, derivados y subidas.
**F1-02** Subida multipart — API y cargador web con progreso, pausa, reanudación y deduplicación por `sha256`; confirmación de derechos de contenido.
**F1-03** Imagen `worker-media` — FFmpeg y FFprobe con versión fija y libass; pruebas de humo de la imagen.
**F1-04** Trabajo `media.probe` — validación por contenido, medidas de seguridad de FFmpeg, derivados (`proxy_720p`, `thumbnail`, `sprite`, `waveform`, `audio_16k`), estados y errores.
**F1-05** Biblioteca — cuadrícula/lista, filtros, búsqueda, detalle de video con reproductor, eliminación lógica.
**F1-06** Esquema `EditProject` — JSON Schema, generación de Pydantic y Zod, fixtures, marco de migración de documentos.
**F1-07** Módulo `clips` — clips, `edit_projects` con `If-Match`, `clip_versions` inmutables (trigger), API.
**F1-08** Generador del grafo de render — módulo puro con pruebas de instantáneas: recorte, división y unión, `crop`, zoom con *keyframes*, velocidad, texto, imagen, fundidos, mezcla de audio, `loudnorm` en dos pasadas, layouts `split`/`fit`/`follow` (recorte fijo), presets.
**F1-09** Worker de render — progreso, cancelación, recuperación tras caída, subida al almacén, `render_jobs`, `usage_records`.
**F1-10** Editor — vista previa con compositor en canvas, línea de tiempo (regla, pistas, bloques, recorte por asas, división, imán, zoom, marcadores), inspector, deshacer/rehacer, guardado automático con control de concurrencia, atajos de `docs/08`.
**F1-11** Texto, imagen y subtítulos manuales — capas, editor de subtítulos, generación de ASS.
**F1-12** Plantillas y kits de marca — semillas del sistema, aplicar plantilla, interfaz.
**F1-13** Exportaciones — selección de preset, progreso por SSE, descarga firmada, historial.
**F1-14** Paridad vista previa ↔ render — conjunto de documentos de referencia y umbrales SSIM de `docs/07`.
**F1-15** Panel v1 — recientes, trabajos en curso, accesos rápidos.
**F1-16** Escritorio — arrastrar y soltar archivos del sistema directo a la subida.

**Aceptación F1:** Favio sube un video de más de 20 minutos, ve sus derivados, corta un tramo, añade texto y subtítulos manuales, exporta 9:16 a 1080p y lo descarga. Reiniciar el worker a mitad de un render no pierde el trabajo. La prueba de paridad y el E2E de Playwright pasan.

---

## F2 — IA de video
Objetivo: del video largo a candidatos de clip con subtítulos y reencuadre.

**F2-01** Núcleo del AI Gateway — tablas, contratos, registro de capacidades, políticas por tarea, presupuestos, libro de uso, cifrado de credenciales, adaptador `fake`, batería de pruebas de contrato. Sembrar modelos leyendo la documentación oficial vigente de cada proveedor.
**F2-02** Adaptadores reales — OpenAI, Anthropic, Google y compatible con OpenAI; pantallas de proveedores, modelos, credenciales, prueba de conexión y consumo.
**F2-03** Imagen `worker-ai` (CUDA) y transcripción — `faster-whisper`, VAD, marcas por palabra, progreso, detección de idioma, comprobación de GPU con alternativa en CPU por configuración.
**F2-04** Embeddings y búsqueda — BGE-M3, búsqueda híbrida, visor de transcripción con salto al tiempo y búsqueda.
**F2-05** Señales de audio y escenas — jobs de análisis y almacenamiento de señales.
**F2-06** Highlights — generación de ventanas, puntuación combinada, puntuación por LLM con salida estructurada, refinamiento de límites, endpoints y pantalla de revisión (aceptar, rechazar, calificar, convertir en clip).
**F2-07** Subtítulos automáticos — cues con palabras desde la transcripción, 3 estilos, edición, traducción opcional por Gateway, quemado en render.
**F2-08** Reencuadre — detección con MediaPipe, layout `follow` con *keyframes* suavizados, región de webcam para `split`, ajuste manual.
**F2-09** Evaluación de highlights — conjunto sintético, línea base, herramienta de etiquetado en la interfaz.
**F2-10** Costos por video — consumo y costo visibles por asset y por clip.

**Aceptación F2:** un video de gameplay de 30 minutos o más se transcribe, produce 5 candidatos ordenados con motivo, se convierte uno en clip vertical con subtítulos en español y se exporta. Se registran tiempo y costo de cada etapa. La evaluación de highlights no regresa respecto a la línea base.

---

## F3 — Agente
Objetivo: que una orden en lenguaje natural ejecute el flujo completo.

**F3-01** Registro de herramientas — decorador con nombre, esquemas, riesgo y scopes; ejecutor que exige `Principal`; exponer los servicios existentes de F1–F2 como herramientas.
**F3-02** Orquestación — tablas del agente, máquina de estados, planificación estructurada, ejecución de pasos, espera de jobs por eventos, presupuestos, reanudación por reconciliador.
**F3-03** Perfiles y delegación — los 8 especialistas, `delegate`, prompts versionados, semillas.
**F3-04** Aprobaciones — compuerta por riesgo, tarjetas en la interfaz, notificaciones, expiración.
**F3-05** Validación — verificadores deterministas de duración, formato, audio, subtítulos y sincronía.
**F3-06** Memoria y feedback — guardar, recuperar y borrar; `POST /feedback`.
**F3-07** Defensas ante inyección — manejo de datos no confiables, escenarios adversariales, cero violaciones como condición de CI.
**F3-08** Estudio del agente — conversación por SSE, plan, pasos, costos, aprobaciones, historial.
**F3-09** Evaluación del agente — ≥ 40 escenarios con `fake` en CI y modo real bajo demanda, métricas de `docs/06`.
**F3-10** Flujo canónico de punta a punta — "analiza mi último directo, 5 mejores momentos, clips verticales con mi plantilla y subtítulos en español, déjalos para revisar".

**Aceptación F3:** la orden canónica termina en `AWAITING_APPROVAL` con 5 clips renderizados y subtítulos, dentro del presupuesto; aprobar los marca como listos; cero acciones no autorizadas y cero inyecciones obedecidas en la evaluación; matar un worker durante la ejecución no pierde la ejecución.

---

## F4 — Directos
Objetivo: capturar un directo propio y sacar clips en vivo.

**F4-01** Migraciones del dominio de directos — `streams`, `live_sessions`, `stream_segments` particionada, `markers`, `chat_activity_buckets`.
**F4-02** Servicio `live-ingest` — configuración de MediaMTX con clave de ingesta por sesión, segmentador fMP4, registro de segmentos, buffer circular y GC, reconexión.
**F4-03** Integración con OBS — cliente obs-websocket v5, estado, disparo del buffer de repetición, marcadores; pantalla de guía de configuración con los ajustes recomendados.
**F4-04** Clip ahora — API de marcadores, ensamblado de segmentos, creación de asset y propuesta de clip, pantalla de Directos.
**F4-05** Fin de sesión — unión en grabación completa, asset y análisis automático según configuración de la organización.
**F4-06** Twitch — conexión OAuth, EventSub, conteo de chat del propio canal, importación de clips; adaptador `fake`.
**F4-07** Escritorio — atajo global real "Clip ahora" y notificaciones nativas.
**F4-08** Marcadores automáticos — picos de chat y de audio durante el directo.
**F4-09** Pruebas de resiliencia — corte y reconexión de la señal, reinicio de workers, simulación de directo de 3 horas.

**Aceptación F4:** un directo de prueba (OBS o una fuente FFmpeg) llega a MediaMTX; al pulsar el atajo, el clip está disponible en la biblioteca en menos de `post_roll + 20 s`; al terminar la sesión se crea la grabación completa; una caída de 60 s de la señal no pierde la sesión.

---

## F5 — Publicación
Objetivo: del clip aprobado a la red social sin duplicados.

**F5-01** Conexiones — marco OAuth (estado, PKCE, almacenamiento cifrado, renovación de tokens, reconexión).
**F5-02** `PublishAdapter` — interfaz, adaptador `fake` y adaptador de YouTube (subida reanudable, metadatos de Shorts, consulta de estado).
**F5-03** Publicaciones — lotes, máquina de estados, idempotencia, intentos, reconciliación, programador.
**F5-04** Interfaz — calendario, editor de borradores con adaptación por red, aprobación y `publish_rule`.
**F5-05** Sugerencias creativas — títulos, descripciones y etiquetas con el Gateway.
**F5-06** TikTok, Instagram y Facebook — adaptadores según la API oficial vigente de cada plataforma; cada uno con `fake` cuando faltan credenciales o aprobación de la app.
**F5-07** Pruebas de caos — reintentos y caídas a mitad de subida no crean duplicados.

**Aceptación F5:** un clip se programa en un canal de YouTube de prueba con aprobación, se publica a la hora, y repetir o reintentar la operación nunca crea un segundo video; el token vencido se renueva solo.

---

## F6 — MCP y automatizaciones
Objetivo: operar Klick desde asistentes externos y automatizar flujos.

**F6-01** Servidor MCP — montaje en `/mcp`, publicación de herramientas desde el registro, `server/discover`, conformidad con la especificación 2026-07-28.
**F6-02** Servidor de autorización — metadatos RFC 8414 y RFC 9728, registro dinámico y CIMD, PKCE, `resource`/`aud`, pantalla de consentimiento, emisión, rotación y revocación.
**F6-03** Controles MCP — auditoría, límites de tasa, scopes, marca `untrusted`, aprobaciones `R3/R4`.
**F6-04** Centro MCP — clientes, concesiones, scopes, auditoría, revocación.
**F6-05** Prueba de integración MCP — los 9 pasos de `docs/05` con dos clientes independientes.
**F6-06** Cliente MCP saliente — servidores de terceros con lista blanca y su interfaz.
**F6-07** Motor de automatizaciones — definición, nodos (disparador, condición, acción, esperar aprobación, retraso, paralelo), ejecución sobre jobs, idempotencia, versiones, historial; disparadores manual, horario, webhook y evento.
**F6-08** Diseñador de automatizaciones y plantillas — incluye "directo → clips → aprobación".
**F6-09** Notificaciones y webhooks salientes — firma HMAC, preferencias.

**Aceptación F6:** pasa la prueba de integración MCP con dos clientes; un flujo automático "al terminar un directo → analizar → clips → pedir aprobación" corre solo de punta a punta.

---

## F7 — Analítica
**F7-01** Recolección de métricas por adaptador (jobs periódicos), `clip_metrics`, `channel_metrics`.
**F7-02** Agregados diarios `analytics_rollups`.
**F7-03** Pantallas de resumen, clips y canales.
**F7-04** Herramienta `analytics.get_summary` para agente y MCP.
**F7-05** Bucle de feedback — cruzar métricas reales con puntajes de highlights y **sugerir** ajustes de pesos (nunca aplicarlos solos).

**Aceptación F7:** las métricas de clips publicados aparecen en la analítica con retraso máximo de 24 h y el informe de calibración se genera.

---

## F8 — SaaS comercial
**F8-00** Decisión de proveedor de pago — Favio elige proveedor (ver `CLAUDE.md`, casos de detención); mientras tanto se implementa la interfaz `BillingProvider` y su versión `fake`.
**F8-01** Planes, suscripciones y límites en `plans` (sin precios definitivos; configurables).
**F8-02** Cuotas — aplicación de límites con el libro `usage_records` (minutos, almacenamiento, publicaciones, ejecuciones del agente, concurrencia); avisos y bloqueo suave.
**F8-03** Facturación — facturas, consumo BYOK separado del de plataforma.
**F8-04** Panel de administración — usuarios, organizaciones, colas, costos, soporte, interruptores globales.
**F8-05** Incorporación guiada y página pública (con `huashu-design`), centro de ayuda y estado del servicio.

**Aceptación F8:** una organización en plan limitado es frenada al alcanzar su cuota con un mensaje claro; el administrador ve costos por organización; todo con proveedor `fake` si aún no hay uno real.

---

## F9 — Endurecimiento y lanzamiento
**F9-01** Pruebas de carga de API (100, 1.000 y 10.000 cuentas simuladas) y de concurrencia de render, con informe.
**F9-02** Revisión de seguridad completa — repetir `tests/security`, escaneo de dependencias, revisión de cabeceras y de la política CSP.
**F9-03** Prueba de restauración — restaurar copias en un entorno limpio y verificar integridad.
**F9-04** SLO y alertas — panel con disponibilidad, latencia p95 < 500 ms en operaciones comunes (sin procesamiento), tasa de error de jobs.
**F9-05** Guía de despliegue en servidor — Compose de producción, proxy inverso con TLS, variables, migraciones, actualización sin pérdida de datos.
**F9-06** Lista de verificación de lanzamiento y documentación final de usuario.

**Aceptación F9:** informes de carga y restauración entregados, sin hallazgos de seguridad abiertos de nivel alto, guía de despliegue probada en un entorno limpio.
