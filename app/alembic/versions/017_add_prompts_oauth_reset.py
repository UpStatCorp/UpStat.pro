"""Add prompts table, Google OAuth fields, password reset tokens

Revision ID: 017
Revises: 016
Create Date: 2026-04-30 00:00:00.000000

Объединяет локальные миграции 004_add_prompts_table,
005_add_google_oauth_fields, 007_add_password_reset_tokens
которые на сервере были пропущены (таблицы уже могут существовать).
Все операции идемпотентны.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = '017'
down_revision = '016'
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

    # --- prompts table ---
    if not _table_exists(conn, 'prompts'):
        op.create_table('prompts',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('created_by', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['created_by'], ['users.id']),
            sa.PrimaryKeyConstraint('id')
        )
        if not _index_exists(conn, 'ix_prompts_name', 'prompts'):
            op.create_index(op.f('ix_prompts_name'), 'prompts', ['name'], unique=False)
        if not _index_exists(conn, 'ix_prompts_is_active', 'prompts'):
            op.create_index(op.f('ix_prompts_is_active'), 'prompts', ['is_active'], unique=False)
        if not _index_exists(conn, 'ix_prompts_created_by', 'prompts'):
            op.create_index(op.f('ix_prompts_created_by'), 'prompts', ['created_by'], unique=False)

    # --- Google OAuth fields ---
    if not _column_exists(conn, 'users', 'google_id'):
        op.add_column('users', sa.Column('google_id', sa.String(length=255), nullable=True))
    if not _column_exists(conn, 'users', 'google_access_token'):
        op.add_column('users', sa.Column('google_access_token', sa.Text(), nullable=True))
    if not _column_exists(conn, 'users', 'google_refresh_token'):
        op.add_column('users', sa.Column('google_refresh_token', sa.Text(), nullable=True))
    if not _column_exists(conn, 'users', 'google_token_expires_at'):
        op.add_column('users', sa.Column('google_token_expires_at', sa.DateTime(), nullable=True))
    if not _column_exists(conn, 'users', 'is_google_user'):
        op.add_column('users', sa.Column('is_google_user', sa.Boolean(), nullable=True, server_default='false'))

    # --- password_reset_tokens ---
    if not _table_exists(conn, 'password_reset_tokens'):
        op.create_table('password_reset_tokens',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('token', sa.String(length=128), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('used', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('used_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id')
        )
        if not _index_exists(conn, 'ix_password_reset_tokens_id', 'password_reset_tokens'):
            op.create_index(op.f('ix_password_reset_tokens_id'), 'password_reset_tokens', ['id'], unique=False)
        if not _index_exists(conn, 'ix_password_reset_tokens_user_id', 'password_reset_tokens'):
            op.create_index(op.f('ix_password_reset_tokens_user_id'), 'password_reset_tokens', ['user_id'], unique=False)
        if not _index_exists(conn, 'ix_password_reset_tokens_token', 'password_reset_tokens'):
            op.create_index(op.f('ix_password_reset_tokens_token'), 'password_reset_tokens', ['token'], unique=True)
        if not _index_exists(conn, 'ix_password_reset_tokens_expires_at', 'password_reset_tokens'):
            op.create_index(op.f('ix_password_reset_tokens_expires_at'), 'password_reset_tokens', ['expires_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_password_reset_tokens_expires_at'), table_name='password_reset_tokens')
    op.drop_index(op.f('ix_password_reset_tokens_token'), table_name='password_reset_tokens')
    op.drop_index(op.f('ix_password_reset_tokens_user_id'), table_name='password_reset_tokens')
    op.drop_index(op.f('ix_password_reset_tokens_id'), table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
    op.drop_column('users', 'is_google_user')
    op.drop_column('users', 'google_token_expires_at')
    op.drop_column('users', 'google_refresh_token')
    op.drop_column('users', 'google_access_token')
    op.drop_column('users', 'google_id')
    op.drop_index(op.f('ix_prompts_created_by'), table_name='prompts')
    op.drop_index(op.f('ix_prompts_is_active'), table_name='prompts')
    op.drop_index(op.f('ix_prompts_name'), table_name='prompts')
    op.drop_table('prompts')
