"""Add batch_id, analysis_score, training_plan_id to CRM recordings and webhook_secret to integrations

Revision ID: 008_add_crm_batch_and_scores
Revises: 007_add_voice_training_fields
Create Date: 2025-03-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = '008_add_crm_batch_and_scores'
down_revision = '007_add_voice_training_fields'
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    try:
        cols = sa_inspect(conn).get_columns(table)
        return any(c.get("name") == column for c in cols)
    except Exception:
        return False


def _index_exists(conn, name: str, table: str) -> bool:
    try:
        for idx in sa_inspect(conn).get_indexes(table):
            if idx.get("name") == name:
                return True
    except Exception:
        pass
    return False


def upgrade():
    conn = op.get_bind()

    if not _column_exists(conn, 'crm_integrations', 'webhook_secret'):
        op.add_column('crm_integrations', sa.Column('webhook_secret', sa.String(64), nullable=True))

    if not _column_exists(conn, 'crm_recordings', 'batch_id'):
        op.add_column('crm_recordings', sa.Column('batch_id', sa.String(20), nullable=True))
    if not _column_exists(conn, 'crm_recordings', 'analysis_score'):
        op.add_column('crm_recordings', sa.Column('analysis_score', sa.Integer(), nullable=True))
    if not _column_exists(conn, 'crm_recordings', 'training_plan_id'):
        op.add_column('crm_recordings', sa.Column('training_plan_id', sa.Integer(), sa.ForeignKey('analysis_training_plans.id'), nullable=True))
    if not _index_exists(conn, 'ix_crm_recordings_batch_id', 'crm_recordings'):
        op.create_index('ix_crm_recordings_batch_id', 'crm_recordings', ['batch_id'])


def downgrade():
    op.drop_index('ix_crm_recordings_batch_id', table_name='crm_recordings')
    op.drop_column('crm_recordings', 'training_plan_id')
    op.drop_column('crm_recordings', 'analysis_score')
    op.drop_column('crm_recordings', 'batch_id')
    op.drop_column('crm_integrations', 'webhook_secret')
