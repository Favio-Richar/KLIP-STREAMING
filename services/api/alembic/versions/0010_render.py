"""render: presets, trabajos de render y registro de consumo (docs/04 §3.6 y §3.13).

- `render_jobs` es único por (clip_version_id, preset_id): el resultado de un render es idempotente
  (docs/07 §3). Un reintento reutiliza la fila.
- `usage_records` es solo de inserción (sin UPDATE/DELETE para ningún rol de la aplicación) y su
  `idempotency_key` único impide contar dos veces el mismo consumo (p. ej. un job reintentado).
- `render_presets`: filas del sistema con `org_id` NULL (legibles por todas las organizaciones).

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE render_presets (
            id uuid PRIMARY KEY,
            org_id uuid REFERENCES organizations(id) ON DELETE CASCADE,
            key text NOT NULL,
            container text NOT NULL DEFAULT 'mp4',
            video_codec text NOT NULL DEFAULT 'libx264',
            audio_codec text NOT NULL DEFAULT 'aac',
            width integer NOT NULL,
            height integer NOT NULL,
            fps integer NOT NULL,
            crf integer NOT NULL DEFAULT 20,
            max_bitrate_kbps integer,
            audio_bitrate_kbps integer NOT NULL DEFAULT 192,
            loudness_target_lufs numeric(5,2) NOT NULL DEFAULT -14,
            is_system boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            CONSTRAINT ck_render_presets__size CHECK (
                width BETWEEN 16 AND 4096 AND height BETWEEN 16 AND 4096
                AND width % 2 = 0 AND height % 2 = 0),
            CONSTRAINT ck_render_presets__fps CHECK (fps IN (24, 25, 30, 50, 60)),
            CONSTRAINT ck_render_presets__crf CHECK (crf BETWEEN 0 AND 51),
            CONSTRAINT ck_render_presets__container CHECK (container IN ('mp4')),
            CONSTRAINT ck_render_presets__system_has_no_org CHECK (NOT is_system OR org_id IS NULL)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_render_presets__system_key ON render_presets (key) WHERE org_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_render_presets__org_key ON render_presets (org_id, key) WHERE org_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE render_jobs (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            clip_id uuid NOT NULL,
            clip_version_id uuid NOT NULL,
            preset_id uuid NOT NULL REFERENCES render_presets(id) ON DELETE RESTRICT,
            job_id uuid REFERENCES jobs(id) ON DELETE SET NULL,
            status text NOT NULL DEFAULT 'queued',
            progress smallint NOT NULL DEFAULT 0,
            output_asset_id uuid,
            ffmpeg_version text,
            ffmpeg_args_hash text,
            error jsonb,
            requested_by uuid REFERENCES users(id) ON DELETE SET NULL,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            CONSTRAINT uq_render_jobs__org_id_id UNIQUE (org_id, id),
            -- docs/07 §3: idempotente por (versión del clip, preset)
            CONSTRAINT uq_render_jobs__version_preset UNIQUE (clip_version_id, preset_id),
            CONSTRAINT fk_render_jobs__clip FOREIGN KEY (org_id, clip_id)
                REFERENCES clips (org_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_render_jobs__version FOREIGN KEY (org_id, clip_version_id)
                REFERENCES clip_versions (org_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_render_jobs__output FOREIGN KEY (org_id, output_asset_id)
                REFERENCES media_assets (org_id, id) ON DELETE SET NULL (output_asset_id),
            CONSTRAINT ck_render_jobs__status CHECK (
                status IN ('queued','running','succeeded','failed','canceled')),
            CONSTRAINT ck_render_jobs__progress CHECK (progress BETWEEN 0 AND 100),
            CONSTRAINT ck_render_jobs__succeeded_has_output CHECK (
                status <> 'succeeded' OR output_asset_id IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_render_jobs__org_id_created_at ON render_jobs (org_id, created_at DESC)"
    )
    op.execute("CREATE INDEX ix_render_jobs__clip_id ON render_jobs (clip_id)")
    op.execute("CREATE INDEX ix_render_jobs__job_id ON render_jobs (job_id)")
    op.execute("CREATE INDEX ix_render_jobs__preset_id ON render_jobs (preset_id)")

    op.execute(
        """
        CREATE TABLE usage_records (
            id uuid PRIMARY KEY,
            -- sin FK a organizations (como audit_events): el consumo registrado sobrevive al borrado
            -- de la organización y la regla de solo inserción no choca con una cascada
            org_id uuid NOT NULL,
            metric text NOT NULL,
            quantity numeric(18,6) NOT NULL,
            unit text NOT NULL,
            source_type text NOT NULL,
            source_id uuid,
            cost_usd numeric(14,6),
            billing_mode text NOT NULL DEFAULT 'platform',
            occurred_at timestamptz NOT NULL DEFAULT now(),
            idempotency_key text NOT NULL,
            billed boolean NOT NULL DEFAULT false,
            CONSTRAINT uq_usage_records__idempotency_key UNIQUE (idempotency_key),
            CONSTRAINT ck_usage_records__metric CHECK (metric IN (
                'stream_ingest_s','video_processed_s','transcription_s','vision_s','tokens_in',
                'tokens_out','storage_gb_month','egress_gb','publications','render_s')),
            CONSTRAINT ck_usage_records__billing_mode CHECK (billing_mode IN ('platform','byok')),
            CONSTRAINT ck_usage_records__quantity CHECK (quantity >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_usage_records__org_id_occurred_at ON usage_records (org_id, occurred_at DESC)"
    )
    op.execute("CREATE INDEX ix_usage_records__source ON usage_records (source_type, source_id)")
    # Solo de inserción: ningún rol de la aplicación puede modificar ni borrar el consumo registrado.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON usage_records FROM klick_app, klick_system")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_usage_record_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'usage_records is append-only (% rejected)', TG_OP
                USING ERRCODE = 'integrity_constraint_violation',
                      CONSTRAINT = 'ck_usage_records__append_only';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_usage_records__append_only BEFORE UPDATE OR DELETE ON usage_records "
        "FOR EACH ROW EXECUTE FUNCTION forbid_usage_record_change()"
    )

    for table in ("render_presets", "render_jobs"):
        op.execute(f"SELECT apply_updated_at_trigger('{table}')")
    op.execute("SELECT apply_tenant_rls('render_jobs')")
    op.execute("SELECT apply_tenant_rls('usage_records')")
    op.execute("SELECT apply_reference_rls('render_presets')")


def downgrade() -> None:
    for table in ("usage_records", "render_jobs", "render_presets"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS forbid_usage_record_change()")
