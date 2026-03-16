"""add_voice_training_fields

Revision ID: 005_add_voice_training_fields
Revises: 004_add_crm_integration
Create Date: 2025-01-13 00:02:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '005_add_voice_training_fields'
down_revision = '004_add_crm_integration'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('training_sessions', sa.Column('session_type', sa.String(length=50), nullable=True, server_default='text'))
    op.add_column('training_sessions', sa.Column('websocket_session_id', sa.String(length=255), nullable=True))
    op.add_column('training_sessions', sa.Column('conversation_history_json', sa.Text(), nullable=True))
    op.add_column('training_sessions', sa.Column('status', sa.String(length=20), nullable=True, server_default='active'))
    op.create_index('ix_training_sessions_websocket_session_id', 'training_sessions', ['websocket_session_id'], unique=False)

    op.create_table(
        'voice_training_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('audio_path', sa.String(length=512), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['training_sessions.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_voice_training_messages_session_id', 'voice_training_messages', ['session_id'], unique=False)
    op.create_index('ix_voice_training_messages_timestamp', 'voice_training_messages', ['timestamp'], unique=False)


def downgrade():
    op.drop_table('voice_training_messages')
    op.drop_index('ix_training_sessions_websocket_session_id', table_name='training_sessions')
    op.drop_column('training_sessions', 'status')
    op.drop_column('training_sessions', 'conversation_history_json')
    op.drop_column('training_sessions', 'websocket_session_id')
    op.drop_column('training_sessions', 'session_type')
