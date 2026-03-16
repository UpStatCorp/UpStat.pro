"""Add WebRTC meeting tables

Revision ID: 006
Revises: 005_add_google_oauth_fields
Create Date: 2024-01-15 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '006'
down_revision = '005_add_google_oauth_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Создание таблицы custom_meetings
    op.create_table('custom_meetings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('meeting_id', sa.String(length=255), nullable=True),
        sa.Column('topic', sa.String(length=255), nullable=False),
        sa.Column('creator_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('max_participants', sa.Integer(), nullable=True),
        sa.Column('duration_minutes', sa.Integer(), nullable=True),
        sa.Column('password', sa.String(length=50), nullable=True),
        sa.Column('ai_agent_enabled', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_custom_meetings_id'), 'custom_meetings', ['id'], unique=False)
    op.create_index(op.f('ix_custom_meetings_meeting_id'), 'custom_meetings', ['meeting_id'], unique=True)
    
    # Создание таблицы meeting_participants
    op.create_table('meeting_participants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('meeting_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('joined_at', sa.DateTime(), nullable=True),
        sa.Column('left_at', sa.DateTime(), nullable=True),
        sa.Column('role', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['meeting_id'], ['custom_meetings.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_meeting_participants_id'), 'meeting_participants', ['id'], unique=False)
    
    # Создание таблицы custom_meeting_transcripts
    op.create_table('custom_meeting_transcripts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('meeting_id', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['meeting_id'], ['custom_meetings.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_custom_meeting_transcripts_id'), 'custom_meeting_transcripts', ['id'], unique=False)


def downgrade():
    # Удаление таблиц в обратном порядке
    op.drop_index(op.f('ix_custom_meeting_transcripts_id'), table_name='custom_meeting_transcripts')
    op.drop_table('custom_meeting_transcripts')
    
    op.drop_index(op.f('ix_meeting_participants_id'), table_name='meeting_participants')
    op.drop_table('meeting_participants')
    
    op.drop_index(op.f('ix_custom_meetings_meeting_id'), table_name='custom_meetings')
    op.drop_index(op.f('ix_custom_meetings_id'), table_name='custom_meetings')
    op.drop_table('custom_meetings')









