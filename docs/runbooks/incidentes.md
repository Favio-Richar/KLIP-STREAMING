# Incidentes

Guía para diagnosticar y resolver fallos del sistema. Cubre lo que existe hoy (API, base de datos, Redis, almacén, workers, scheduler). Cada fase posterior añade aquí sus propios escenarios (IA, publicación, directos) al cerrarse.

## 0. Severidad
| Nivel | Criterio | Respuesta |
|---|---|---|
| **S1** | Un cliente ve datos de otro; credenciales expuestas; pérdida de datos | Inmediata; contener primero, explicar después |
| **S2** | API caída o ningún job avanza | Dentro de la hora |
| **S3** | Degradación (latencia, cola creciendo, un tipo de job fallando) | Mismo día |
| **S4** | Cosmético o con solución alternativa | Siguiente iteración |

## 1. Primeros cinco minutos
```bash
curl -s localhost:8000/readyz            # db / redis / storage: cuál está en "unavailable"
docker compose ps                        # contenedores de infraestructura
docker compose -f docker-compose.yml -f infra/compose/workers.yml ps    # workers y scheduler
curl -s localhost:8000/metrics | grep -E "klick_jobs|scrape_ok"
```
Los logs son JSON con `request_id`, `job_id`, `org_id` y, si hay trazas, `trace_id`. Para seguir una petición, filtra por su `request_id` (la API lo devuelve en la cabecera `X-Request-ID` y en el cuerpo de cada error).

Paneles: Grafana «Klick · Jobs y API» (`pnpm obs:up`, `http://localhost:3001`).

## 2. `/readyz` responde 503
La respuesta dice cuál dependencia falla; el detalle técnico **no** se expone al cliente, está en los logs.
| Falla | Qué comprobar | Acción |
|---|---|---|
| `db` | `docker compose ps postgres`; `docker compose logs postgres --tail 50` | Reiniciar el contenedor. Si el volumen está dañado → `backup-restauracion.md` |
| `redis` | `docker compose ps redis` | Reiniciar. Ver §4 (efecto sobre jobs) |
| `storage` | `docker compose ps minio`; consola en `:9001` | Reiniciar. La API arranca igual y reintenta; las subidas fallarán mientras tanto |

La API arranca aunque el almacén esté caído (best-effort al inicio); `/readyz` es la fuente de verdad del estado.

## 3. Jobs atascados o no avanzan
Síntoma: `klick_jobs_oldest_queued_age_seconds` crece, o un job lleva mucho en `running`.

```sql
-- Estado general
select kind, status, count(*) from jobs group by 1,2 order by 1,2;

-- En cola hace más de 5 minutos
select id, kind, queue, created_at, run_after, attempts from jobs
 where status = 'queued' and created_at < now() - interval '5 minutes' order by created_at;

-- En ejecución con latido viejo (worker perdido)
select id, kind, locked_by, heartbeat_at, attempts, max_attempts from jobs
 where status = 'running' and heartbeat_at < now() - interval '60 seconds';

-- Últimos fallos
select id, kind, attempts, error from jobs where status = 'failed' order by updated_at desc limit 10;
```
Ejecútalas con `docker exec klick-postgres-1 psql -U klick_owner -d klick -c "<consulta>"`.

Cómo se recupera solo el sistema (no hay que tocar la base a mano):
- Cada worker marca un **latido** (cada 10 s). Sin latido 60 s, el **reconciliador** del `scheduler` (corre cada 15 s) reencola el job con espera exponencial (base 5 s, máx. 15 min) hasta `max_attempts`; después queda `failed` con el error.
- Los jobs `queued` cuyo mensaje pudo perderse se reenvían a los 600 s.
- Los eventos pasan por el **outbox** (el scheduler lo publica cada 1 s a Redis Streams).

