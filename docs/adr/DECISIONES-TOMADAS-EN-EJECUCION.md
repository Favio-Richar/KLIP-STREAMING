# Decisiones tomadas durante la ejecución

Una línea por decisión que no estaba en la documentación. Formato: `AAAA-MM-DD · tarea · decisión · motivo`.

2026-11-22 · F0-01 · members del workspace uv listados explícitamente (services/api) · live-ingest y worker aún no tienen pyproject
2026-11-22 · F0-02 · MinIO con imagen bitnamilegacy/minio fijada (crea el bucket vía MINIO_DEFAULT_BUCKETS) · minio/minio y quay.io dejaron de publicar imágenes comunitarias; sigue siendo MinIO (ADR-005)
2026-11-22 · F0-02 · Puertos del host: postgres 5434, redis 6381 · 5432/6379 ya los ocupan contenedores de otros proyectos en el PC de Favio
2026-11-22 · F0-03 · No se reinstalan las skills; ui-ux-pro-max se usa desde ~/.codex/skills y MASTER.md se marca reconciliado · Favio ya las instaló; docs/08 manda sobre su salida
2026-11-22 · F0-03 · huashu-design no encontrada en el equipo; se omite · solo sirve para prototipos, no es de producción
