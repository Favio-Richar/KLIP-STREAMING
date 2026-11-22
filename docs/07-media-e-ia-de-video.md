# Media e IA de video

## 1. Ingesta de archivos

- Subida directa del navegador al almacén (multipart, URLs firmadas). Tamaño máximo por defecto 20 GB y 4 h por archivo (configurable por organización).
- Contenedores aceptados: `mp4`, `mov`, `mkv`, `webm`, `ts`. Audio: `mp3`, `wav`, `m4a`. Imagen: `png`, `jpg`, `webp`.
- **Se valida por contenido, no por extensión:** FFprobe debe encontrar al menos un stream de video (o audio para `kind=audio`). Si falla, el asset pasa a `failed` con motivo.
- Seguridad al ejecutar FFmpeg/FFprobe sobre archivos de usuario: `-nostdin`, `-protocol_whitelist file,pipe` (evita SSRF por listas de reproducción), tiempo máximo por proceso, límites de CPU/memoria del contenedor, usuario sin privilegios y directorio temporal aislado por job.
- Derivados generados tras la subida (`media.probe`): `proxy_720p` (H.264, GOP corto para edición ágil), `thumbnail`, `sprite` (tira de miniaturas para la línea de tiempo), `waveform` y `audio_16k` (mono 16 kHz para transcripción).

## 2. Documento de edición (`EditProject` v1)

Fuente única en `packages/video-project-schema/edit-project.schema.json`. De ahí se generan Pydantic (backend) y Zod (frontend). Tiempos en **milisegundos enteros**; coordenadas **normalizadas 0–1** respecto al lienzo.

```jsonc
{
  "schema_version": "1.0",
  "canvas": { "width": 1080, "height": 1920, "fps": 30, "background": "#000000" },
  "sources": [ { "id": "s1", "asset_id": "<uuid>", "kind": "video" } ],
  "layout": {
    "mode": "follow",                      // follow | split | fit | manual
    "split": { "top_source": "s2", "bottom_source": "s1", "ratio": 0.4 },
    "fit":   { "background": { "type": "solid", "color": "#0B0D10" } },
    "follow":{ "smoothing": 0.85, "subject": "face" }
  },
  "timeline": {
    "duration_ms": 28400,
    "tracks": [
      { "id": "v1", "kind": "video", "items": [{
          "id": "i1", "source_id": "s1",
          "source_in_ms": 120000, "source_out_ms": 148400, "start_ms": 0,
          "speed": 1.0, "volume": 1.0, "fade_in_ms": 0, "fade_out_ms": 200,
          "crop": { "x": 0.28, "y": 0, "w": 0.316, "h": 1 },
          "keyframes": [ { "t_ms": 0, "scale": 1.0, "x": 0.5, "y": 0.5, "ease": "out" } ]
      }]},
      { "id": "a1", "kind": "audio", "items": [] },
      { "id": "t1", "kind": "text",  "items": [{ "id": "x1", "start_ms": 0, "end_ms": 2500, "text": "…", "style_ref": "tpl:<uuid>", "position": { "x": 0.5, "y": 0.18 } }] },
      { "id": "g1", "kind": "image", "items": [] }
    ]
  },
  "captions": { "track_id": "<caption_tracks.id>", "style_ref": "tpl:<uuid>", "safe_area": "shorts" },
  "audio": { "normalize": { "enabled": true, "target_lufs": -14, "true_peak_db": -1.5 }, "ducking": { "enabled": false } }
}
```

Reglas:
- Validación en el servidor en cada `PATCH`; un documento inválido nunca se guarda.
- Cambios de `schema_version` mayores traen una migración de documentos (`migrate_edit_project(v_from, v_to)`) probada con fixtures.
- Las plantillas (`templates`) se **resuelven al renderizar** y la versión resuelta se guarda en `clip_versions.document` (instantánea inmutable), para que un render pasado sea reproducible.

## 3. Render

