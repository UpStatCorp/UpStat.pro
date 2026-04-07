"""Add batch_id, analysis_score, training_plan_id to CRM recordings and webhook_secret to integrations

Revision ID: 008_add_crm_batch_and_scores
Revises: 007_add_voice_training_fields
Create Date: 2025-03-08
"""

from alembic import op
import sqlalchemy as sa

revision = '008_add_crm_batch_and_scores'
down_revision = '007_add_voice_training_fields'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('crm_integrations', sa.Column('webhook_secret', sa.String(64), nullable=True))

    op.add_column('crm_recordings', sa.Column('batch_id', sa.String(20), nullable=True))
    op.add_column('crm_recordings', sa.Column('analysis_score', sa.Integer(), nullable=True))
    op.add_column('crm_recordings', sa.Column('training_plan_id', sa.Integer(), sa.ForeignKey('analysis_training_plans.id'), nullable=True))
    op.create_index('ix_crm_recordings_batch_id', 'crm_recordings', ['batch_id'])


def downgrade():
    op.drop_index('ix_crm_recordings_batch_id', table_name='crm_recordings')
    op.drop_column('crm_recordings', 'training_plan_id')
    op.drop_column('crm_recordings', 'analysis_score')
    op.drop_column('crm_recordings', 'batch_id')
    op.drop_column('crm_integrations', 'webhook_secret')
