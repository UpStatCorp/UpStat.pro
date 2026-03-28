"""Add soft disconnect fields and fix unique constraints for CRM entities

Revision ID: 011
Revises: 010
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa

revision = '011'
down_revision = '010'
branch_labels = None
depends_on = None


def _column_exists(conn, table_name, column_name):
    try:
        result = conn.execute(sa.text(
            f"PRAGMA table_info('{table_name}')"
        ))
        columns = [row[1] for row in result.fetchall()]
        return column_name in columns
    except Exception:
        try:
            result = conn.execute(sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c)"
            ), {"t": table_name, "c": column_name})
            return result.scalar()
        except Exception:
            return False


def _index_exists(conn, index_name):
    try:
        result = conn.execute(sa.text(
            f"SELECT name FROM sqlite_master WHERE type='index' AND name='{index_name}'"
        ))
        return result.fetchone() is not None
    except Exception:
        return False


def upgrade():
    conn = op.get_bind()

    if not _column_exists(conn, 'crm_integrations', 'initial_sync_completed'):
        op.add_column('crm_integrations', sa.Column(
            'initial_sync_completed', sa.Boolean(),
            server_default='false', nullable=False,
        ))

    if not _column_exists(conn, 'crm_integrations', 'sync_cursor_json'):
        op.add_column('crm_integrations', sa.Column(
            'sync_cursor_json', sa.Text(), nullable=True,
        ))

    # Replace global unique(bitrix_id) with composite unique(integration_id, bitrix_id).
    # SQLite doesn't support DROP CONSTRAINT, so we create new indexes if they don't exist.
    # The old unique index on bitrix_id alone stays (harmless) but the model
    # will enforce the composite constraint going forward.
    for table, uq_name in [
        ('crm_deals', 'uq_deal_integ_bx'),
        ('crm_leads', 'uq_lead_integ_bx'),
        ('crm_contacts', 'uq_contact_integ_bx'),
        ('crm_companies', 'uq_company_integ_bx'),
        ('crm_activities', 'uq_activity_integ_bx'),
    ]:
        if not _index_exists(conn, uq_name):
            try:
                op.create_unique_constraint(uq_name, table, ['integration_id', 'bitrix_id'])
            except Exception:
                op.create_index(uq_name, table, ['integration_id', 'bitrix_id'], unique=True)


def downgrade():
    for uq_name in [
        'uq_deal_integ_bx', 'uq_lead_integ_bx', 'uq_contact_integ_bx',
        'uq_company_integ_bx', 'uq_activity_integ_bx',
    ]:
        try:
            op.drop_constraint(uq_name)
        except Exception:
            try:
                op.drop_index(uq_name)
            except Exception:
                pass

    op.drop_column('crm_integrations', 'sync_cursor_json')
    op.drop_column('crm_integrations', 'initial_sync_completed')