Si nada de eso ocurre, **el scheduler está caído**: es el único proceso periódico.
```bash
docker compose -f docker-compose.yml -f infra/compose/workers.yml logs scheduler --tail 50
docker compose -f docker-compose.yml -f infra/compose/workers.yml restart scheduler
```
Si la cola no baja y el scheduler está bien, revisa los workers de esa cola (`default`, `media`, `ai`, `publish`) con `logs worker-<cola>` y reinícialos. Reiniciar un worker **no pierde trabajo**: el job en curso se reencola por falta de latido.

Cancelar un job desde la API: `POST /api/v1/jobs/{id}/cancel` (permiso `org:manage`). No edites `jobs.status` a mano salvo que el reconciliador esté inoperante, y en ese caso anótalo.

## 4. Redis caído
- Se pierden temporalmente: colas en memoria de Dramatiq, pub/sub, SSE y límites de tasa.
- **No se pierde el estado:** los jobs viven en PostgreSQL. Al volver Redis, el reconciliador reencola lo pendiente y el outbox republica los eventos.
- Con Redis caído, el login y los endpoints con límite de tasa pueden responder error; `/readyz` lo indica.
- Tras recuperarlo: confirmar que `klick_jobs{status="queued"}` baja a 0 en pocos minutos.

## 5. PostgreSQL caído
La API responde 503 en `/readyz` y los endpoints fallan. Los workers reintentan al recuperarse.
1. Reiniciar el contenedor y revisar el log del arranque.
2. Si reporta corrupción o falta el volumen → restaurar (`backup-restauracion.md`).
3. Al volver: `pnpm db:migrate` no debe aplicar nada nuevo; revisar `klick_jobs_scrape_ok == 1`.

## 6. Disco o almacén lleno
Síntoma: subidas fallan, escritura en PostgreSQL falla. `docker system df`, tamaño de los volúmenes. El recolector de huérfanos (scheduler, cada hora) borra objetos sin referencias tras 24 h de gracia y subidas abandonadas tras 24 h; no libera objetos referenciados. Liberar espacio **nunca** borrando archivos de volúmenes a mano.

## 7. S1: sospecha de fuga entre organizaciones
1. **Contener:** detener la API (o bloquear el acceso externo). No investigar con el sistema abierto.
2. **Preservar:** copiar los logs y `audit_events` del periodo (la auditoría es append-only).
3. **Reproducir:** `pytest tests/security` (aislamiento RLS, IDOR, matriz rol × endpoint). Un fallo ahí confirma el hueco.
4. **Revisar políticas:** `select tablename, count(*) from pg_policies group by 1`; toda tabla con `org_id` debe tener política. Tras una restauración, el total debe coincidir con el esperado (hoy 26).
5. **Corregir** con prueba que reproduzca el caso, migración si aplica, y avisar a las organizaciones afectadas según `docs/12-riesgos-y-legal.md`.

## 8. Credenciales expuestas
- Tokens de API de un usuario: revocar en `/settings/profile` o `DELETE /api/v1/me/api-tokens/{id}`; las sesiones se cierran desde la misma pantalla.
- `APP_SECRET_KEY`: rotarla invalida sesiones y tokens firmados (los usuarios deben ingresar de nuevo).
- `ENCRYPTION_MASTER_KEY`: **no rotar sin migración de re-cifrado**; si se filtra, tratarlo como S1 y planificar el re-cifrado de los secretos de MFA y credenciales.
- Claves de proveedores (OpenAI, Anthropic, Google, redes sociales): revocar en el proveedor primero, después reemplazar en la configuración.
- Un secreto en el repositorio: revocarlo (reescribir la historia no basta), y revisar por qué `gitleaks` no lo detuvo.

## 9. Después del incidente
Anotar en `docs/entregas/` (o en el ADR si cambia una decisión): qué pasó, cómo se detectó, el tiempo hasta contener y resolver, la causa raíz y la prueba o alerta que lo habría detectado antes. Toda corrección de un error añade esa prueba (`docs/11`).
