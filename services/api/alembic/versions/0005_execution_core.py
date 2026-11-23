"""Núcleo de ejecución: jobs, outbox_events, idempotency_keys y audit_events (particionada).

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG = "nullif(current_setting('app.org_id', true), '')::uuid"
PARTITIONED = ("audit_events",)


def upgrade() -> None:
    # ------------------------------------------------------------------------------- jobs
    op.create_table(
        "jobs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", pg.UUID(as_uuid=True)),
        sa.Column("queue", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("payload", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", pg.JSONB()),
        sa.Column("progress", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("locked_by", sa.Text()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("error", pg.JSONB()),
        sa.Column("parent_job_id", pg.UUID(as_uuid=True)),
        sa.Column("correlation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("created_by_type", sa.Text(), nullable=False, server_default="system"),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_jobs__org_id"
        ),
        sa.ForeignKeyConstraint(
            ["parent_job_id"], ["jobs.id"], ondelete="SET NULL", name="fk_jobs__parent_job_id"
        ),
        sa.CheckConstraint("queue in ('default','media','ai','publish')", name="ck_jobs__queue"),
        sa.CheckConstraint(
            "status in ('queued','running','succeeded','failed','retrying','canceled','expired')",
            name="ck_jobs__status",
        ),
        sa.CheckConstraint("progress between 0 and 100", name="ck_jobs__progress"),
        sa.CheckConstraint(
            "created_by_type in ('user','api_token','mcp','agent','system')",
            name="ck_jobs__created_by_type",
        ),
    )
    op.create_index("ix_jobs__status_queue_run_after", "jobs", ["status", "queue", "run_after"])
    op.execute("CREATE INDEX ix_jobs__org_id_created_at ON jobs (org_id, created_at DESC)")
    op.execute(
        "CREATE UNIQUE INDEX uq_jobs__org_id_kind_idempotency_key ON jobs "
        "(org_id, kind, idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    op.execute("SELECT apply_updated_at_trigger('jobs')")
    op.execute("SELECT apply_tenant_rls('jobs')")

    # -------------------------------------------------------------------------- outbox_events
    op.create_table(
        "outbox_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", pg.UUID(as_uuid=True)),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", pg.UUID(as_uuid=True)),
        sa.Column("correlation_id", pg.UUID(as_uuid=True)),
        sa.Column("payload", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_outbox_events__org_id"
        ),
    )
    op.execute(
        "CREATE INDEX ix_outbox_events__unpublished ON outbox_events (created_at) "
        "WHERE published_at IS NULL"
    )
    op.execute("SELECT apply_tenant_rls('outbox_events')")

    # ----------------------------------------------------------------------- idempotency_keys
    op.create_table(
        "idempotency_keys",
        sa.Column("org_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="in_progress"),
        sa.Column("response_status", sa.SmallInteger()),
        sa.Column("response", pg.JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "scope", "key", name="pk_idempotency_keys"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_idempotency_keys__org_id"
        ),
        sa.CheckConstraint(
            "status in ('in_progress','completed')", name="ck_idempotency_keys__status"
        ),
    )
    op.create_index("ix_idempotency_keys__expires_at", "idempotency_keys", ["expires_at"])
    op.execute("SELECT apply_tenant_rls('idempotency_keys')")

    # ------------------------------------------------------------------------- audit_events
    op.execute(
        """
        CREATE TABLE audit_events (
          id uuid NOT NULL,
          org_id uuid,  -- sin FK: la auditoría sobrevive a la organización (append-only)
          actor_type text NOT NULL
            CONSTRAINT ck_audit_events__actor_type
            CHECK (actor_type IN ('user','api_token','mcp','agent','system')),
          actor_id uuid,
          action text NOT NULL,
          resource_type text,
          resource_id text,
          ip inet,
          user_agent text,
          request_id text,
          diff jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_audit_events PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute(
        "CREATE INDEX ix_audit_events__org_id_created_at ON audit_events (org_id, created_at DESC)"
    )
    op.execute("CREATE INDEX ix_audit_events__action ON audit_events (action)")

    # Funciones de gestión de particiones: SECURITY DEFINER para que el rol de sistema (que no es
    # dueño de las tablas) pueda crear y eliminar particiones sin otros privilegios de DDL.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ensure_monthly_partitions(parent regclass, months_ahead int)
        RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE
          created int := 0;
          m date;
          part text;
          parent_name text := (SELECT relname FROM pg_class WHERE oid = parent);
        BEGIN
          FOR i IN 0..months_ahead LOOP
            m := (date_trunc('month', now() AT TIME ZONE 'UTC') + make_interval(months => i))::date;
            part := parent_name || '_' || to_char(m, 'YYYY_MM');
            IF to_regclass(part) IS NULL THEN
              EXECUTE format(
                'CREATE TABLE %I PARTITION OF %s FOR VALUES FROM (%L) TO (%L)',
                part, parent, m::timestamptz, (m + interval '1 month')::timestamptz);
              -- Solo se accede por la tabla padre: sin privilegios directos sobre la partición.
              EXECUTE format('REVOKE ALL ON %I FROM klick_app, klick_system', part);
              created := created + 1;
            END IF;
          END LOOP;
          RETURN created;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION drop_expired_partitions(parent regclass, retention_days int)
        RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE
          dropped int := 0;
          child record;
          upper_bound timestamptz;
        BEGIN
          FOR child IN
            SELECT c.oid::regclass AS name, pg_get_expr(c.relpartbound, c.oid) AS bound
            FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent = parent AND c.relispartition AND c.relpartbound IS NOT NULL
          LOOP
            IF child.bound LIKE 'DEFAULT' THEN CONTINUE; END IF;
            upper_bound := (regexp_match(child.bound, 'TO \\(''([^'']+)''\\)'))[1]::timestamptz;
            IF upper_bound < now() - make_interval(days => retention_days) THEN
              EXECUTE format('DROP TABLE %s', child.name);
              dropped := dropped + 1;
            END IF;
          END LOOP;
          RETURN dropped;
        END $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION ensure_monthly_partitions(regclass, int) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION drop_expired_partitions(regclass, int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION ensure_monthly_partitions(regclass, int) TO klick_system")
    op.execute("GRANT EXECUTE ON FUNCTION drop_expired_partitions(regclass, int) TO klick_system")

    # Partición por defecto (red de seguridad) y particiones del mes en curso y los 3 siguientes.
    op.execute("CREATE TABLE audit_events_default PARTITION OF audit_events DEFAULT")
    op.execute("REVOKE ALL ON audit_events_default FROM klick_app, klick_system")
    op.execute("SELECT ensure_monthly_partitions('audit_events', 3)")

    # Append-only: ni la aplicación ni el sistema pueden actualizar o borrar; un trigger lo refuerza.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM klick_app, klick_system")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_modification() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% es de solo anexar (append-only)', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION reject_modification()"
    )
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY audit_select ON audit_events FOR SELECT USING (org_id = {ORG})")
    op.execute(
        f"CREATE POLICY audit_insert ON audit_events FOR INSERT "
        f"WITH CHECK (org_id IS NULL OR org_id = {ORG})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_events CASCADE")
    for fn in (
        "ensure_monthly_partitions(regclass, int)",
        "drop_expired_partitions(regclass, int)",
        "reject_modification()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    op.drop_table("idempotency_keys")
    op.drop_table("outbox_events")
    op.drop_table("jobs")
