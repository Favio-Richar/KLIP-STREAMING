# Decisiones tomadas durante la ejecución

Una línea por decisión que no estaba en la documentación. Formato: `AAAA-MM-DD · tarea · decisión · motivo`.

2026-11-22 · F0-01 · members del workspace uv listados explícitamente (services/api) · live-ingest y worker aún no tienen pyproject
2026-11-22 · F0-02 · MinIO con imagen bitnamilegacy/minio fijada (crea el bucket vía MINIO_DEFAULT_BUCKETS) · minio/minio y quay.io dejaron de publicar imágenes comunitarias; sigue siendo MinIO (ADR-005)
2026-11-22 · F0-02 · Puertos del host: postgres 5434, redis 6381 · 5432/6379 ya los ocupan contenedores de otros proyectos en el PC de Favio
2026-11-22 · F0-03 · No se reinstalan las skills; ui-ux-pro-max se usa desde ~/.codex/skills y MASTER.md se marca reconciliado · Favio ya las instaló; docs/08 manda sobre su salida
2026-11-22 · F0-03 · huashu-design no encontrada en el equipo; se omite · solo sirve para prototipos, no es de producción
2026-11-22 · F0-04 · scripts/task.mjs omite con aviso los comandos cuyo objetivo aún no existe · permite que pnpm check sea verde antes de F0-05..F0-13
2026-11-22 · F0-06 · Las pruebas con base de datos crean una base temporal migrada con Alembic y se omiten si no hay PostgreSQL (fallan si CI=true) · aislamiento total y `pnpm check` utilizable sin infraestructura levantada
2026-11-22 · F0-06 · Migración 0001 añade apply_reference_rls (tablas de referencia con org_id nulo) además de apply_tenant_rls · docs/04 §2 exige ambas políticas
2026-11-22 · F0-06 · Los roles klick_app/klick_system se crean en la migración con contraseñas de KLICK_APP_PASSWORD/KLICK_SYSTEM_PASSWORD (defaults solo de desarrollo) y no se eliminan al hacer downgrade · son objetos del clúster
2026-11-22 · F0-07 · Sesión web = JWT de acceso de 15 min en cookie HttpOnly + refresh opaco rotatorio en cookie con Path=/api/v1/auth; CSRF por HMAC ligado a la sesión (cookie legible + cabecera X-CSRF-Token) · ADR-017 pide cookie HttpOnly con refresco rotatorio; el HMAC evita guardar estado
2026-11-22 · F0-07 · El inicio de sesión se permite sin correo verificado (se expone `email_verified`; las acciones sensibles lo exigirán más adelante) · evita bloquear el flujo de aceptación de F0 y la verificación sigue disponible
2026-11-22 · F0-07 · Registro y recuperación responden 202 genérico y, si el correo ya existe, se le envía un aviso en vez de revelarlo · evita enumeración de cuentas (docs/09)
2026-11-22 · F0-07 · Bloqueo progresivo por correo: 5 fallos libres en 15 min y luego 1, 2, 4... min (máx. 60); además tasa por IP · docs/09 exige bloqueo progresivo
2026-11-22 · F0-07 · Los clientes web deben serializar el refresco (un solo vuelo) porque una carrera de dos refrescos con el mismo token se trata como reutilización · sin ventana de gracia para no debilitar la detección
2026-11-22 · F0-07 · Singletons (BD, Redis, correo, sondas de salud) viven en app.state y no en módulos globales · permite varias apps en pruebas
2026-11-22 · F0-08 · Secreto TOTP cifrado con AES-256-GCM en sobre (clave de datos por registro envuelta con ENCRYPTION_MASTER_KEY vía HKDF) y ligado al usuario por AAD · ADR-014
2026-11-22 · F0-08 · Migración 0003 añade mfa_factors.last_used_step para impedir reutilizar un código TOTP · protección anti-replay; la 0002 ya estaba aplicada y no se edita
2026-11-22 · F0-08 · El desafío MFA del login vive en Redis (5 min, un solo uso, 5 intentos) y los códigos de recuperación se guardan como SHA-256 · estado efímero; los códigos tienen ~50 bits y se consumen de forma atómica
2026-11-22 · F0-08 · Google OIDC: si el correo local existe sin verificar, se descarta su contraseña y sus sesiones antes de vincular; si está verificado, se vincula · previene el secuestro previo al registro
2026-11-22 · F0-08 · Proveedor `fake` de Google solo si no hay credenciales, FEATURE_FAKE_ADAPTERS=true y no es producción; con producción se exige APP_SECRET_KEY y ENCRYPTION_MASTER_KEY no por defecto · el fake no puede quedar activo en producción por error
2026-11-22 · F0-09 · RLS de organizations/memberships/invitations/api_tokens usa además `app.user_id` y los GUC `app.invite_token_hash` y `app.token_hash` (lista blanca en core.db.set_context) · permite listar mis organizaciones y resolver una invitación o un token sin saltarse RLS ni usar el rol de sistema
2026-11-22 · F0-09 · Regla anti-escalada: un rol, invitación o token solo puede otorgar permisos que el actor ya tiene; un admin no puede degradar ni expulsar a un owner · evita escalada de privilegios (docs/09)
2026-11-22 · F0-09 · Aceptar una invitación exige sesión con el mismo correo y correo verificado · el token por sí solo no basta si se filtra
2026-11-22 · F0-09 · Un token de API no puede crear otros tokens y su Principal usa scopes ∩ permisos actuales del rol · limita persistencia y escalada tras un robo de token
2026-11-22 · F0-09 · Se corrigió el downgrade() de la migración 0004 (soltar la política org_select antes de borrar tablas) antes de commitearla; el esquema aplicado no cambió · el downgrade fallaba por dependencia entre políticas
2026-11-22 · F0-09 · La tabla `devices` (docs/04 §3.2) se difiere a F0-14 (escritorio) · no la usa ninguna tarea anterior
2026-11-22 · F0-09 · Los cambios de rol, altas/bajas de miembros y tokens se registran con structlog; pasan a `audit_events` en F0-11 cuando exista la tabla · la tabla de auditoría aún no existe
2026-11-22 · F0-10 · Las pruebas de aislamiento viven en tests/security (como pide docs/04) y comparten fixtures con services/api/tests mediante `pythonpath` · evita duplicar la preparación de PostgreSQL y Redis
2026-11-22 · F0-10 · Cada tabla con org_id debe registrar una fábrica en tests/security/sec_factories.py y cada ruta una política en sec_policy.py; la prueba falla si falta · fuerza a probar el aislamiento de todo recurso o endpoint futuro
2026-11-22 · F0-10 · El aplanado de rutas recorre `_IncludedRouter` (FastAPI 0.165 incluye routers de forma perezosa) · FastAPI.routes ya no devuelve APIRoute directamente
2026-11-22 · F0-10 · Los límites de tasa por IP de registro y login pasan a ajustes (RATE_LIMIT_REGISTER_IP, RATE_LIMIT_LOGIN_IP) con los mismos valores por defecto · las pruebas crean decenas de usuarios desde una sola IP
2026-11-22 · F0-11 · El publicador del outbox corre dentro del `scheduler` (cada 1 s) y no en `worker-default` · el scheduler ya es el único proceso periódico; evita un worker que haga polling
2026-11-22 · F0-11 · Los reintentos los decide la tabla `jobs` (backoff exponencial base 5 s, máx. 15 min) y Dramatiq se configura con `max_retries=0` · una sola fuente de verdad para el estado
2026-11-22 · F0-11 · El progreso (`job.progress`) es un evento efímero solo al stream de la organización; los cambios de estado pasan por el outbox · evita una fila de outbox por cada tick de progreso
2026-11-22 · F0-11 · SSE usa el stream de Redis por organización (`klick:org:{id}:events`) con IDs de stream como `Last-Event-ID`; acepta `?org_id=` porque EventSource no envía cabeceras · reanudación sin estado extra
2026-11-22 · F0-11 · La idempotencia de peticiones es un helper `IdempotencyStore` usado por las rutas (no un middleware ASGI) porque necesita la organización resuelta por la autenticación · aplicada a POST /invitations y POST /jobs; no a respuestas con secretos
2026-11-22 · F0-11 · `audit_events.org_id` no tiene FK a `organizations` · la auditoría es append-only y debe sobrevivir al borrado de su organización
2026-11-22 · F0-11 · Los índices de partición se gestionan con funciones SECURITY DEFINER (ensure_monthly_partitions, drop_expired_partitions) ejecutables solo por klick_system; las particiones no tienen privilegios directos para la app · el rol de sistema no es dueño de las tablas
2026-11-22 · F0-11 · POST /jobs y su cancelación exigen `org:manage` mientras `system.ping` sea el único kind público; una prueba obliga a revisar la política si se añade otro · el permiso depende del kind y sec_policy lo declara por ruta
2026-11-22 · F0-11 · `worker-ai` existe desde F0 sin GPU; el perfil `gpu` con faster-whisper llega en F2 · la cola `ai` ya está disponible
2026-11-23 · F0-12 · El trigger `set_updated_at` pisa `updated_at` en cada UPDATE; las pruebas que necesitan fechas antiguas lo suspenden solo dentro de su transacción · evita relajar el trigger real
2026-11-23 · F0-13 · El navegador habla solo con el origen del web: Next reescribe `/api/*` hacia la API (`KLICK_API_URL`) · cookies de sesión y CSRF funcionan sin CORS ni cookies de terceros
2026-11-23 · F0-13 · Idioma sin prefijo en la URL: cookie `klick_locale` → `Accept-Language` → es-CL; el tema es preferencia local (`klick.theme`: sistema/oscuro/claro) · menos rutas y sin parpadeo gracias a un script previo al pintado
2026-11-23 · F0-13 · Tokens: `--text-3` oscuro #7A8394→#8A93A4 (4,48→≥4,5:1 sobre bg-2), nuevo `--control-border` (≥3:1) para bordes de campos y `--focus-ring` (acento en oscuro, #4D7A00 en claro porque el acento sobre blanco no llega a 3:1); en claro `--text-3` #6E7787→#5F6878 y semánticos oscurecidos (#0A7558, #8A5300, #C4203A, #1B5FC7) para texto ≥4,5:1 · la prueba de contraste de `packages/ui` lo exige
2026-11-23 · F0-13 · Raíz en 16 px y 14 px solo en `body` · con la raíz en 14 px Tailwind encogía todas las medidas en rem (h-10 medía 35 px); detectado por la prueba de áreas táctiles
2026-11-23 · F0-13 · El service worker solo cachea activos estáticos (`/_next/static`) y la pantalla sin conexión; nunca la API ni HTML autenticado · el estado vive en el servidor (CLAUDE.md regla 4)
2026-11-23 · F0-13 · Los módulos de fases futuras (Directos, Biblioteca, Clips, Agente, Publicaciones, Analítica) aparecen en la navegación como elementos no activables con la explicación "Disponible en una próxima etapa" · respeta docs/08 §3 sin crear pantallas falsas
2026-11-23 · F0-13 · Enlace de invitación `/invitations/accept?token=` (el que genera la API) · el correo ya existía en el backend; el web se alineó con él
2026-11-23 · F0-13 · Las capturas de entrega se generan con Playwright sin comparar píxeles; el contraste y la accesibilidad (axe) sí se verifican · las fuentes y el antialiasing difieren entre Windows y Linux y una referencia de píxeles daría falsos fallos en CI
2026-11-23 · F0-13 · E2E con el Chrome instalado (`channel: chrome`) y API en el puerto 8010, web en 3100 · evita descargar un navegador aparte y choques con otros servicios locales en 8000/3000
2026-11-23 · F0-13 · pnpm: `@parcel/watcher` y `@swc/core` con scripts de build denegados (`allowBuilds: false`) · solo son respaldos de compilación; los binarios llegan precompilados
2026-11-23 · F0-13 · Versión de ruff del hook de pre-commit alineada con la del proyecto (v0.17.6) · con versiones distintas el hook reformateaba archivos y luego `ruff format --check` fallaba (el espejo de prettier quedó congelado en v3.1.0)
2026-11-24 · F0-15 · CI usa `pre-commit run --all-files` para gitleaks/ruff/prettier y `pip-audit` sobre `uv export --package klick` · una sola fuente de verdad con los hooks locales; `pip-audit` directo falla porque el paquete del workspace no está en PyPI
2026-11-24 · F0-16 · `/metrics` es público en desarrollo y exige Bearer (`METRICS_TOKEN`) en producción; sin token queda en 404 · evita exponer métricas por accidente sin añadir autenticación de usuario a un endpoint de máquina
2026-11-24 · F0-16 · Las métricas de jobs son agregados globales (kind, status) leídos con `klick_system` en cada scrape; la API es el único exportador y los workers no abren puerto propio · Prometheus ve el estado real desde PostgreSQL (regla 4) sin multiplicar endpoints; la cardinalidad es fija
2026-11-24 · F0-16 · Etiquetas HTTP con la plantilla de ruta y `unmatched` para rutas inexistentes · evita explosión de cardinalidad y que ids de recursos acaben en Prometheus
2026-11-24 · F0-16 · Trazas OpenTelemetry solo si `OTEL_EXPORTER_OTLP_ENDPOINT` existe, con registro de métricas y proveedor por aplicación (no globales) · `create_app` se llama muchas veces en pruebas; sin endpoint no hay costo ni dependencia de red
2026-11-24 · F0-16 · Prometheus recibe el destino por `OBS_API_TARGET` (plantilla + `sed` al arrancar) · permite apuntar a la API en cualquier puerto sin editar archivos versionados
2026-11-24 · F0-17 · `Settings` trata `COOKIE_SECURE=` vacío como automático (None) · `.env.example` lo trae vacío y la verificación en limpio mostró que la API no arrancaba con él
