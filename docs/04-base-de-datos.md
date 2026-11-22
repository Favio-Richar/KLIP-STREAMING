# Base de datos

PostgreSQL 17. Esta es la especificación **completa** del esquema. Claude Code crea una migración Alembic por dominio, en el orden de las fases del roadmap (cada fase crea solo las tablas que necesita; este documento define todas).

## 1. Convenciones

- **Clave primaria:** `id uuid` UUIDv7 generado en la aplicación.
- **Auditoría básica:** `created_at timestamptz not null default now()`, `updated_at timestamptz` (trigger) y, cuando aplica, `created_by`.
- **Tenant:** toda tabla de negocio tiene `org_id uuid not null references organizations(id)`.
- **Borrado lógico:** `deleted_at timestamptz` solo donde se indica (`SD`). El borrado definitivo lo hace el job de retención.
- **Enums:** `text` con `CHECK` (no tipos `enum` nativos) para facilitar migraciones.
- **JSON:** `jsonb`; el contenido se valida con Pydantic y, para el documento de edición, con JSON Schema.
- **Nombres:** `snake_case`, tablas en plural, FKs `<entidad>_id`, índices `ix_<tabla>__<columnas>`, únicos `uq_<tabla>__<columnas>`.
- **Dinero y costos:** `numeric(14,6)`. Cantidades de consumo: `numeric(18,6)`.
- **Vectores:** `vector(1024)` (BGE-M3) con índice HNSW (`vector_cosine_ops`).
- **Texto buscable:** columna `tsv tsvector` generada con configuración `spanish` + índice GIN; `pg_trgm` para búsqueda aproximada de títulos y etiquetas.
- `FK` hacia tablas con `org_id`: `FOREIGN KEY (org_id, x_id)` compuesta cuando se quiere impedir cruces entre organizaciones (obligatorio en `clips`, `publications`, `render_jobs`, `agent_*`, `workflow_*`).

## 2. Seguridad de datos: roles y RLS

Roles:
- `klick_owner`: dueño del esquema; solo lo usa Alembic.
- `klick_app`: usado por API y workers. **Sin `BYPASSRLS`**. Permisos DML sobre tablas.
- `klick_system`: usado únicamente por `scheduler` (reconciliador, particiones, GC, retención) y migraciones de datos. Con `BYPASSRLS`. Toda operación se registra en `audit_events` con `actor_type='system'`.

Contexto de tenant: en cada transacción, la capa `core.db` ejecuta `SELECT set_config('app.org_id', :org_id, true)` (equivalente a `SET LOCAL`) antes de cualquier consulta. Si no hay contexto, `current_setting('app.org_id', true)` es nulo y la política no devuelve filas.

Política estándar (se genera con una función de migración `apply_tenant_rls(table)`):
```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <t>
  USING      (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
  WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
```
Tablas sin `org_id` propio (`users`, `sessions`, `permissions`, `mfa_*`, `auth_tokens`, `user_identities`, `mcp_clients`) se protegen por la capa de servicio y por restricciones de columna; sus pruebas de acceso van en `tests/security`.

Tablas de referencia del sistema con `org_id` nulo permitido (`roles`, `render_presets`, `ai_providers`, `ai_models`, `prompt_templates`, `templates`): política de lectura `org_id IS NULL OR org_id = <contexto>`; escritura solo con `org_id = <contexto>`.

**Prueba obligatoria** (`tests/security/test_rls_isolation.py`): para cada tabla con `org_id` se generan dos organizaciones, se insertan filas en ambas y se verifica que (a) con contexto A no se leen filas de B, (b) no se puede insertar ni actualizar con `org_id` de B, (c) sin contexto no se ve nada. Si se añade una tabla con `org_id` sin política, la prueba falla.

## 3. Esquema por dominio

Leyenda: **PK** clave primaria, **FK** clave foránea, **U** único, **SD** borrado lógico, **P** particionada por rango mensual sobre `created_at` (o la columna indicada).

### 3.1 Identidad (sin `org_id`)

