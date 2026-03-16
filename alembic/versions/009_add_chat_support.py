"""Add chat support to CRM recordings

Revision ID: 009_add_chat_support
Revises: 008_add_crm_batch_and_scores
Create Date: 2025-03-08
"""
from alembic import op
import sqlalchemy as sa

revision = '009_add_chat_support'
down_revision = '008_add_crm_batch_and_scores'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('crm_recordings', sa.Column('record_type', sa.String(20), server_default='call', nullable=False))
    op.add_column('crm_recordings', sa.Column('chat_text', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('crm_recordings', 'chat_text')
    op.drop_column('crm_recordings', 'record_type')