- Un módulo `klick/render/graph.py` convierte `EditProject` + preset en una lista de argumentos de FFmpeg (grafo `-filter_complex`). Es **puro y determinista** (misma entrada → mismos argumentos) y se cubre con pruebas unitarias de instantáneas.
- Códec por defecto: H.264 (`libx264`, `-preset fast`, `-crf 20`, `yuv420p`, `-movflags +faststart`) y AAC 192 kbps. Codificación por GPU (`h264_nvenc`) como opción de preset, no por defecto.
- Audio: normalización a −14 LUFS en dos pasadas (`loudnorm` medir → aplicar), pico verdadero −1,5 dB.
- Subtítulos: archivo ASS generado desde `caption_tracks.cues` y la plantilla de estilo, quemado con libass. Estilos incluidos: *limpio*, *palabra destacada* (resaltado palabra a palabra con etiquetas `\k`) y *contorno*.
- **Zona segura 9:16** (`safe_area: "shorts"`): márgenes de 6 % lateral, 12 % superior y 20 % inferior para que los elementos de la interfaz de las redes no tapen subtítulos ni textos. Máximo 2 líneas y ~28 caracteres por línea en vertical.
- Progreso: se lee `-progress pipe:1` y se publica como `render.progress` (0–100) cada segundo.
- Cancelación: señal al proceso; limpieza de temporales; estado `canceled`.
- Recuperación: un render interrumpido por caída del worker se reencola completo (el reconciliador); el resultado es idempotente por `(clip_version_id, preset_id)`.
- Versión de FFmpeg fija en la imagen; se guarda en `render_jobs.ffmpeg_version` y el hash de argumentos en `ffmpeg_args_hash`.

### Paridad vista previa ↔ render (prueba obligatoria)
`tests/e2e/preview_render_parity.spec.ts` con un conjunto de documentos de referencia (recorte, zoom con *keyframes*, layout `split`, layout `fit`, subtítulos, texto, imagen):
- Se captura el lienzo de la vista previa con Playwright en 3 instantes (inicio, medio, final).
- Se extraen los mismos fotogramas del render.
- Se calcula SSIM. **Umbrales:** ≥ 0,95 en el fotograma completo; ≥ 0,90 en la región de subtítulos.
- Un fallo bloquea CI.

## 4. Pipeline de IA sobre video

Orquestado como jobs encadenados (`analysis_runs`), cada etapa idempotente y reanudable.

| # | Etapa | Implementación | Salida |
|---|---|---|---|
| 1 | Transcripción | `faster-whisper` en GPU, VAD, marcas de tiempo por palabra, idioma autodetectado (español por defecto). Modelo configurable en `WHISPER_MODEL`; valor inicial `large-v3` (usar la variante *turbo* si la versión instalada la ofrece) | `transcripts`, `transcript_segments` |
| 2 | Embeddings | BGE-M3 (1024 dims) sobre segmentos | `transcript_segments.embedding` |
| 3 | Señales de audio | Loudness EBU R128 por ventana, picos de energía, detección de risa/grito por energía y espectro | señales por ventana de 1 s |
| 4 | Escenas | PySceneDetect | cortes de escena |
| 5 | Actividad de chat | `chat_activity_buckets` (si hay datos) | señal por minuto |
| 6 | Visión (opcional) | Muestreo de 1 fotograma cada 5 s hacia un modelo multimodal vía Gateway, solo en ventanas preseleccionadas | descripciones y marcas de interés |
| 7 | Candidatos | Ventanas deslizantes + filtro | ventanas pre-puntuadas |
| 8 | Puntuación | Combinación ponderada (abajo) + LLM sobre texto | `highlight_candidates` |
| 9 | Refinamiento | Ajuste de límites a fin de frase; pre-roll 1 s y post-roll 0,5 s | rangos finales |

### Puntuación de impacto
- Cada señal se normaliza a 0–1 por **rango percentil dentro del propio video**.
- `score = 100 × Σ(wᵢ·sᵢ) / Σ(wᵢ de señales disponibles)`. Pesos iniciales (configurables en `organizations.settings.highlight_weights`): audio 0,20 · texto 0,35 · chat 0,15 · escena 0,05 · visión 0,10 · marcador 0,15.
- Un marcador manual o de atajo dentro de la ventana fija un **piso de 85**.
- Ventanas candidatas: duración objetivo 20–60 s (configurable), paso de 5 s. Se preselecciona el 3×N superior por audio+escena antes de llamar al LLM para controlar costos.
- Supresión de solapes: separación mínima de 20 s entre candidatos; se devuelven los N mejores (por defecto 5).
- El LLM recibe la ventana de transcripción (dentro de `<untrusted_data>`) y devuelve JSON validado: `{score_text, category, reason, title_suggestion}`.
- El puntaje es una **estimación**, no una predicción de rendimiento; la interfaz lo comunica así.