| Tabla | Columnas principales |
|---|---|
| `users` | `id`, `email citext U`, `password_hash null`, `display_name`, `avatar_storage_object_id null`, `locale default 'es-CL'`, `timezone default 'America/Santiago'`, `is_active bool`, `email_verified_at null`, `last_login_at null`, `is_platform_admin bool default false` |
| `user_identities` | `id`, `user_id FK`, `provider` (`google`), `subject`, `email`, `U(provider, subject)` |
| `sessions` | `id`, `user_id FK`, `refresh_token_hash U`, `family_id` (detección de reutilización), `user_agent`, `ip inet`, `expires_at`, `revoked_at null`, `rotated_from null` |
| `mfa_factors` | `id`, `user_id FK`, `type` (`totp`), `secret_encrypted bytea`, `confirmed_at null` |
| `mfa_recovery_codes` | `id`, `user_id FK`, `code_hash`, `used_at null` |
| `auth_tokens` | `id`, `user_id FK`, `purpose` (`verify_email`,`reset_password`), `token_hash U`, `expires_at`, `used_at null` |

### 3.2 Organizaciones y permisos

| Tabla | Columnas principales |
|---|---|
| `organizations` | `id`, `name`, `slug U`, `plan_id null`, `settings jsonb` (idioma, zona horaria, reglas de publicación, pesos de highlights, retención), `created_by`, `SD` |
| `permissions` | `key PK` (p. ej. `clips:write`), `description` |
| `roles` | `id`, `org_id null` (null = rol de sistema), `key` (`owner`,`admin`,`editor`,`reviewer`,`viewer`), `name`, `is_system bool`, `U(org_id, key)` |
| `role_permissions` | `role_id FK`, `permission_key FK`, `PK(role_id, permission_key)` |
| `memberships` | `id`, `org_id`, `user_id FK`, `role_id FK`, `status` (`invited`,`active`,`suspended`), `U(org_id, user_id)` |
| `invitations` | `id`, `org_id`, `email`, `role_id`, `token_hash U`, `invited_by`, `expires_at`, `accepted_at null` |
| `api_tokens` | `id`, `org_id`, `user_id`, `name`, `prefix` (primeros 8 caracteres visibles), `token_hash U`, `scopes text[]`, `last_used_at`, `expires_at null`, `revoked_at null` |
| `devices` | `id`, `org_id`, `user_id`, `name`, `platform` (`windows`,`macos`,`linux`,`web`), `app_version`, `last_seen_at` |

### 3.3 Almacenamiento y media

| Tabla | Columnas principales |
|---|---|
| `storage_objects` | `id`, `org_id`, `backend` (`s3`), `bucket`, `key`, `size_bytes bigint`, `sha256 bytea null`, `mime`, `status` (`uploading`,`ready`,`orphan`,`deleted`), `ref_count int default 0`, `orphaned_at null`, `U(bucket, key)`, índice `(org_id, sha256)` |
| `projects` | `id`, `org_id`, `name`, `description`, `brand_kit_id null`, `settings jsonb`, `archived_at null`, `created_by`, `SD` |
| `folders` | `id`, `org_id`, `project_id null`, `parent_id null`, `name` |
| `media_assets` | `id`, `org_id`, `project_id null`, `folder_id null`, `kind` (`video`,`audio`,`image`), `source_type` (`upload`,`live_recording`,`live_clip`,`twitch_clip`,`render`), `title`, `original_filename`, `status` (`uploading`,`processing`,`ready`,`failed`), `storage_object_id FK`, `duration_ms`, `width`, `height`, `fps numeric(6,3)`, `video_codec`, `audio_codec`, `container`, `bitrate_kbps`, `has_audio bool`, `probe jsonb`, `live_session_id null`, `created_by`, `tsv`, `SD` |
| `media_derivatives` | `id`, `org_id`, `asset_id FK`, `kind` (`proxy_720p`,`thumbnail`,`sprite`,`waveform`,`audio_16k`), `storage_object_id FK`, `meta jsonb`, `U(asset_id, kind)` |
| `uploads` | `id`, `org_id`, `asset_id`, `storage_object_id`, `s3_upload_id`, `part_size`, `parts jsonb`, `status` (`open`,`completing`,`completed`,`aborted`,`expired`), `expires_at` |
| `tags` | `id`, `org_id`, `name`, `U(org_id, name)` |
| `asset_tags` | `asset_id`, `tag_id`, `PK(asset_id, tag_id)` |

### 3.4 Directos

