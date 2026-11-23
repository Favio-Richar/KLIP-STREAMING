"""Fundaciones: extensiones, roles, UUIDv7, trigger updated_at y apply_tenant_rls.

Revision ID: 0001
Revises:
"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORG = "nullif(current_setting(''app.org_id'', true), '''')::uuid"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Roles de ejecución (docs/04 §2). Las contraseñas vienen del entorno; los valores por
    # defecto son solo de desarrollo y .env.example los documenta.
    app_pw = _quote(os.environ.get("KLICK_APP_PASSWORD", "klick_app_dev_password"))
    sys_pw = _quote(os.environ.get("KLICK_SYSTEM_PASSWORD", "klick_system_dev_password"))
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'klick_app') THEN
            CREATE ROLE klick_app LOGIN NOBYPASSRLS NOSUPERUSER PASSWORD {app_pw};
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'klick_system') THEN
            CREATE ROLE klick_system LOGIN BYPASSRLS NOSUPERUSER PASSWORD {sys_pw};
          END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO klick_app, klick_system")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO klick_app, klick_system"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO klick_app, klick_system"
    )

    # UUIDv7 en SQL (PostgreSQL 17 no lo trae). La aplicación genera los IDs; esto es respaldo.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid
        LANGUAGE sql VOLATILE AS $$
          SELECT encode(
            set_bit(
              set_bit(
                overlay(uuid_send(gen_random_uuid())
                        placing substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
                        FROM 1 FOR 6),
                52, 1),
              53, 1),
            'hex')::uuid
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION apply_updated_at_trigger(tbl regclass) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
          EXECUTE format(
            'CREATE TRIGGER trg_set_updated_at BEFORE UPDATE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION set_updated_at()', tbl);
        END
        $$
        """
    )

    # Política estándar de aislamiento por organización (docs/04 §2).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION apply_tenant_rls(tbl regclass) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
          EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
          EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
          EXECUTE format(
            'CREATE POLICY tenant_isolation ON %s USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})',
            tbl);
        END
        $$
        """
    )
    # Variante para tablas de referencia con org_id nulo permitido (docs/04 §2).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION apply_reference_rls(tbl regclass) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
          EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
          EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
          EXECUTE format(
            'CREATE POLICY reference_read ON %s FOR SELECT USING (org_id IS NULL OR org_id = {_ORG})', tbl);
          EXECUTE format(
            'CREATE POLICY reference_insert ON %s FOR INSERT WITH CHECK (org_id = {_ORG})', tbl);
          EXECUTE format(
            'CREATE POLICY reference_update ON %s FOR UPDATE USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})', tbl);
          EXECUTE format(
            'CREATE POLICY reference_delete ON %s FOR DELETE USING (org_id = {_ORG})', tbl);
        END
        $$
        """
    )


def downgrade() -> None:
    for fn in (
        "apply_reference_rls(regclass)",
        "apply_tenant_rls(regclass)",
        "apply_updated_at_trigger(regclass)",
        "set_updated_at()",
        "uuid_generate_v7()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM klick_app, klick_system"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM klick_app, klick_system"
    )
    # Los roles son del clúster y pueden servir a otras bases: no se eliminan.
    # Las extensiones tampoco, por si otras migraciones o datos las usan.
