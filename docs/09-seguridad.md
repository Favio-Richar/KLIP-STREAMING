# Seguridad, privacidad y gobernanza

## 1. Modelo de amenazas (resumen)
| Amenaza | Mitigación |
|---|---|
| Acceso entre organizaciones (IDOR) | RLS obligatoria + comprobación en servicio + FKs compuestas + prueba de aislamiento por tabla |
| Inyección de instrucciones por contenido (videos, transcripciones, nombres, MCP externo) | Datos no confiables en bloques delimitados; permisos decididos por backend; aprobaciones `R3/R4`; escenarios adversariales en la evaluación del agente |
| Escalada por agente o cliente MCP | `acting_user_id`, scopes ≤ permisos del usuario, lista blanca de herramientas, presupuestos |
| Token passthrough / confusión de audiencia | Validación de `aud`, `iss`, `jti`; prohibido reenviar tokens |
| Robo de credenciales de plataformas o IA | Cifrado de sobre AES-256-GCM, nunca en logs ni respuestas, rotación, acceso solo en backend |
| Archivos maliciosos | Validación por contenido, FFmpeg con `-protocol_whitelist file,pipe`, límites de recursos, usuario sin privilegios, contenedores sin red para el worker de media |
| SSRF | No se descargan URLs arbitrarias en V1; los webhooks salientes validan destino (sin redes privadas) |
| Fuerza bruta y abuso | Limitación de tasa, bloqueo temporal progresivo, MFA, `argon2id` |
| XSS/CSRF | CSP estricta, cookies `HttpOnly`+`SameSite=Lax`, token anti-CSRF en mutaciones con cookie, escape por defecto de React, sanitización de HTML importado |
| Publicación indebida | `publish_rule` explícita, aprobación humana, idempotencia, reconciliación |
| Pérdida de datos | Copias de PostgreSQL y del almacén verificadas con restauración probada |

## 2. Controles obligatorios
- **Identidad:** `argon2id`, verificación de correo, MFA TOTP con códigos de recuperación, refresco rotatorio con detección de reutilización (revoca la familia), cierre de sesión remoto.
- **Autorización:** RBAC + permiso sobre el recurso; servicios reciben un `Principal` y nunca confían en IDs del cliente sin comprobarlos.
- **API:** validación Pydantic estricta (`extra="forbid"`), límites de tamaño, tasa por usuario/token/IP/organización, CORS restringido a `PUBLIC_WEB_URL` y el esquema `tauri://`.
- **Secretos:** solo en variables de entorno o gestor de secretos; `ENCRYPTION_MASTER_KEY` fuera de la base; escáner de secretos en CI y en `pre-commit` (gitleaks).
- **Dependencias:** auditoría automática (`pip-audit`, `pnpm audit`) y Dependabot; imágenes base fijadas por *digest*.
- **Contenedores:** usuario no root, sistema de archivos de solo lectura donde sea posible, límites de CPU/memoria, red mínima.
- **Registro:** `structlog` JSON; lista de campos prohibidos (contraseñas, tokens, claves, cuerpos de IA) con filtro y prueba que lo verifica.
- **Auditoría:** `audit_events` (append-only) para inicios de sesión, cambios de rol, conexiones, credenciales, aprobaciones, publicaciones, concesiones MCP, accesos de administrador del sistema.
- **Privacidad:** exportación y eliminación por usuario/organización (`/privacy/*`), retención configurable, minimización de datos enviados a IA externa (ver `docs/06`), carga de IA no almacenada por defecto.
- **Contenido de terceros:** solo archivos subidos y fuentes autorizadas; confirmación explícita de derechos al subir ("tengo derecho a usar este contenido"), guardada con fecha y usuario.
- **Backups:** `pg_dump` + WAL (si servidor) y réplica del bucket; script y guía de restauración en `docs/runbooks/backup-restauracion.md` con prueba trimestral.
- **Cabeceras:** `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `Strict-Transport-Security` en producción.

## 3. Pruebas de seguridad obligatorias (`tests/security`)
1. Aislamiento RLS en todas las tablas con `org_id`.
2. IDOR por endpoint: para cada ruta con `{id}`, un usuario de otra organización recibe `404`.
3. Autorización por rol: matriz rol × endpoint.
4. MCP: token con audiencia incorrecta, expirado, revocado y sin scope → rechazados.
5. Inyección desde datos: escenarios adversariales del agente (ver `docs/06`).
6. Subida de archivos: ejecutable renombrado `.mp4`, contenedor corrupto, lista de reproducción con URL remota → rechazados sin efectos.
7. Fuerza bruta: bloqueo progresivo y limitación de tasa.
8. Filtro de logs: ningún secreto aparece en la salida de logs durante una ejecución completa.

## 4. Respuesta a incidentes (guía mínima)
`docs/runbooks/incidentes.md`: cómo revocar un token o concesión, rotar `ENCRYPTION_MASTER_KEY` (re-cifrado de sobres), desconectar una plataforma, pausar publicaciones (interruptor global `publishing_paused` en `app_settings`), y reconstruir desde copias.
