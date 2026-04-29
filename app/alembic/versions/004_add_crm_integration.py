"""Add CRM integration tables

Revision ID: 004_add_crm_integration
Revises: 003
Create Date: 2024-12-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = '004_add_crm_integration'
down_revision = '003'
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    return name in sa_inspect(conn).get_table_names()


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

    if not _table_exists(conn, 'crm_integrations'):
        op.create_table('crm_integrations',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('crm_type', sa.String(50), nullable=False),
            sa.Column('crm_name', sa.String(255), nullable=False),
            sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
            sa.Column('access_token', sa.Text(), nullable=True),
            sa.Column('refresh_token', sa.Text(), nullable=True),
            sa.Column('token_expires_at', sa.DateTime(), nullable=True),
            sa.Column('crm_domain', sa.String(255), nullable=True),
            sa.Column('client_id', sa.String(255), nullable=True),
            sa.Column('client_secret', sa.Text(), nullable=True),
            sa.Column('webhook_url', sa.String(512), nullable=True),
            sa.Column('last_sync_at', sa.DateTime(), nullable=True),
            sa.Column('recordings_count', sa.Integer(), default=0),
            sa.Column('analyzed_count', sa.Integer(), default=0),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_crm_integrations_user_id', 'crm_integrations', ['user_id'])

    if not _table_exists(conn, 'crm_recordings'):
        op.create_table('crm_recordings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('integration_id', sa.Integer(), sa.ForeignKey('crm_integrations.id'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('crm_record_id', sa.String(255), nullable=False),
            sa.Column('crm_call_id', sa.String(255), nullable=True),
            sa.Column('call_date', sa.DateTime(), nullable=False),
            sa.Column('duration_seconds', sa.Integer(), default=0),
            sa.Column('direction', sa.String(20), default='unknown'),
            sa.Column('manager_name', sa.String(255), nullable=True),
            sa.Column('manager_phone', sa.String(50), nullable=True),
            sa.Column('client_name', sa.String(255), nullable=True),
            sa.Column('client_phone', sa.String(50), nullable=True),
            sa.Column('client_company', sa.String(255), nullable=True),
            sa.Column('recording_url', sa.Text(), nullable=True),
            sa.Column('local_file_path', sa.String(512), nullable=True),
            sa.Column('file_size_bytes', sa.Integer(), nullable=True),
            sa.Column('sync_status', sa.String(20), default='available'),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id'), nullable=True),
            sa.Column('crm_metadata_json', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('downloaded_at', sa.DateTime(), nullable=True),
            sa.Column('analyzed_at', sa.DateTime(), nullable=True),
        )
        if not _index_exists(conn, 'ix_crm_recordings_integration_id', 'crm_recordings'):
            op.create_index('ix_crm_recordings_integration_id', 'crm_recordings', ['integration_id'])
        if not _index_exists(conn, 'ix_crm_recordings_user_id', 'crm_recordings'):
            op.create_index('ix_crm_recordings_user_id', 'crm_recordings', ['user_id'])
        if not _index_exists(conn, 'ix_crm_recordings_conversation_id', 'crm_recordings'):
            op.create_index('ix_crm_recordings_conversation_id', 'crm_recordings', ['conversation_id'])
        if not _index_exists(conn, 'ix_crm_recordings_sync_status', 'crm_recordings'):
            op.create_index('ix_crm_recordings_sync_status', 'crm_recordings', ['sync_status'])


def downgrade():
    op.drop_table('crm_recordings')
    op.drop_table('crm_integrations')
