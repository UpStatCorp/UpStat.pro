"""Add Zoom tables

Revision ID: 002
Revises: 001
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None

def upgrade():
    # Создание таблицы zoom_meetings
    op.create_table('zoom_meetings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('meeting_id', sa.String(length=255), nullable=False),
        sa.Column('topic', sa.String(length=255), nullable=True),
        sa.Column('start_time', sa.DateTime(), nullable=True),
        sa.Column('duration_minutes', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('join_url', sa.String(length=512), nullable=True),
        sa.Column('password', sa.String(length=20), nullable=True),
        sa.Column('ai_agent_enabled', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('meeting_id')
    )
    op.create_index(op.f('ix_zoom_meetings_user_id'), 'zoom_meetings', ['user_id'], unique=False)

    # Создание таблицы meeting_transcripts
    op.create_table('meeting_transcripts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('meeting_id', sa.Integer(), nullable=False),
        sa.Column('full_transcript', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('participants_count', sa.Integer(), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['meeting_id'], ['zoom_meetings.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_meeting_transcripts_meeting_id'), 'meeting_transcripts', ['meeting_id'], unique=False)

def downgrade():
    op.drop_index(op.f('ix_meeting_transcripts_meeting_id'), table_name='meeting_transcripts')
    op.drop_table('meeting_transcripts')
    op.drop_index(op.f('ix_zoom_meetings_user_id'), table_name='zoom_meetings')
    op.drop_table('zoom_meetings')
