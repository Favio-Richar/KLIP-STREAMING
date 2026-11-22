# Calidad y pruebas

## 1. Pirámide
| Nivel | Herramienta | Qué cubre | Cuándo corre |
|---|---|---|---|
| Unitarias | pytest, Vitest | Servicios, reglas de negocio, constructor del grafo de render, utilidades | cada commit |
| Integración | pytest + servicios reales (PostgreSQL, Redis, MinIO) | Repos con RLS, jobs, outbox, adaptadores `fake`, endpoints | cada commit |
| Contratos | pytest | OpenAPI estable, esquemas de eventos y de herramientas, batería de contrato de adaptadores de IA y de publicación | cada commit |
| E2E | Playwright | Flujos completos con interfaz real | cada PR y noche |
| Multimedia | pytest + FFmpeg | Códecs, duración, audio, sincronía, `loudnorm`, subtítulos | cada PR |
| Paridad | Playwright + SSIM | Vista previa vs render (`docs/07` §3) | cada PR |
| Seguridad | pytest | Lista de `docs/09` §3 | cada PR |
| Evaluación IA | arnés propio | Agente (`docs/06` §3) y highlights (`docs/07` §4) | cada PR (modo `fake`) y bajo demanda (modo real) |
| Carga y resiliencia | locust/k6 + scripts de caos | API, renders concurrentes, caídas de workers y proveedores | F4, F5 y F9 |
| Accesibilidad y diseño | axe + `check-design-rules` + contraste | Reglas de `docs/08` | cada PR |

## 2. Reglas
- `pnpm check` debe pasar antes de cada commit; CI repite lo mismo en Linux.
- Cobertura mínima de líneas en backend: 80 % en `service.py` de cada módulo; en frontend, los componentes de `packages/ui` y la lógica del editor (estado, línea de tiempo) con pruebas unitarias.
- **Condiciones que bloquean CI sin excepción:** pruebas de aislamiento RLS, IDOR, acciones no autorizadas del agente = 0, inyección obedecida = 0, paridad vista previa/render, reglas de diseño, secretos detectados.
- Los adaptadores reales de IA y de redes sociales se prueban solo con claves presentes (`@pytest.mark.live`); en CI siempre corre `fake`.
- Datos de prueba: fábricas (`factory-boy` / equivalentes en TS); videos de prueba generados con FFmpeg (`testsrc2`, tonos y silencios) y guardados como fixtures pequeñas; ningún contenido con derechos de terceros.
- Toda corrección de un error añade la prueba que lo habría detectado.
- Las pruebas de interfaz usan selectores accesibles (`getByRole`) y no dependen de tiempos fijos.

## 3. Pruebas de resiliencia obligatorias
1. Reiniciar `worker-media` durante un render → el render termina una sola vez.
2. Reiniciar `worker-ai` durante transcripción → se reanuda sin duplicar segmentos.
3. Reiniciar el orquestador durante una ejecución del agente → se reanuda desde `agent_steps`.
4. Caída de Redis durante 30 s → los jobs en `jobs` se reencolan al volver.
5. Caída del proveedor de IA primario → *fallback* a un alterno compatible, sin repetir efectos externos.
6. Corte de la señal de directo de 60 s → la sesión continúa.
7. Fallo a mitad de una subida a una red social → la reconciliación evita duplicados.

## 4. Informes
Cada fase deja `docs/entregas/F<n>.md` con: resultados de las pruebas de aceptación, métricas (tiempos, costos de IA, tasas de éxito), capturas de pantalla de las pantallas nuevas en ambos temas y problemas conocidos.
