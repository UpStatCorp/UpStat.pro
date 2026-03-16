"""Add Google OAuth fields to users table

Revision ID: 005
Revises: 004_add_prompts_table
Create Date: 2024-01-15 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade():
    # Добавляем поля для Google OAuth
    op.add_column('users', sa.Column('google_id', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('is_oauth_user', sa.Boolean(), nullable=False, server_default='false'))
    
    # Делаем password_hash nullable для OAuth пользователей
    op.alter_column('users', 'password_hash', nullable=True)
    
    # Создаем индексы
    op.create_index('ix_users_google_id', 'users', ['google_id'], unique=True)


def downgrade():
    # Удаляем индексы
    op.drop_index('ix_users_google_id', table_name='users')
    
    # Удаляем поля
    op.drop_column('users', 'is_oauth_user')
    op.drop_column('users', 'google_id')
    
    # Возвращаем password_hash как NOT NULL
    op.alter_column('users', 'password_hash', nullable=False)