| Tabla | Columnas principales |
|---|---|
| `platform_connections` | `id`, `org_id`, `platform` (`twitch`,`youtube`,`tiktok`,`instagram`,`facebook`,`kick`,`obs`), `external_account_id`, `display_name`, `scopes text[]`, `status` (`active`,`needs_reauth`,`revoked`,`error`), `credentials_encrypted bytea` (access + refresh, cifrado de sobre), `token_expires_at`, `publish_rule jsonb null` (reglas explícitas de publicación automática), `meta jsonb`, `connected_by`, `U(org_id, platform, external_account_id)` |
| `streams` | `id`, `org_id`, `connection_id null`, `platform`, `title`, `external_stream_id null` |
| `live_sessions` | `id`, `org_id`, `stream_id null`, `source_kind` (`obs_rtmp`,`obs_srt`,`twitch`,`manual`), `status` (`scheduled`,`live`,`ending`,`ended`,`failed`), `ingest_key_hash`, `started_at`, `ended_at`, `buffer_retention_s default 86400`, `pre_roll_s default 30`, `post_roll_s default 15`, `recording_asset_id null`, `meta jsonb` |
| `stream_segments` | `id`, `org_id`, `live_session_id`, `seq int`, `storage_object_id`, `started_at timestamptz`, `duration_ms`, `pts_start_ms bigint`, `expires_at`, **P** por `started_at`, `U(live_session_id, seq, started_at)` |
| `markers` | `id`, `org_id`, `live_session_id null`, `asset_id null`, `at_ms bigint` (desplazamiento), `wall_clock timestamptz null`, `kind` (`manual`,`hotkey`,`ai`,`chat_spike`,`twitch_clip`), `label`, `created_by_type` (`user`,`agent`,`system`), `created_by null`, `meta jsonb` |
| `chat_activity_buckets` | `live_session_id`, `bucket_start timestamptz`, `msg_count`, `unique_users`, `emote_count`, `org_id`, `PK(live_session_id, bucket_start)` |

### 3.5 Transcripción y análisis

| Tabla | Columnas principales |
|---|---|
| `transcripts` | `id`, `org_id`, `asset_id FK`, `language`, `engine` (`faster_whisper`,`provider`), `model`, `status`, `duration_ms`, `word_count`, `version int`, `is_current bool`, `U(asset_id, version)` |
| `transcript_segments` | `id`, `org_id`, `transcript_id FK`, `idx int`, `start_ms`, `end_ms`, `text`, `speaker null`, `words jsonb` (`[{w,s,e,p}]`), `tsv`, `embedding vector(1024) null`, `U(transcript_id, idx)`, GIN(`tsv`), HNSW(`embedding`) |
| `analysis_runs` | `id`, `org_id`, `asset_id`, `kind` (`segmentation`,`audio_signals`,`scene_detection`,`vision`,`highlights`), `status`, `params jsonb`, `engine_version`, `cost_usd`, `started_at`, `finished_at`, `job_id` |
| `highlight_candidates` | `id`, `org_id`, `asset_id`, `analysis_run_id`, `start_ms`, `end_ms`, `score numeric(5,2)`, `signals jsonb` (`{audio,text,chat,scene,vision,marker}`), `category` (`funny`,`clutch`,`fail`,`reaction`,`story`,`other`), `title_suggestion`, `reason`, `status` (`proposed`,`accepted`,`rejected`,`clipped`), `rating smallint null`, índice `(asset_id, score desc)` |

### 3.6 Clips y render

| Tabla | Columnas principales |
|---|---|
| `clips` | `id`, `org_id`, `project_id`, `source_asset_id FK`, `title`, `status` (`draft`,`ready`,`archived`), `candidate_id null`, `current_version_id null`, `created_by_type`, `created_by`, `tsv`, `SD` |
| `edit_projects` | `id`, `org_id`, `clip_id U`, `schema_version text`, `document jsonb`, `revision int` (concurrencia optimista), `updated_by` |
| `clip_versions` | `id`, `org_id`, `clip_id`, `version int`, `schema_version`, `document jsonb` (instantánea inmutable), `label null`, `created_by`, `U(clip_id, version)` |
| `render_presets` | `id`, `org_id null`, `key` (`9x16_1080_30`,`16x9_1080_30`,`1x1_1080_30`,`4x5_1080_30`), `container`, `video_codec`, `audio_codec`, `width`, `height`, `fps`, `crf`, `max_bitrate_kbps`, `audio_bitrate_kbps`, `loudness_target_lufs default -14`, `is_system` |
| `render_jobs` | `id`, `org_id`, `clip_version_id FK`, `preset_id FK`, `job_id FK`, `status` (`queued`,`running`,`succeeded`,`failed`,`canceled`), `progress smallint`, `output_asset_id null`, `ffmpeg_version`, `ffmpeg_args_hash`, `log_storage_object_id null`, `error jsonb null`, `started_at`, `finished_at` |
| `caption_tracks` | `id`, `org_id`, `clip_id null`, `asset_id null`, `language`, `source` (`transcript`,`translation`,`manual`), `transcript_id null`, `style_template_id null`, `cues jsonb` (`[{start_ms,end_ms,text,words}]`), `status` |

