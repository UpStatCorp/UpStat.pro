"""Add prompts table

Revision ID: 004
Revises: 003
Create Date: 2024-12-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None

def upgrade():
    # Создание таблицы prompts
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
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Создание индексов
    op.create_index(op.f('ix_prompts_name'), 'prompts', ['name'], unique=False)
    op.create_index(op.f('ix_prompts_is_active'), 'prompts', ['is_active'], unique=False)
    op.create_index(op.f('ix_prompts_created_by'), 'prompts', ['created_by'], unique=False)

def downgrade():
    # Удаление индексов и таблицы
    op.drop_index(op.f('ix_prompts_created_by'), table_name='prompts')
    op.drop_index(op.f('ix_prompts_is_active'), table_name='prompts')
    op.drop_index(op.f('ix_prompts_name'), table_name='prompts')
    op.drop_table('prompts')

