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