### 3.7 Diseño y marca

| Tabla | Columnas principales |
|---|---|
| `brand_kits` | `id`, `org_id`, `name`, `colors jsonb`, `fonts jsonb`, `logo_asset_id null`, `intro_asset_id null`, `outro_asset_id null`, `watermark jsonb null`, `is_default bool` |
| `templates` | `id`, `org_id null`, `kind` (`clip_layout`,`caption_style`,`overlay_pack`), `name`, `document jsonb` (validado por JSON Schema por `kind`), `thumbnail_asset_id null`, `is_system bool` |

### 3.8 Proveedores de IA y uso

| Tabla | Columnas principales |
|---|---|
| `ai_providers` | `id`, `org_id null`, `kind` (`openai`,`anthropic`,`google`,`openai_compatible`,`local`), `name`, `base_url null`, `status`, `config jsonb` |
| `provider_credentials` | `id`, `org_id`, `provider_id`, `mode` (`byok`,`platform`), `key_encrypted bytea`, `key_last4`, `rotated_at`, `last_check_at`, `last_check_ok bool` |
| `ai_models` | `id`, `provider_id`, `model_id` (texto del proveedor), `display_name`, `capabilities jsonb` (`chat`,`tools`,`vision`,`audio_in`,`structured_output`,`embedding`,`transcription`,`streaming`), `context_window`, `max_output`, `price_in_per_mtok null`, `price_out_per_mtok null`, `enabled`, `tier` (`fast`,`balanced`,`deep`), `U(provider_id, model_id)` |
| `model_policies` | `id`, `org_id`, `task_kind` (`reasoning`,`planning`,`structured`,`vision`,`transcription`,`embedding`,`creative`), `primary_model_id`, `fallback_model_ids uuid[]`, `constraints jsonb` (`max_cost_usd`, `max_latency_ms`, `data_residency`), `U(org_id, task_kind)` |
| `prompt_templates` | `id`, `org_id null`, `key`, `version int`, `task_kind`, `system text`, `user_template text`, `output_schema jsonb null`, `status` (`draft`,`active`,`retired`), `checksum`, `U(key, version)` |
| `ai_requests` | `id`, `org_id`, `run_id null`, `step_id null`, `task_kind`, `provider_id`, `model_id`, `prompt_key`, `prompt_version`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `status`, `error_code null`, `created_at`, **P** |
| `ai_request_payloads` | `request_id`, `created_at`, `org_id`, `request jsonb`, `response jsonb`, `expires_at` — solo si `organizations.settings.store_ai_payloads = true` |
| `usage_records` | `id`, `org_id`, `metric` (`stream_ingest_s`,`video_processed_s`,`transcription_s`,`vision_s`,`tokens_in`,`tokens_out`,`storage_gb_month`,`egress_gb`,`publications`,`render_s`), `quantity numeric(18,6)`, `unit`, `source_type`, `source_id`, `cost_usd numeric(14,6) null`, `billing_mode` (`platform`,`byok`), `occurred_at`, `idempotency_key U`, `billed bool default false` — **append-only** |

### 3.9 Agentes y memoria

