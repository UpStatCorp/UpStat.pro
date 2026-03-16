"""Add user roles

Revision ID: 003
Revises: 002
Create Date: 2024-12-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None

def upgrade():
    # Добавляем поле role в таблицу users
    op.add_column('users', sa.Column('role', sa.String(length=10), nullable=False, server_default='user'))
    
    # Создаем индекс для поля role
    op.create_index(op.f('ix_users_role'), 'users', ['role'], unique=False)

def downgrade():
    # Удаляем индекс и поле role
    op.drop_index(op.f('ix_users_role'), table_name='users')
    op.drop_column('users', 'role')

