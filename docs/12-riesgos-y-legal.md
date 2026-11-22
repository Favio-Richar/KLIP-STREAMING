# Riesgos, límites de plataformas y aspectos legales

> Este documento no es asesoría legal. La revisión legal de derechos de autor, privacidad y términos de cada plataforma corresponde a Favio y a su asesor antes de ofrecer Klick a terceros.

## 1. Riesgos principales
| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Restricciones de APIs sociales (auditorías, cuotas, cambios) | Alta | Alto | Adaptadores aislados con `fake`; solicitar revisiones desde F0; empezar por YouTube |
| Costos de IA y GPU | Media | Alto | Presupuestos por ejecución/día/organización; modelos locales por defecto para transcripción y embeddings; preselección antes del LLM |
| Calidad de clips insuficiente | Media | Alto | Conjunto de evaluación propio, revisión humana por defecto, mejora por feedback |
| Inconsistencia entre vista previa y render | Media | Alto | Esquema único y prueba de paridad obligatoria |
| Acciones indebidas del agente | Baja | Muy alto | Permisos en backend, aprobaciones `R3/R4`, evaluación con cero tolerancia |
| Vulnerabilidades MCP | Media | Muy alto | OAuth 2.1, validación de audiencia, sin *passthrough*, auditoría |
| Pérdida de datos | Baja | Muy alto | Copias con restauración probada, versionado, almacenamiento con referencias |
| Sobrecarga de la señal en directo | Media | Medio | Segmentos pequeños, buffer con límite, contrapresión |
| Cambio de la especificación MCP | Media | Medio | Versión fijada (2026-07-28); capa de adaptación aislada en el módulo `mcp` |
| Dependencia de un proveedor de IA | Media | Medio | Gateway con adaptadores y *fallback* compatible |

## 2. Contenido de terceros
- Klick procesa **archivos que el usuario sube** y **fuentes propias o autorizadas** (su canal conectado, su OBS).
- No descarga ni captura transmisiones o videos de terceros, aunque sean públicos.
- Al subir un archivo, el usuario confirma que tiene derecho a usarlo; queda registrado (usuario, fecha, asset).
- Se incluirá un flujo de retiro de contenido (`POST /privacy/delete-request` y gestión de reclamos) y términos de uso que prohíban subir contenido sin derechos.
- El clip generado de un contenido de terceros autorizado hereda la responsabilidad del usuario; Klick no verifica derechos de música ni de juegos.

## 3. Límites de plataformas (a verificar al implementar cada adaptador)
- **YouTube:** cuota diaria de la API de datos y verificación de la aplicación para subir públicamente; los videos subidos desde aplicaciones sin verificar pueden quedar restringidos.
- **TikTok:** la publicación directa exige auditoría de la aplicación; sin ella, el contenido queda con visibilidad restringida y hay límites de uso diarios.
- **Instagram/Facebook:** requieren cuenta profesional, permisos revisados por Meta y formato/duración según la API vigente.
- **Twitch:** permisos (`scopes`) específicos para clips y EventSub; límites de tasa de la API.
- **Kick:** solo capacidades que su API oficial exponga; si no hay API suficiente, queda fuera.
Los valores concretos (duración máxima, tamaño, cuotas) se leen de la documentación oficial vigente en la tarea de cada adaptador y se guardan en la configuración del adaptador, no en el código.

## 4. Privacidad y datos personales (Chile)
- Se tratan datos personales (correo, nombre, contenido audiovisual con voz e imagen). Se aplicará política de privacidad, consentimiento, exportación y eliminación (ver `docs/09`).
- Datos enviados a IA externa: minimizados, configurables por organización y con opción `local_only`.
- La norma aplicable (Ley N° 19.628 y su actualización por la Ley N° 21.719) debe revisarse con asesoría antes del lanzamiento comercial.

## 5. Licencias de dependencias
- Se prefieren licencias permisivas (MIT, Apache-2.0, BSD). **No se usan** componentes AGPL en el producto (por ejemplo, Ultralytics/YOLO).
- FFmpeg se usa como binario externo con una compilación sin componentes `--enable-nonfree`; revisar la licencia (LGPL/GPL) de la compilación elegida antes de distribuir la aplicación de escritorio.
- Un reporte de licencias (`pnpm licenses`, `pip-licenses`) se genera en CI y se revisa en F9.
