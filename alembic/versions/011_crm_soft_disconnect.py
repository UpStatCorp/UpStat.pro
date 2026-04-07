"""Add soft disconnect fields and fix unique constraints for CRM entities

Revision ID: 011
Revises: 010_add_crm_entities
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '011'
down_revision = '010_add_crm_entities'
branch_labels = None
depends_on = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Только через Inspector — без PRAGMA/sqlite_master/raw SQL (иначе в PG ломается транзакция)."""
    try:
        insp = inspect(conn)
        cols = insp.get_columns(table_name)
        return any(c.get("name") == column_name for c in cols)
    except Exception:
        return False


def _constraint_or_unique_index_exists(conn, table_name: str, name: str) -> bool:
    try:
        insp = inspect(conn)
        for uq in insp.get_unique_constraints(table_name):
            if uq.get("name") == name:
                return True
        for idx in insp.get_indexes(table_name):
            if idx.get("name") == name:
                return True
    except Exception:
        pass
    return False


def upgrade():
    conn = op.get_bind()

    if not _column_exists(conn, "crm_integrations", "initial_sync_completed"):
        op.add_column(
            "crm_integrations",
            sa.Column(
                "initial_sync_completed",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        )

    if not _column_exists(conn, "crm_integrations", "sync_cursor_json"):
        op.add_column(
            "crm_integrations",
            sa.Column("sync_cursor_json", sa.Text(), nullable=True),
        )

    for table, uq_name in [
        ("crm_deals", "uq_deal_integ_bx"),
        ("crm_leads", "uq_lead_integ_bx"),
        ("crm_contacts", "uq_contact_integ_bx"),
        ("crm_companies", "uq_company_integ_bx"),
        ("crm_activities", "uq_activity_integ_bx"),
    ]:
        if _constraint_or_unique_index_exists(conn, table, uq_name):
            continue
        try:
            op.create_unique_constraint(uq_name, table, ["integration_id", "bitrix_id"])
        except Exception:
            try:
                op.create_index(
                    uq_name, table, ["integration_id", "bitrix_id"], unique=True
                )
            except Exception:
                pass


def downgrade():
    for table, uq_name in [
        ("crm_deals", "uq_deal_integ_bx"),
        ("crm_leads", "uq_lead_integ_bx"),
        ("crm_contacts", "uq_contact_integ_bx"),
        ("crm_companies", "uq_company_integ_bx"),
        ("crm_activities", "uq_activity_integ_bx"),
    ]:
        try:
            op.drop_constraint(uq_name, table, type_="unique")
        except Exception:
            try:
                op.drop_index(uq_name, table_name=table)
            except Exception:
                pass

    op.drop_column("crm_integrations", "sync_cursor_json")
    op.drop_column("crm_integrations", "initial_sync_completed")
