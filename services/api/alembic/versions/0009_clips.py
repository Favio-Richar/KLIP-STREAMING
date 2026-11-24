"""clips: clips, documentos de edición y versiones inmutables (docs/04 §3.6).

- FKs compuestas (org_id, x_id) y RLS por organización en las tres tablas.
- `edit_projects.revision` solo avanza (concurrencia optimista; docs/04 §6.3).
- `clip_versions` es inmutable (docs/04 §6.2): un trigger rechaza todo UPDATE y solo permite DELETE
  cuando el rol de sistema lo habilita explícitamente (`SET LOCAL klick.allow_version_delete = 'on'`,
  que usa la retención al borrar un clip definitivamente). La aplicación (`klick_app`) no puede.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("clips", "edit_projects", "clip_versions")
UPDATED_AT_TABLES = ("clips", "edit_projects")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE clips (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id uuid,
            source_asset_id uuid NOT NULL,
            title text NOT NULL,
            status text NOT NULL DEFAULT 'draft',
            candidate_id uuid,  -- FK a highlight_candidates en F2-06
            current_version_id uuid,
            created_by_type text NOT NULL DEFAULT 'user',
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            deleted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            tsv tsvector GENERATED ALWAYS AS (to_tsvector('spanish'::regconfig, coalesce(title, ''))) STORED,
            CONSTRAINT uq_clips__org_id_id UNIQUE (org_id, id),
            CONSTRAINT fk_clips__project FOREIGN KEY (org_id, project_id)
                REFERENCES projects (org_id, id) ON DELETE SET NULL (project_id),
            CONSTRAINT fk_clips__source_asset FOREIGN KEY (org_id, source_asset_id)
                REFERENCES media_assets (org_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_clips__title CHECK (length(btrim(title)) BETWEEN 1 AND 300),
            CONSTRAINT ck_clips__status CHECK (status IN ('draft','ready','archived')),
            CONSTRAINT ck_clips__created_by_type CHECK (
                created_by_type IN ('user','api_token','mcp','agent','system'))
        )
        """
    )
    op.execute("CREATE INDEX ix_clips__org_id_created_at ON clips (org_id, created_at DESC)")
    op.execute("CREATE INDEX ix_clips__source_asset_id ON clips (source_asset_id)")
    op.execute("CREATE INDEX ix_clips__project_id ON clips (project_id)")
    op.execute("CREATE INDEX ix_clips__tsv ON clips USING gin (tsv)")
    op.execute("CREATE INDEX ix_clips__title_trgm ON clips USING gin (title gin_trgm_ops)")

    op.execute(
        """
        CREATE TABLE edit_projects (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            clip_id uuid NOT NULL,
            schema_version text NOT NULL,
            document jsonb NOT NULL,
            revision integer NOT NULL DEFAULT 1,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            CONSTRAINT uq_edit_projects__clip_id UNIQUE (clip_id),
            CONSTRAINT fk_edit_projects__clip FOREIGN KEY (org_id, clip_id)
                REFERENCES clips (org_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_edit_projects__revision CHECK (revision >= 1),
            CONSTRAINT ck_edit_projects__document CHECK (jsonb_typeof(document) = 'object')
        )
        """
    )
    op.execute("CREATE INDEX ix_edit_projects__org_id ON edit_projects (org_id)")

    op.execute(
        """
        CREATE TABLE clip_versions (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            clip_id uuid NOT NULL,
            version integer NOT NULL,
            schema_version text NOT NULL,
            document jsonb NOT NULL,
            label text,
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_clip_versions__org_id_id UNIQUE (org_id, id),
            CONSTRAINT uq_clip_versions__clip_id_version UNIQUE (clip_id, version),
            CONSTRAINT fk_clip_versions__clip FOREIGN KEY (org_id, clip_id)
                REFERENCES clips (org_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_clip_versions__version CHECK (version >= 1),
            CONSTRAINT ck_clip_versions__label CHECK (label IS NULL OR length(label) <= 200),
            CONSTRAINT ck_clip_versions__document CHECK (jsonb_typeof(document) = 'object')
        )
        """
    )
    op.execute("CREATE INDEX ix_clip_versions__org_id ON clip_versions (org_id)")

    # clips.current_version_id apunta a una versión de la misma organización
    op.execute(
        "ALTER TABLE clips ADD CONSTRAINT fk_clips__current_version "
        "FOREIGN KEY (org_id, current_version_id) REFERENCES clip_versions (org_id, id) "
        "ON DELETE SET NULL (current_version_id)"
    )

    # --- clip_versions es inmutable (docs/04 §6.2) -------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_clip_version_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_setting('klick.allow_version_delete', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'clip_versions is immutable (% rejected)', TG_OP
                USING ERRCODE = 'integrity_constraint_violation',
                      CONSTRAINT = 'ck_clip_versions__immutable';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_clip_versions__immutable BEFORE UPDATE OR DELETE ON clip_versions "
        "FOR EACH ROW EXECUTE FUNCTION forbid_clip_version_change()"
    )

    # --- la revisión solo avanza (docs/04 §6.3) ----------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION edit_project_revision_only_forward() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.revision < OLD.revision THEN
                RAISE EXCEPTION 'edit_projects.revision cannot decrease (% -> %)', OLD.revision, NEW.revision
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_edit_projects__revision_forward';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_edit_projects__revision BEFORE UPDATE ON edit_projects "
        "FOR EACH ROW EXECUTE FUNCTION edit_project_revision_only_forward()"
    )

    # Defensa en profundidad: además del trigger, ningún rol de la aplicación tiene el privilegio.
    # Los borrados en cascada de la retención los ejecuta el dueño de la tabla.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON clip_versions FROM klick_app, klick_system")

    for table in UPDATED_AT_TABLES:
        op.execute(f"SELECT apply_updated_at_trigger('{table}')")
    for table in TABLES:
        op.execute(f"SELECT apply_tenant_rls('{table}')")


def downgrade() -> None:
    op.execute("ALTER TABLE clips DROP CONSTRAINT IF EXISTS fk_clips__current_version")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS edit_project_revision_only_forward()")
    op.execute("DROP FUNCTION IF EXISTS forbid_clip_version_change()")
