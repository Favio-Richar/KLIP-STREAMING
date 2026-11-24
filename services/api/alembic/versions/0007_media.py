"""media: proyectos, carpetas, etiquetas, assets, derivados y subidas (docs/04 §3.3).

- FKs compuestas (org_id, x_id): una fila nunca puede apuntar a un recurso de otra organización.
- RLS por organización en todas las tablas (apply_tenant_rls).
- Triggers: conteo de referencias de storage_objects (docs/04 §6.6), invariante de assets
  `ready` (§6.1) y coherencia de proyecto entre carpetas padre e hijas.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "projects",
    "folders",
    "media_assets",
    "media_derivatives",
    "uploads",
    "tags",
    "asset_tags",
)
UPDATED_AT_TABLES = ("projects", "folders", "media_assets", "media_derivatives", "uploads")


def upgrade() -> None:
    # Necesario para las FKs compuestas que apuntan a storage_objects.
    op.execute(
        "ALTER TABLE storage_objects ADD CONSTRAINT uq_storage_objects__org_id_id UNIQUE (org_id, id)"
    )

    op.execute(
        """
        CREATE TABLE projects (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name text NOT NULL,
            description text,
            brand_kit_id uuid,  -- FK a brand_kits en F1-12
            settings jsonb NOT NULL DEFAULT '{}'::jsonb,
            archived_at timestamptz,
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            deleted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            CONSTRAINT uq_projects__org_id_id UNIQUE (org_id, id),
            CONSTRAINT ck_projects__name CHECK (length(btrim(name)) BETWEEN 1 AND 200)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_projects__org_id_name ON projects (org_id, lower(name)) "
        "WHERE deleted_at IS NULL"
    )
    op.execute("CREATE INDEX ix_projects__org_id_created_at ON projects (org_id, created_at DESC)")

    op.execute(
        """
        CREATE TABLE folders (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id uuid,
            parent_id uuid,
            name text NOT NULL,
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            CONSTRAINT uq_folders__org_id_id UNIQUE (org_id, id),
            CONSTRAINT fk_folders__project FOREIGN KEY (org_id, project_id)
                REFERENCES projects (org_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_folders__parent FOREIGN KEY (org_id, parent_id)
                REFERENCES folders (org_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_folders__name CHECK (length(btrim(name)) BETWEEN 1 AND 200),
            CONSTRAINT ck_folders__not_own_parent CHECK (parent_id IS NULL OR parent_id <> id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_folders__siblings ON folders ("
        "org_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid), "
        "COALESCE(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))"
    )
    op.execute("CREATE INDEX ix_folders__project_id ON folders (project_id)")
    op.execute("CREATE INDEX ix_folders__parent_id ON folders (parent_id)")

    op.execute(
        """
        CREATE TABLE media_assets (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id uuid,
            folder_id uuid,
            kind text NOT NULL,
            source_type text NOT NULL DEFAULT 'upload',
            title text NOT NULL,
            original_filename text,
            status text NOT NULL DEFAULT 'uploading',
            storage_object_id uuid NOT NULL,
            duration_ms bigint,
            width integer,
            height integer,
            fps numeric(6,3),
            video_codec text,
            audio_codec text,
            container text,
            bitrate_kbps integer,
            has_audio boolean,
            probe jsonb,
            error jsonb,
            live_session_id uuid,  -- FK a live_sessions en F4
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            deleted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            tsv tsvector GENERATED ALWAYS AS (
                to_tsvector('spanish'::regconfig,
                            coalesce(title, '') || ' ' || coalesce(original_filename, ''))
            ) STORED,
            CONSTRAINT uq_media_assets__org_id_id UNIQUE (org_id, id),
            CONSTRAINT fk_media_assets__project FOREIGN KEY (org_id, project_id)
                REFERENCES projects (org_id, id) ON DELETE SET NULL (project_id),
            CONSTRAINT fk_media_assets__folder FOREIGN KEY (org_id, folder_id)
                REFERENCES folders (org_id, id) ON DELETE SET NULL (folder_id),
            CONSTRAINT fk_media_assets__storage_object FOREIGN KEY (org_id, storage_object_id)
                REFERENCES storage_objects (org_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_media_assets__kind CHECK (kind IN ('video','audio','image')),
            CONSTRAINT ck_media_assets__source_type CHECK (
                source_type IN ('upload','live_recording','live_clip','twitch_clip','render')),
            CONSTRAINT ck_media_assets__status CHECK (
                status IN ('uploading','processing','ready','failed')),
            CONSTRAINT ck_media_assets__title CHECK (length(btrim(title)) BETWEEN 1 AND 300),
            CONSTRAINT ck_media_assets__dimensions CHECK (
                (width IS NULL OR width > 0) AND (height IS NULL OR height > 0)
                AND (fps IS NULL OR fps > 0) AND (duration_ms IS NULL OR duration_ms >= 0)
                AND (bitrate_kbps IS NULL OR bitrate_kbps >= 0)),
            -- docs/04 §6.1: un asset listo (salvo imagen) tiene duración conocida
            CONSTRAINT ck_media_assets__ready_has_duration CHECK (
                status <> 'ready' OR kind = 'image' OR duration_ms IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_media_assets__org_id_created_at ON media_assets (org_id, created_at DESC)"
    )
    op.execute("CREATE INDEX ix_media_assets__project_id ON media_assets (project_id)")
    op.execute("CREATE INDEX ix_media_assets__folder_id ON media_assets (folder_id)")
    op.execute(
        "CREATE INDEX ix_media_assets__storage_object_id ON media_assets (storage_object_id)"
    )
    op.execute("CREATE INDEX ix_media_assets__tsv ON media_assets USING gin (tsv)")
    op.execute(
        "CREATE INDEX ix_media_assets__title_trgm ON media_assets USING gin (title gin_trgm_ops)"
    )

    op.execute(
        """
        CREATE TABLE media_derivatives (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            asset_id uuid NOT NULL,
            kind text NOT NULL,
            storage_object_id uuid NOT NULL,
            meta jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            CONSTRAINT fk_media_derivatives__asset FOREIGN KEY (org_id, asset_id)
                REFERENCES media_assets (org_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_media_derivatives__storage_object FOREIGN KEY (org_id, storage_object_id)
                REFERENCES storage_objects (org_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_media_derivatives__asset_id_kind UNIQUE (asset_id, kind),
            CONSTRAINT ck_media_derivatives__kind CHECK (
                kind IN ('proxy_720p','thumbnail','sprite','waveform','audio_16k'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_media_derivatives__storage_object_id ON media_derivatives (storage_object_id)"
    )
    op.execute("CREATE INDEX ix_media_derivatives__org_id ON media_derivatives (org_id)")

    op.execute(
        """
        CREATE TABLE uploads (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            asset_id uuid NOT NULL,
            storage_object_id uuid NOT NULL,
            s3_upload_id text,
            part_size integer,
            size_bytes bigint,
            parts jsonb NOT NULL DEFAULT '[]'::jsonb,
            status text NOT NULL DEFAULT 'open',
            expires_at timestamptz NOT NULL,
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            CONSTRAINT fk_uploads__asset FOREIGN KEY (org_id, asset_id)
                REFERENCES media_assets (org_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_uploads__storage_object FOREIGN KEY (org_id, storage_object_id)
                REFERENCES storage_objects (org_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_uploads__status CHECK (
                status IN ('open','completing','completed','aborted','expired')),
            CONSTRAINT ck_uploads__sizes CHECK (
                (part_size IS NULL OR part_size > 0) AND (size_bytes IS NULL OR size_bytes >= 0))
        )
        """
    )
    op.execute("CREATE INDEX ix_uploads__asset_id ON uploads (asset_id)")
    op.execute("CREATE INDEX ix_uploads__storage_object_id ON uploads (storage_object_id)")
    op.execute("CREATE INDEX ix_uploads__org_id ON uploads (org_id)")
    op.execute("CREATE INDEX ix_uploads__open_expiry ON uploads (expires_at) WHERE status = 'open'")

    op.execute(
        """
        CREATE TABLE tags (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_tags__org_id_id UNIQUE (org_id, id),
            CONSTRAINT ck_tags__name CHECK (length(btrim(name)) BETWEEN 1 AND 60)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_tags__org_id_name ON tags (org_id, lower(name))")
    op.execute("CREATE INDEX ix_tags__name_trgm ON tags USING gin (name gin_trgm_ops)")

    op.execute(
        """
        CREATE TABLE asset_tags (
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            asset_id uuid NOT NULL,
            tag_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_asset_tags PRIMARY KEY (asset_id, tag_id),
            CONSTRAINT fk_asset_tags__asset FOREIGN KEY (org_id, asset_id)
                REFERENCES media_assets (org_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_asset_tags__tag FOREIGN KEY (org_id, tag_id)
                REFERENCES tags (org_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_asset_tags__tag_id ON asset_tags (tag_id)")
    op.execute("CREATE INDEX ix_asset_tags__org_id ON asset_tags (org_id)")

    # --- conteo de referencias de storage_objects (docs/04 §6.6) ------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION storage_ref_adjust(obj uuid, delta integer) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF delta > 0 THEN
                UPDATE storage_objects
                   SET ref_count = ref_count + delta,
                       status = CASE WHEN status = 'orphan' THEN 'ready' ELSE status END,
                       orphaned_at = CASE WHEN status = 'orphan' THEN NULL ELSE orphaned_at END
                 WHERE id = obj;
            ELSE
                UPDATE storage_objects
                   SET ref_count = greatest(ref_count + delta, 0),
                       status = CASE WHEN status = 'ready' AND greatest(ref_count + delta, 0) = 0
                                     THEN 'orphan' ELSE status END,
                       orphaned_at = CASE WHEN status = 'ready' AND greatest(ref_count + delta, 0) = 0
                                          THEN now() ELSE orphaned_at END
                 WHERE id = obj;
            END IF;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_storage_ref() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                PERFORM storage_ref_adjust(NEW.storage_object_id, 1);
            ELSIF TG_OP = 'DELETE' THEN
                PERFORM storage_ref_adjust(OLD.storage_object_id, -1);
            ELSIF NEW.storage_object_id IS DISTINCT FROM OLD.storage_object_id THEN
                PERFORM storage_ref_adjust(OLD.storage_object_id, -1);
                PERFORM storage_ref_adjust(NEW.storage_object_id, 1);
            END IF;
            RETURN NULL;
        END $$
        """
    )
    for table in ("media_assets", "media_derivatives"):
        op.execute(
            f"CREATE TRIGGER trg_{table}__storage_ref AFTER INSERT OR UPDATE OF storage_object_id "
            f"OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sync_storage_ref()"
        )

    # --- docs/04 §6.1: un asset `ready` apunta a un objeto `ready` ------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION check_asset_ready() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE obj_status text;
        BEGIN
            IF NEW.status = 'ready' THEN
                SELECT status INTO obj_status FROM storage_objects WHERE id = NEW.storage_object_id;
                IF obj_status IS DISTINCT FROM 'ready' THEN
                    RAISE EXCEPTION 'asset ready requires a ready storage object (is %)', obj_status
                        USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_media_assets__ready_object';
                END IF;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_media_assets__ready BEFORE INSERT OR UPDATE OF status, storage_object_id "
        "ON media_assets FOR EACH ROW EXECUTE FUNCTION check_asset_ready()"
    )

    # --- una carpeta hija vive en el mismo proyecto que su padre -------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION check_folder_parent() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE parent_project uuid;
        BEGIN
            IF NEW.parent_id IS NOT NULL THEN
                SELECT project_id INTO parent_project FROM folders
                 WHERE org_id = NEW.org_id AND id = NEW.parent_id;
                IF parent_project IS DISTINCT FROM NEW.project_id THEN
                    RAISE EXCEPTION 'folder and parent must belong to the same project'
                        USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_folders__same_project';
                END IF;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_folders__parent BEFORE INSERT OR UPDATE OF parent_id, project_id "
        "ON folders FOR EACH ROW EXECUTE FUNCTION check_folder_parent()"
    )

    for table in UPDATED_AT_TABLES:
        op.execute(f"SELECT apply_updated_at_trigger('{table}')")
    for table in TABLES:
        op.execute(f"SELECT apply_tenant_rls('{table}')")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS check_folder_parent()")
    op.execute("DROP FUNCTION IF EXISTS check_asset_ready()")
    op.execute("DROP FUNCTION IF EXISTS sync_storage_ref()")
    op.execute("DROP FUNCTION IF EXISTS storage_ref_adjust(uuid, integer)")
    op.execute(
        "ALTER TABLE storage_objects DROP CONSTRAINT IF EXISTS uq_storage_objects__org_id_id"
    )
