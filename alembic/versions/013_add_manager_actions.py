"""Add manager_actions and action_patterns tables

Revision ID: 013
Revises: 012
Create Date: 2026-04-13 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('manager_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('stage', sa.String(50), nullable=False),
        sa.Column('action_text', sa.String(500), nullable=False),
        sa.Column('action_type', sa.String(50), server_default='phrase'),
        sa.Column('outcome', sa.String(20), nullable=False),
        sa.Column('client_reaction', sa.String(300), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0.8'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_manager_actions_id', 'manager_actions', ['id'])
    op.create_index('ix_manager_actions_team_id', 'manager_actions', ['team_id'])
    op.create_index('ix_manager_actions_user_id', 'manager_actions', ['user_id'])
    op.create_index('ix_manager_actions_conversation_id', 'manager_actions', ['conversation_id'])

    op.create_table('action_patterns',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('stage', sa.String(50), nullable=False),
        sa.Column('pattern_text', sa.String(500), nullable=False),
        sa.Column('outcome', sa.String(20), nullable=False),
        sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_calls', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('percentage', sa.Float(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='collecting'),
        sa.Column('reported_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_action_patterns_id', 'action_patterns', ['id'])
    op.create_index('ix_action_patterns_team_id', 'action_patterns', ['team_id'])


def downgrade():
    op.drop_table('action_patterns')
    op.drop_table('manager_actions')
