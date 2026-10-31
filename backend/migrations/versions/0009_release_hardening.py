"""Add tenant RLS policies and a non-privileged runtime role.

Revision ID: 0009_release_hardening
Revises: 0008_semantic_retrieval
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pydantic import SecretStr

from app.config import Settings

revision: str = "0009_release_hardening"
down_revision: str | None = "0008_semantic_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "contacts",
    "categories",
    "tickets",
    "messages",
    "audit_events",
    "customer_access",
    "account_tokens",
    "customer_invitations",
    "notifications",
    "sla_policies",
    "sla_policy_versions",
    "ticket_slas",
    "integration_sources",
    "webhook_receipts",
    "incidents",
    "incident_tickets",
    "incident_events",
    "radar_runs",
    "radar_proposals",
    "radar_proposal_tickets",
    "radar_suppressions",
    "ticket_embeddings",
)


def _runtime_role() -> None:
    password = Settings().runtime_db_password or SecretStr("")
    password = password.get_secret_value()
    if len(password) < 16:
        raise RuntimeError("SUPPORTFLOW_RUNTIME_DB_PASSWORD must contain at least 16 characters")
    bind = op.get_bind()
    literal = bind.scalar(sa.text("SELECT quote_literal(:password)"), {"password": password})
    bind.exec_driver_sql(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supportflow_runtime') THEN "
        f"CREATE ROLE supportflow_runtime LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
        f"NOCREATEROLE NOREPLICATION PASSWORD {literal}; "
        "ELSE "
        f"ALTER ROLE supportflow_runtime WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
        f"NOCREATEROLE NOREPLICATION PASSWORD {literal}; "
        "END IF; END $$"
    )
    bind.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    bind.exec_driver_sql("GRANT USAGE ON SCHEMA public TO supportflow_runtime")
    bind.exec_driver_sql(
        "GRANT CONNECT ON DATABASE "
        + bind.dialect.identifier_preparer.quote(bind.engine.url.database)
        + " TO supportflow_runtime"
    )
    bind.exec_driver_sql("REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM supportflow_runtime")
    bind.exec_driver_sql(
        "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO supportflow_runtime"
    )
    bind.exec_driver_sql(
        "GRANT DELETE ON TABLE sessions, account_tokens, customer_invitations, "
        "webhook_receipts, notifications, incident_tickets, radar_runs, ticket_embeddings, "
        "background_jobs, outbox_events, email_deliveries TO supportflow_runtime"
    )
    bind.exec_driver_sql(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO supportflow_runtime"
    )
    bind.exec_driver_sql("REVOKE ALL ON TABLE alembic_version FROM supportflow_runtime")
    bind.exec_driver_sql("GRANT SELECT ON TABLE alembic_version TO supportflow_runtime")
    bind.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE "
        "ON TABLES TO supportflow_runtime"
    )
    bind.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT "
        "ON SEQUENCES TO supportflow_runtime"
    )
    bind.exec_driver_sql(
        "REVOKE UPDATE ON TABLE messages, audit_events, incident_events, "
        "email_deliveries FROM supportflow_runtime"
    )
    bind.exec_driver_sql(
        "GRANT UPDATE (status, processed_at, ticket_id, message_id, error_category, result) "
        "ON TABLE webhook_receipts TO supportflow_runtime"
    )
    bind.exec_driver_sql(
        "REVOKE DELETE ON TABLE messages, audit_events, incident_events FROM supportflow_runtime"
    )
    bind.exec_driver_sql(
        "REVOKE DELETE ON TABLE users, organizations, memberships, contacts, categories, "
        "tickets, customer_access, sla_policies, sla_policy_versions, ticket_slas, "
        "integration_sources, incidents, radar_proposals, radar_proposal_tickets, "
        "radar_suppressions FROM supportflow_runtime"
    )


def upgrade() -> None:
    _runtime_role()
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.supportflow_current_uuid(setting_name text)
        RETURNS uuid
        LANGUAGE plpgsql STABLE PARALLEL SAFE
        SET search_path = pg_catalog
        AS $$
        DECLARE value_text text;
        BEGIN
          value_text := current_setting(setting_name, true);
          IF value_text IS NULL OR value_text !~* (
            '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
          ) THEN
            RETURN NULL;
          END IF;
          RETURN value_text::uuid;
        EXCEPTION WHEN others THEN
          RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.supportflow_current_account_token_hash(setting_name text)
        RETURNS text
        LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog
        AS $$
          SELECT CASE
            WHEN current_setting(setting_name, true) ~ '^[0-9a-f]{64}$'
            THEN current_setting(setting_name, true)
            ELSE NULL
          END
        $$
        """
    )
    bind = op.get_bind()
    for table in TENANT_TABLES:
        bind.exec_driver_sql(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        bind.exec_driver_sql(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        bind.exec_driver_sql(
            f'CREATE POLICY supportflow_tenant_isolation ON "{table}" '
            "USING (organization_id = "
            "public.supportflow_current_uuid('supportflow.organization_id')) "
            "WITH CHECK (organization_id = "
            "public.supportflow_current_uuid('supportflow.organization_id'))"
        )

    bind.exec_driver_sql(
        "CREATE POLICY supportflow_user_self_read ON customer_access FOR SELECT "
        "USING (user_id = public.supportflow_current_uuid('supportflow.user_id'))"
    )
    bind.exec_driver_sql(
        "CREATE POLICY supportflow_user_token_access ON account_tokens FOR ALL "
        "USING (user_id = public.supportflow_current_uuid('supportflow.user_id') AND "
        "(organization_id IS NULL OR organization_id = "
        "public.supportflow_current_uuid('supportflow.organization_id'))) "
        "WITH CHECK (user_id = public.supportflow_current_uuid('supportflow.user_id') AND "
        "(organization_id IS NULL OR organization_id = "
        "public.supportflow_current_uuid('supportflow.organization_id')))"
    )
    bind.exec_driver_sql(
        "CREATE POLICY supportflow_account_token_lookup ON account_tokens FOR SELECT "
        "USING (token_hash = "
        "public.supportflow_current_account_token_hash('supportflow.account_token_hash'))"
    )
    bind.exec_driver_sql(
        "CREATE POLICY supportflow_user_self_read ON notifications FOR SELECT "
        "USING (user_id = public.supportflow_current_uuid('supportflow.user_id') AND EXISTS ("
        "SELECT 1 FROM customer_access WHERE customer_access.organization_id = "
        "notifications.organization_id AND customer_access.user_id = "
        "public.supportflow_current_uuid('supportflow.user_id') "
        "AND customer_access.is_active))"
    )
    bind.exec_driver_sql(
        "CREATE POLICY supportflow_user_self_update ON notifications FOR UPDATE "
        "USING (user_id = public.supportflow_current_uuid('supportflow.user_id') AND EXISTS ("
        "SELECT 1 FROM customer_access WHERE customer_access.organization_id = "
        "notifications.organization_id AND customer_access.user_id = "
        "public.supportflow_current_uuid('supportflow.user_id') "
        "AND customer_access.is_active)) "
        "WITH CHECK (user_id = public.supportflow_current_uuid('supportflow.user_id') AND EXISTS ("
        "SELECT 1 FROM customer_access WHERE customer_access.organization_id = "
        "notifications.organization_id AND customer_access.user_id = "
        "public.supportflow_current_uuid('supportflow.user_id') "
        "AND customer_access.is_active))"
    )
    bind.exec_driver_sql(
        "CREATE POLICY supportflow_source_lookup ON integration_sources FOR SELECT "
        "USING (id = public.supportflow_current_uuid('supportflow.integration_source_id'))"
    )
    bind.exec_driver_sql(
        "CREATE POLICY supportflow_invitation_token_lookup ON customer_invitations FOR SELECT "
        "USING (token_hash = "
        "public.supportflow_current_account_token_hash('supportflow.invitation_token_hash'))"
    )
    bind.exec_driver_sql(
        "GRANT EXECUTE ON FUNCTION public.supportflow_current_uuid(text) TO supportflow_runtime"
    )
    bind.exec_driver_sql(
        "GRANT EXECUTE ON FUNCTION public.supportflow_current_account_token_hash(text) "
        "TO supportflow_runtime"
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TENANT_TABLES):
        bind.exec_driver_sql(f'DROP POLICY IF EXISTS supportflow_tenant_isolation ON "{table}"')
        bind.exec_driver_sql(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        bind.exec_driver_sql(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    bind.exec_driver_sql("DROP POLICY IF EXISTS supportflow_user_self_read ON customer_access")
    bind.exec_driver_sql("DROP POLICY IF EXISTS supportflow_user_token_access ON account_tokens")
    bind.exec_driver_sql("DROP POLICY IF EXISTS supportflow_account_token_lookup ON account_tokens")
    bind.exec_driver_sql("DROP POLICY IF EXISTS supportflow_user_self_read ON notifications")
    bind.exec_driver_sql("DROP POLICY IF EXISTS supportflow_user_self_update ON notifications")
    bind.exec_driver_sql("DROP POLICY IF EXISTS supportflow_source_lookup ON integration_sources")
    bind.exec_driver_sql(
        "DROP POLICY IF EXISTS supportflow_invitation_token_lookup ON customer_invitations"
    )
    op.execute("DROP FUNCTION IF EXISTS public.supportflow_current_account_token_hash(text)")
    op.execute("DROP FUNCTION IF EXISTS public.supportflow_current_uuid(text)")
