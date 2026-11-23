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
