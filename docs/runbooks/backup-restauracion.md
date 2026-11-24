# Backup y restauración

Procedimientos verificados el 2026-11-24 contra el entorno local (PostgreSQL 17 + pgvector, MinIO). Restauración comprobada con una base temporal: mismas filas, misma migración (`alembic_version`), mismas 26 políticas RLS y extensión `vector` presente.

> Estado en PostgreSQL = fuente de verdad (regla 4 de CLAUDE.md). Los objetos binarios (videos, derivados, renders) viven en el almacén S3. **Un backup válido son los dos juntos**, tomados con poca diferencia de tiempo. Un dump sin su almacén deja filas que apuntan a objetos inexistentes; el recolector de huérfanos y `storage_objects` permiten detectar el desajuste (ver §4).

## Qué se respalda
| Dato | Dónde vive | Cómo | Frecuencia recomendada |
|---|---|---|---|
| Base de datos | volumen `klick_pgdata` | `pg_dump -Fc` | diario + antes de cada migración |
| Objetos | volumen `klick_miniodata` / bucket `klick` | `mc mirror` | diario (incremental) |
| Secretos y `.env` | gestor de secretos | copia cifrada fuera del servidor | al cambiar |
| Redis | `klick_redisdata` | **no se respalda**: es caché, colas y pub/sub; el estado de jobs está en PostgreSQL y el reconciliador reencola | — |

`ENCRYPTION_MASTER_KEY` es parte del backup: sin ella no se pueden descifrar los secretos de MFA ni las credenciales cifradas de la base. Guárdala **separada** del dump.

## 1. Backup de PostgreSQL
```bash
# MSYS_NO_PATHCONV=1 en Git Bash para que no reescriba las rutas /tmp
docker exec klick-postgres-1 pg_dump -U klick_owner -d klick -Fc -f /tmp/klick.dump
docker cp klick-postgres-1:/tmp/klick.dump ./backups/klick-$(date +%Y%m%d-%H%M).dump
```
`-Fc` (formato personalizado) permite restaurar de forma selectiva y en paralelo. Copia el archivo fuera del contenedor y fuera de la máquina.

## 2. Backup del almacén de objetos
```bash
docker exec klick-minio-1 sh -c '
  mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" &&
  mc mirror --overwrite local/klick /tmp/minio-backup'
docker cp klick-minio-1:/tmp/minio-backup ./backups/minio-$(date +%Y%m%d)
```
En producción apunta `mc mirror` a un bucket de otra cuenta o región con versionado activado. No uses `--remove` en un job automático: un borrado accidental se propagaría al respaldo.

## 3. Restauración
**Siempre en una base nueva primero**, se verifica y solo entonces se promueve.

```bash
# 3.1 Base temporal
docker exec klick-postgres-1 psql -U klick_owner -d postgres -c "CREATE DATABASE klick_restore"
docker cp ./backups/klick-AAAAMMDD-HHMM.dump klick-postgres-1:/tmp/restore.dump
docker exec klick-postgres-1 pg_restore -U klick_owner -d klick_restore --no-owner --exit-on-error /tmp/restore.dump

# 3.2 Verificación (compara con lo esperado del momento del backup)
docker exec klick-postgres-1 psql -U klick_owner -d klick_restore -Atc "
  select 'alembic', version_num from alembic_version
  union all select 'rls_policies', count(*)::text from pg_policies
  union all select 'users', count(*)::text from users
  union all select 'orgs', count(*)::text from organizations
  union all select 'jobs', count(*)::text from jobs"
```
Debe coincidir la versión de Alembic y el número de políticas RLS (hoy 26; crece con cada tabla de negocio). **Una restauración sin las políticas RLS es un incidente de aislamiento**: no promover.

```bash
# 3.3 Promoción (ventana de mantenimiento: detener API, workers y scheduler)
docker exec klick-postgres-1 psql -U klick_owner -d postgres -c "ALTER DATABASE klick RENAME TO klick_old"
docker exec klick-postgres-1 psql -U klick_owner -d postgres -c "ALTER DATABASE klick_restore RENAME TO klick"
pnpm db:migrate     # no debe aplicar nada si el dump era de la versión actual
```
Conserva `klick_old` hasta confirmar el servicio sano; bórrala después, a mano.

**Roles:** los roles `klick_app` y `klick_system` son del clúster, no de la base. Si restauras en un servidor nuevo, `pnpm db:migrate` los crea con las contraseñas de `KLICK_APP_PASSWORD` y `KLICK_SYSTEM_PASSWORD`; usa las mismas que el `.env`.

Restauración de objetos:
```bash
docker cp ./backups/minio-AAAAMMDD klick-minio-1:/tmp/minio-restore
docker exec klick-minio-1 sh -c 'mc mirror --overwrite /tmp/minio-restore local/klick'
```
Verificado: borrar un objeto, restaurar con `mc mirror` y leerlo de nuevo devuelve el contenido original.

## 4. Después de restaurar
1. `curl http://localhost:8000/readyz` → `db`, `redis`, `storage` en `ok`.
2. Arrancar workers y scheduler. Los jobs que estaban `running` al momento del backup pierden su latido: el reconciliador (cada 15 s) los reencola o los marca fallidos según `max_attempts`.
3. El outbox se republica solo; los eventos ya entregados no se repiten porque llevan estado de publicación.
4. Cuando existan publicaciones externas (F5): **no reintentar a ciegas** las publicaciones en vuelo; la reconciliación consulta primero a la plataforma (regla 5, idempotencia).
5. Revisar `klick_jobs_scrape_ok == 1` en `/metrics`.

## 5. Prueba periódica
Cada trimestre (y antes del lanzamiento, tarea F9-03) ejecutar §1–§3.2 sobre una base temporal y registrar la fecha, el tamaño del dump, el tiempo de restauración y el resultado de la verificación en `docs/entregas/`. Un backup que nunca se restauró no cuenta como backup.

## 6. Retención sugerida
Diarios 14 días, semanales 8 semanas, mensuales 12 meses. La auditoría (`audit_events`) tiene su propia retención de 730 días por particiones; el backup no la sustituye ni la amplía.