### Evaluación de highlights
`tests/eval/highlights/` con videos propios de Favio etiquetados a mano (mínimo 10 videos de 20 min o más; el etiquetado lo hace Favio con una herramienta de la propia interfaz: marcar rangos buenos).
- Métricas: precisión@N, recall@N y coincidencia con IoU ≥ 0,5; acuerdo con aceptación/rechazo del creador (`highlight_candidates.status`).
- Hasta que exista el conjunto etiquetado, la prueba usa videos sintéticos con picos conocidos y el CI **bloquea regresiones** frente a la línea base guardada en `tests/eval/highlights/baseline.json`.

## 5. Reencuadre vertical

- Detección de rostro/persona con MediaPipe cada 3 fotogramas, interpolación entre muestras y suavizado exponencial (parámetro `smoothing`), con zona muerta para evitar temblor.
- `follow`: recorte 9:16 que sigue al sujeto; `split`: webcam arriba y gameplay abajo (región de webcam definida por plantilla o detectada); `fit`: video completo con fondo configurable (color sólido, imagen o copia ampliada).
- El resultado se escribe en el documento como `crop` + `keyframes`, de modo que el usuario lo ajusta manualmente en el editor.
- Si no hay sujeto detectable, se usa el centro y se marca el clip con un aviso de revisión.

## 6. Directos (`live-ingest`)

- **OBS → MediaMTX** (RTMP/SRT). Ajustes recomendados que la interfaz muestra al usuario: H.264 CBR, intervalo de fotogramas clave 2 s, AAC.
- `live-ingest` genera segmentos fMP4 de 2 s hacia `org/{org}/live/{session}/seg_{seq}.m4s` y registra `stream_segments` con `pts_start_ms` y `started_at`.
- **Buffer circular:** se retienen los segmentos `buffer_retention_s` (24 h por defecto); el `scheduler` borra los vencidos.
- **Clip ahora:** segmentos en `[t − pre_roll, t + post_roll]` → concatenación sin recodificar (`-c copy`, lista `concat`) → `media_assets(source_type=live_clip)`. El recorte exacto lo hace el render.
- **Fin del directo:** los segmentos se unen en una grabación completa (`live_recording`) que pasa a ser un `media_asset` listo para análisis.
- **Reconexión:** si OBS se corta, la sesión pasa a `live` de nuevo al reanudar la señal dentro de 120 s; los huecos se registran en `meta.gaps`.
- **obs-websocket v5:** conexión opcional desde `live-ingest` para leer estado, disparar el buffer de repetición y crear marcadores.
- **Twitch:** EventSub (`stream.online`, `stream.offline`) y creación de clips por la API oficial con el permiso correspondiente del usuario. Los clips de Twitch se importan como `media_assets(source_type=twitch_clip)` solo si la API y las condiciones vigentes lo permiten.
- **Chat:** lectura del chat del propio canal para calcular `chat_activity_buckets` (solo conteos, no se almacenan mensajes).
- **Sin descarga de transmisiones de terceros** (regla 11 de `CLAUDE.md`).

## 7. Formatos y presets

| Preset | Resolución | FPS | Uso |
|---|---|---|---|
| `9x16_1080_30` | 1080×1920 | 30 | Shorts, TikTok, Reels |
| `16x9_1080_30` | 1920×1080 | 30 | YouTube horizontal |
| `1x1_1080_30` | 1080×1080 | 30 | Feed cuadrado |
| `4x5_1080_30` | 1080×1350 | 30 | Feed vertical |

Los límites de duración y tamaño de cada red se validan en `PublishAdapter.validate` con los valores vigentes de su documentación oficial al implementar cada adaptador.