| Tabla | Columnas principales |
|---|---|
| `agents` | `id`, `org_id null`, `key` (`orchestrator`,`live`,`highlights`,`editing`,`captions`,`creative`,`publishing`,`analytics`,`automation`), `name`, `prompt_key`, `tool_allowlist text[]`, `model_task_kind`, `enabled` |
| `agent_conversations` | `id`, `org_id`, `user_id`, `title`, `project_id null`, `archived_at null` |
| `agent_runs` | `id`, `org_id`, `conversation_id null`, `parent_run_id null`, `agent_id FK`, `initiated_by_type` (`user`,`api_token`,`mcp`,`automation`,`event`), `initiated_by_id`, `acting_user_id FK`, `status` (CREATED, PLANNING, QUEUED, RUNNING, VALIDATING, AWAITING_APPROVAL, COMPLETED, FAILED, RETRYING, CANCELLED, EXPIRED, PARTIAL_SUCCESS), `goal text`, `plan jsonb null`, `budget jsonb` (`max_cost_usd`,`max_steps`,`max_wall_s`), `spent_cost_usd`, `result jsonb null`, `error jsonb null`, `idempotency_key null`, `started_at`, `finished_at` |
| `agent_messages` | `id`, `org_id`, `conversation_id`, `run_id null`, `idx`, `role` (`user`,`assistant`,`tool`,`system`), `content jsonb`, `created_at` |
| `agent_steps` | `id`, `org_id`, `run_id FK`, `idx`, `kind` (`plan`,`llm`,`tool`,`delegate`,`wait_job`,`wait_approval`,`validate`), `status`, `input jsonb`, `output jsonb`, `started_at`, `finished_at` |
| `tool_calls` | `id`, `org_id`, `run_id`, `step_id`, `tool_name`, `risk_level` (`R0`..`R4`), `args jsonb`, `result jsonb`, `status` (`pending`,`awaiting_approval`,`running`,`succeeded`,`failed`,`denied`), `external_effect bool`, `idempotency_key`, `duration_ms`, `created_at`, **P** |
| `approvals` | `id`, `org_id`, `run_id`, `tool_call_id`, `requested_action jsonb` (qué, dónde, con qué efecto), `status` (`pending`,`approved`,`rejected`,`expired`), `decided_by null`, `decided_at null`, `expires_at` |
| `memory_entries` | `id`, `org_id`, `user_id null`, `project_id null`, `scope` (`org`,`user`,`project`), `kind` (`preference`,`template`,`decision`,`fact`), `content text`, `embedding vector(1024)`, `source_run_id null`, `last_used_at`, `SD` |
| `knowledge_sources` | `id`, `org_id`, `title`, `kind` (`document`,`url`), `storage_object_id null`, `status` |
| `knowledge_chunks` | `id`, `org_id`, `source_id FK`, `idx`, `text`, `tsv`, `embedding vector(1024)` |
| `feedback` | `id`, `org_id`, `user_id`, `target_type` (`clip`,`candidate`,`run`,`caption`,`publication`), `target_id`, `rating smallint`, `tags text[]`, `comment` |

### 3.10 MCP

| Tabla | Columnas principales |
|---|---|
| `mcp_clients` (sin `org_id`) | `client_id text PK`, `client_name`, `registration_type` (`dcr`,`cimd`), `redirect_uris text[]`, `metadata jsonb`, `created_at`, `revoked_at null` |
| `mcp_grants` | `id`, `org_id`, `user_id`, `client_id FK`, `scopes text[]`, `resource` (URL del MCP, audiencia), `refresh_token_hash`, `granted_at`, `last_used_at`, `revoked_at null` |
| `mcp_servers` | `id`, `org_id`, `name`, `url`, `auth_encrypted bytea null`, `allowed_tools text[]`, `status` — para el **cliente** MCP saliente |
| `mcp_tool_audits` | `id`, `org_id`, `grant_id null`, `client_id`, `tool`, `args_hash`, `args_redacted jsonb`, `result_status`, `duration_ms`, `ip inet`, `created_at`, **P** |

### 3.11 Automatizaciones

| Tabla | Columnas principales |
|---|---|
| `workflows` | `id`, `org_id`, `name`, `status` (`draft`,`active`,`paused`), `current_version_id null`, `created_by`, `SD` |
| `workflow_versions` | `id`, `org_id`, `workflow_id`, `version int`, `definition jsonb` (nodos, aristas, condiciones), `checksum`, `published_at null`, `U(workflow_id, version)` |
| `triggers` | `id`, `org_id`, `workflow_id`, `kind` (`manual`,`schedule`,`webhook`,`event`), `config jsonb`, `cron null`, `event_type null`, `secret_hash null`, `enabled`, `next_fire_at null` |
| `workflow_runs` | `id`, `org_id`, `workflow_version_id`, `trigger_id null`, `status`, `input jsonb`, `output jsonb`, `idempotency_key U`, `started_at`, `finished_at` |
| `workflow_run_steps` | `id`, `org_id`, `run_id`, `node_id`, `status`, `attempt`, `input jsonb`, `output jsonb`, `error jsonb`, `started_at`, `finished_at` |

