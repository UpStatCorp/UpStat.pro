"""Add chat support to CRM recordings

Revision ID: 009_add_chat_support
Revises: 008_add_crm_batch_and_scores
Create Date: 2025-03-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = '009_add_chat_support'
down_revision = '008_add_crm_batch_and_scores'
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    try:
        cols = sa_inspect(conn).get_columns(table)
        return any(c.get("name") == column for c in cols)
    except Exception:
        return False


def upgrade():
    conn = op.get_bind()

    if not _column_exists(conn, 'crm_recordings', 'record_type'):
        op.add_column('crm_recordings', sa.Column('record_type', sa.String(20), server_default='call', nullable=False))
    if not _column_exists(conn, 'crm_recordings', 'chat_text'):
        op.add_column('crm_recordings', sa.Column('chat_text', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('crm_recordings', 'chat_text')
    op.drop_column('crm_recordings', 'record_type')
