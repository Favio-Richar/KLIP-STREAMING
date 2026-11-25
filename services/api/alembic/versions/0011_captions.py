"""captions: pistas de subtítulos manuales (docs/04 §3.6) e instantánea dentro de cada versión.

- `caption_tracks.cues` es un arreglo JSON validado por la aplicación (`klick.captions.cues`) y por
  un CHECK de tipo. `revision` avanza en cada cambio (concurrencia optimista, igual que
  `edit_projects`).
- `clip_versions.captions` guarda las señales (cues) vigentes al congelar la versión: un render
  pasado se reproduce con exactamente los subtítulos que tenía (docs/07 §2, plantillas "se resuelven
  al renderizar y se guardan en la versión"). La columna es nueva y nullable; el trigger de
  inmutabilidad solo rechaza UPDATE/DELETE, no cambia.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE caption_tracks (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            clip_id uuid,
            asset_id uuid,
            language text NOT NULL DEFAULT 'es',
            source text NOT NULL DEFAULT 'manual',
            cues jsonb NOT NULL DEFAULT '[]'::jsonb,
            status text NOT NULL DEFAULT 'ready',
            revision integer NOT NULL DEFAULT 1,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz,
            CONSTRAINT uq_caption_tracks__org_id_id UNIQUE (org_id, id),
            CONSTRAINT fk_caption_tracks__clip FOREIGN KEY (org_id, clip_id)
                REFERENCES clips (org_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_caption_tracks__asset FOREIGN KEY (org_id, asset_id)
                REFERENCES media_assets (org_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_caption_tracks__owner CHECK (clip_id IS NOT NULL OR asset_id IS NOT NULL),
            CONSTRAINT ck_caption_tracks__source CHECK (source IN ('transcript','translation','manual')),
            CONSTRAINT ck_caption_tracks__status CHECK (status IN ('ready','processing','failed')),
            CONSTRAINT ck_caption_tracks__language CHECK (language ~ '^[a-z]{2,3}(-[A-Za-z]{2,4})?$'),
            CONSTRAINT ck_caption_tracks__cues CHECK (jsonb_typeof(cues) = 'array'),
            CONSTRAINT ck_caption_tracks__revision CHECK (revision >= 1)
        )
        """
    )
    op.execute("CREATE INDEX ix_caption_tracks__org_id ON caption_tracks (org_id)")
    op.execute("CREATE INDEX ix_caption_tracks__clip ON caption_tracks (org_id, clip_id)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION caption_track_revision_only_forward() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.revision < OLD.revision THEN
                RAISE EXCEPTION 'caption_tracks.revision cannot decrease (% -> %)', OLD.revision, NEW.revision
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_caption_tracks__revision_forward';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_caption_tracks__revision BEFORE UPDATE ON caption_tracks "
        "FOR EACH ROW EXECUTE FUNCTION caption_track_revision_only_forward()"
    )
    op.execute("SELECT apply_updated_at_trigger('caption_tracks')")
    op.execute("SELECT apply_tenant_rls('caption_tracks')")

    op.execute("ALTER TABLE clip_versions ADD COLUMN captions jsonb")
    op.execute(
        "ALTER TABLE clip_versions ADD CONSTRAINT ck_clip_versions__captions "
        "CHECK (captions IS NULL OR jsonb_typeof(captions) = 'object')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE clip_versions DROP CONSTRAINT IF EXISTS ck_clip_versions__captions")
    op.execute("ALTER TABLE clip_versions DROP COLUMN IF EXISTS captions")
    op.execute("DROP TABLE IF EXISTS caption_tracks CASCADE")
    op.execute("DROP FUNCTION IF EXISTS caption_track_revision_only_forward()")