### 3.12 Publicación y métricas

| Tabla | Columnas principales |
|---|---|
| `publication_batches` | `id`, `org_id`, `clip_version_id FK`, `name`, `created_by_type`, `created_by` |
| `publications` | `id`, `org_id`, `batch_id FK`, `connection_id FK`, `source_asset_id` (el render a publicar), `title`, `description`, `hashtags text[]`, `visibility`, `platform_options jsonb`, `status` (`draft`,`awaiting_approval`,`scheduled`,`sending`,`processing`,`published`,`error`,`canceled`), `scheduled_for timestamptz null`, `external_id null`, `external_url null`, `idempotency_key U`, `approved_by null`, `approved_at null`, `last_error jsonb null` |
| `delivery_attempts` | `id`, `org_id`, `publication_id FK`, `attempt int`, `status`, `request_summary jsonb`, `response_code null`, `external_state jsonb`, `error jsonb`, `started_at`, `finished_at` |
| `clip_metrics` | `id`, `org_id`, `publication_id`, `captured_at`, `views`, `likes`, `comments`, `shares`, `watch_time_s`, `avg_view_pct`, `raw jsonb`, **P** por `captured_at` |
| `channel_metrics` | `id`, `org_id`, `connection_id`, `captured_at`, `followers`, `views`, `raw jsonb` |
| `analytics_rollups` | `org_id`, `day date`, `dimension` (`org`,`channel`,`clip`,`category`), `key`, `metrics jsonb`, `PK(org_id, day, dimension, key)` |

### 3.13 Comercial (se crean en la fase 8; `usage_records` existe desde la fase 0)

| Tabla | Columnas principales |
|---|---|
| `plans` | `id`, `key` (`free`,`creator`,`pro`,`team`,`agency`,`enterprise`), `name`, `limits jsonb` (minutos de procesamiento, almacenamiento, publicaciones, ejecuciones del agente, concurrencia), `features text[]`, `is_active` |
| `subscriptions` | `id`, `org_id U`, `plan_id`, `status`, `current_period_start`, `current_period_end`, `provider`, `provider_subscription_id` |
| `invoices` | `id`, `org_id`, `subscription_id`, `period_start`, `period_end`, `total numeric(14,2)`, `currency`, `status`, `provider_invoice_id`, `lines jsonb` |

### 3.14 Sistema

| Tabla | Columnas principales |
|---|---|
| `jobs` | `id`, `org_id null`, `queue` (`default`,`media`,`ai`,`publish`), `kind`, `status` (`queued`,`running`,`succeeded`,`failed`,`retrying`,`canceled`,`expired`), `priority smallint`, `payload jsonb`, `result jsonb`, `progress smallint`, `attempts`, `max_attempts`, `run_after`, `locked_by`, `heartbeat_at`, `deadline_at`, `error jsonb`, `parent_job_id null`, `correlation_id`, `idempotency_key null`, `created_by_type`, `created_by`, `started_at`, `finished_at`; índices `(status, queue, run_after)`, `(org_id, created_at desc)`, único parcial `(org_id, kind, idempotency_key) WHERE idempotency_key IS NOT NULL` |
| `outbox_events` | `id`, `org_id null`, `type`, `schema_version`, `aggregate_type`, `aggregate_id`, `correlation_id`, `payload jsonb`, `created_at`, `published_at null`; índice parcial `WHERE published_at IS NULL` |
| `idempotency_keys` | `org_id`, `scope`, `key`, `request_hash`, `response jsonb null`, `status` (`in_progress`,`completed`), `expires_at`, `PK(org_id, scope, key)` |
| `audit_events` | `id`, `org_id null`, `actor_type` (`user`,`api_token`,`mcp`,`agent`,`system`), `actor_id`, `action`, `resource_type`, `resource_id`, `ip`, `user_agent`, `request_id`, `diff jsonb`, `created_at`, **P** — append-only (sin UPDATE/DELETE para `klick_app`) |
| `notifications` | `id`, `org_id`, `user_id`, `kind`, `title`, `body`, `data jsonb`, `read_at null`, `created_at` |
| `notification_preferences` | `user_id`, `org_id`, `kind`, `channels text[]` (`in_app`,`email`,`desktop`), `PK(user_id, org_id, kind)` |
| `webhook_endpoints` | `id`, `org_id`, `url`, `secret_encrypted`, `events text[]`, `enabled` |
| `webhook_deliveries` | `id`, `org_id`, `endpoint_id`, `event_id`, `attempt`, `status`, `response_code null`, `next_attempt_at null`, `created_at` |
| `retention_policies` | `id`, `org_id`, `resource_kind` (`stream_segments`,`derivatives`,`ai_payloads`,`audit_events`,`renders`), `days`, `action` (`delete`,`archive`) |
| `deletion_requests` | `id`, `org_id`, `user_id`, `scope` (`user`,`org`,`asset`), `status`, `requested_at`, `completed_at null` |
| `app_settings` | `org_id null`, `key`, `value jsonb`, `PK(org_id, key)`. Claves globales conocidas (`org_id` nulo): `publishing_paused` (bool), `maintenance_mode` (bool) |

