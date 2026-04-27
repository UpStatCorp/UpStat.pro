"""Add seller_passports, passport_snapshots tables and stage column to trainings

Revision ID: 012
Revises: 011
Create Date: 2026-04-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '012'
down_revision = '011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('seller_passports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('score_contact', sa.Float(), nullable=False, server_default='0'),
        sa.Column('score_needs', sa.Float(), nullable=False, server_default='0'),
        sa.Column('score_presentation', sa.Float(), nullable=False, server_default='0'),
        sa.Column('score_objections', sa.Float(), nullable=False, server_default='0'),
        sa.Column('score_closing', sa.Float(), nullable=False, server_default='0'),
        sa.Column('overall_score', sa.Float(), nullable=False, server_default='0'),
        sa.Column('total_calls_analyzed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_trainings_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('first_call_at', sa.DateTime(), nullable=True),
        sa.Column('last_updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index('ix_seller_passports_id', 'seller_passports', ['id'])
    op.create_index('ix_seller_passports_user_id', 'seller_passports', ['user_id'])

    op.create_table('passport_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('passport_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('score_contact', sa.Float(), nullable=False, server_default='0'),
        sa.Column('score_needs', sa.Float(), nullable=False, server_default='0'),
        sa.Column('score_presentation', sa.Float(), nullable=False, server_default='0'),
        sa.Column('score_objections', sa.Float(), nullable=False, server_default='0'),
        sa.Column('score_closing', sa.Float(), nullable=False, server_default='0'),
        sa.Column('overall_score', sa.Float(), nullable=False, server_default='0'),
        sa.Column('training_id_before', sa.Integer(), nullable=True),
        sa.Column('training_stage', sa.String(50), nullable=True),
        sa.Column('training_applied', sa.String(20), nullable=True),
        sa.Column('training_delta', sa.Float(), nullable=True),
        sa.Column('gpt_comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['passport_id'], ['seller_passports.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.ForeignKeyConstraint(['training_id_before'], ['trainings.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_passport_snapshots_id', 'passport_snapshots', ['id'])
    op.create_index('ix_passport_snapshots_passport_id', 'passport_snapshots', ['passport_id'])
    op.create_index('ix_passport_snapshots_user_id', 'passport_snapshots', ['user_id'])
    op.create_index('ix_passport_snapshots_conversation_id', 'passport_snapshots', ['conversation_id'])

    op.add_column('trainings', sa.Column('stage', sa.String(50), nullable=True))


def downgrade():
    op.drop_column('trainings', 'stage')
    op.drop_table('passport_snapshots')
    op.drop_table('seller_passports')
