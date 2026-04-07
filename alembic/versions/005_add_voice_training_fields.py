"""add_voice_training_fields

Revision ID: 005_add_voice_training_fields
Revises: 004_add_crm_integration
Create Date: 2025-01-13 00:02:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = '005_add_voice_training_fields'
down_revision = '004_add_crm_integration'
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    return name in sa_inspect(conn).get_table_names()


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

    if not _column_exists(conn, 'training_sessions', 'session_type'):
        op.add_column('training_sessions', sa.Column('session_type', sa.String(length=50), nullable=True, server_default='text'))
    if not _column_exists(conn, 'training_sessions', 'websocket_session_id'):
        op.add_column('training_sessions', sa.Column('websocket_session_id', sa.String(length=255), nullable=True))
    if not _column_exists(conn, 'training_sessions', 'conversation_history_json'):
        op.add_column('training_sessions', sa.Column('conversation_history_json', sa.Text(), nullable=True))
    if not _column_exists(conn, 'training_sessions', 'status'):
        op.add_column('training_sessions', sa.Column('status', sa.String(length=20), nullable=True, server_default='active'))

    if not _index_exists(conn, 'ix_training_sessions_websocket_session_id', 'training_sessions'):
        op.create_index('ix_training_sessions_websocket_session_id', 'training_sessions', ['websocket_session_id'], unique=False)

    if not _table_exists(conn, 'voice_training_messages'):
        op.create_table(
            'voice_training_messages',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('session_id', sa.Integer(), nullable=False),
            sa.Column('role', sa.String(length=20), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('audio_path', sa.String(length=512), nullable=True),
            sa.Column('timestamp', sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['session_id'], ['training_sessions.id']),
            sa.PrimaryKeyConstraint('id'),
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