## 4. Particionado y retención

| Tabla | Clave | Retención por defecto |
|---|---|---|
| `stream_segments` | `started_at` | según `live_sessions.buffer_retention_s` (la fila y el objeto se borran) |
| `ai_requests` | `created_at` | 400 días |
| `tool_calls` | `created_at` | 400 días |
| `mcp_tool_audits` | `created_at` | 400 días |
| `audit_events` | `created_at` | 730 días |
| `clip_metrics` | `captured_at` | 730 días |

Declarativo `PARTITION BY RANGE`. El `scheduler` crea las particiones de los siguientes 3 meses cada día y elimina (`DROP`) las vencidas. Una prueba verifica que existan las particiones futuras.

## 5. Índices obligatorios (además de los indicados)
- Todas las FKs que se consultan por padre (`..._id`) llevan índice.
- `(org_id, created_at desc)` en tablas listadas por la interfaz (`media_assets`, `clips`, `publications`, `agent_runs`, `jobs`).
- `media_assets`, `clips`: GIN sobre `tsv`; GIN `gin_trgm_ops` sobre `title`.
- `transcript_segments`: GIN `tsv` + HNSW `embedding`. `memory_entries` y `knowledge_chunks`: HNSW.
- `jobs`: índice parcial `WHERE status IN ('queued','retrying')` para el *dispatcher*.

## 6. Reglas de integridad críticas
1. Un `media_assets` en `ready` tiene `storage_object_id` en `ready` y `duration_ms` no nulo.
2. `clip_versions` es inmutable: trigger que rechaza `UPDATE`/`DELETE`.
3. `edit_projects.revision` solo avanza; la actualización exige `WHERE revision = :esperada`.
4. `publications.idempotency_key` es único; reintentar la misma publicación nunca crea otra fila ni otra publicación remota.
5. `usage_records` y `audit_events` son *append-only* (permisos y trigger).
6. `storage_objects.ref_count` se mantiene por trigger desde `media_assets`, `media_derivatives` y `knowledge_sources`; en cero pasa a `orphan` y el GC lo borra tras 24 h.
7. `agent_runs.acting_user_id` define la autoridad de la ejecución; ninguna herramienta puede exceder los permisos de ese usuario ni de la concesión MCP o token que la inició.
8. `platform_connections.credentials_encrypted` y `provider_credentials.key_encrypted` nunca se devuelven por API ni se registran.

## 7. Semillas (`pnpm db:seed`)
- `permissions` y los 5 roles de sistema con sus permisos.
- `render_presets` del sistema (4 formatos × 1080p/30 fps).
- `templates` del sistema: 3 `caption_style` (limpio, destacado palabra a palabra, contorno) y 3 `clip_layout` (`follow`, `split`, `fit`).
- `agents` del sistema (orquestador + 8 perfiles).
- `prompt_templates` iniciales versionados (planificación, puntuación de highlights, títulos, descripciones).
- `ai_providers` y `ai_models` iniciales: los identificadores de modelo se leen de la documentación oficial de cada proveedor **en el momento de la tarea** y se registran con sus capacidades; no se inventan.
