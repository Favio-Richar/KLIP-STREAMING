# Visión y alcance

## Propuesta de valor
Convertir transmisiones y videos extensos en clips profesionales mediante IA, gestionarlos desde un estudio de edición y distribuirlos en varias redes desde un único sistema, con un agente que ejecuta el flujo completo y no solo responde preguntas.

## Usuarios
1. **Favio** (usuario primario): lo usa para su propio canal y su agencia (Visionary AI Group).
2. Streamers, gamers y creadores de contenido.
3. Agencias, community managers y productoras (equipos con roles y aprobaciones).

## Cuatro motores
| Motor | Responsabilidad |
|---|---|
| Media Engine | Captura, edición, composición y render |
| AI Agent Engine | Planificación, herramientas, ejecución, memoria |
| Integration Engine | Proveedores de IA, MCP, redes sociales, automatizaciones |
| SaaS Core | Cuentas, seguridad, almacenamiento, cuotas y facturación |

## Alcance de la V1 (fases F0–F6)
- Cuentas, organizaciones, roles, MFA, PWA y aplicación de escritorio.
- Subida de videos, biblioteca, estudio de edición, render y exportación en 4 formatos.
- Transcripción local con GPU, búsqueda semántica, detección de momentos destacados, subtítulos y reencuadre vertical.
- AI Gateway multiproveedor con presupuestos y registro de consumo.
- Agente con especialistas, aprobaciones y memoria.
- Directos propios por OBS: buffer, marcadores, "clip ahora" con atajo global, grabación completa.
- Publicación con aprobación en YouTube, TikTok, Instagram y Facebook (según permisos de cada plataforma).
- Servidor y cliente MCP con OAuth 2.1.
- Automatizaciones con disparadores, condiciones y aprobaciones.

## Después de la V1
Analítica y calibración (F7), planes y facturación y administración (F8), endurecimiento y lanzamiento (F9).

## Fuera del alcance de la V1
Render local en escritorio, diarización de hablantes, transiciones y filtros avanzados, residencia de datos configurable, IA privada empresarial, captura de transmisiones de terceros.

## Criterios de éxito del producto
- Tiempo desde un video de 30 min hasta 5 clips verticales con subtítulos listos para revisar: medido y registrado en cada entrega (objetivo inicial: menos de 15 minutos con GPU local, sin contar la revisión humana).
- Cero publicaciones duplicadas por reintentos internos.
- Cero acciones no autorizadas del agente en la evaluación.
- 100 % de las pruebas de aislamiento entre organizaciones superadas.
- Costo de IA por clip aprobado medido desde F2 y visible para el usuario.
