"""Baseline: базовая схема, ранее создававшаяся Base.metadata.create_all

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

До этой правки 001 была пустой заглушкой ("Initial schema (created by
create_all)"), и базовая схема не существовала в виде миграции вообще.
Следствие: `alembic upgrade head` на чистой базе падал на 003
(ALTER TABLE users при отсутствующей users), а развернуть проект с нуля
без Base.metadata.create_all было невозможно.

Здесь создаются 9 таблиц, которые ни одна миграция 002-021 не создаёт,
а только ALTER-ит. Состав определён механически:

    baseline = определение в app/models.py МИНУС всё, что добавляют 002-021
    * колонка исключается, если её добавляет любая миграция 002-021;
    * индекс исключается, если хотя бы одна его колонка исключена.

Второе правило не косметическое: 003 добавляет не только users.role,
но и индекс ix_users_role по этой колонке, причём без guard.

Исключено из baseline (добавляется последующими миграциями):
    users              role(003), google_id/is_premium/free_analyses_limit/
                       analyses_used/premium_granted_by/premium_granted_at(018),
                       organization_id/product_mode(018)
    teams              organization_id(018)
    trainings          stage(012)
    training_sessions  session_type/websocket_session_id/
                       conversation_history_json/status(005)
    analysis_training_plans   plan_source(018)
    индексы            ix_users_google_id, ix_users_organization_id,
                       ix_teams_organization_id, ix_training_sessions_status,
                       ix_training_sessions_user_id_status,
                       ix_training_sessions_websocket_session_id

Файл СГЕНЕРИРОВАН, не написан руками: tools/gen_baseline.py.
Типы рендерит сам alembic из Base.metadata — иначе ловушки вроде
`users.updated_at = mapped_column(String)` (String БЕЗ длины) воспроизводятся
неточно и ломают критерий приёмки (compare_type в alembic 1.12.1 = True).
Правки вносить в генератор и перегенерировать, а не редактировать здесь.

На проде эта ревизия НЕ выполнится: база стоит на 021, alembic применяет
только ревизии выше текущей.
"""
from alembic import op
import sqlalchemy as sa

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('avatar', sa.String(length=512), nullable=True),
    sa.Column('is_oauth_user', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.String(), nullable=True),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_table('conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    op.create_table('messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_conversation_id'), 'messages', ['conversation_id'], unique=False)
    op.create_table('attachments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('mime_type', sa.String(length=255), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_attachments_message_id'), 'attachments', ['message_id'], unique=False)
    op.create_table('teams',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('manager_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['manager_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_teams_id'), 'teams', ['id'], unique=False)
    op.create_index(op.f('ix_teams_manager_id'), 'teams', ['manager_id'], unique=False)
    op.create_table('team_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role_in_team', sa.String(length=50), nullable=False),
    sa.Column('joined_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('team_id', 'user_id', name='uq_team_member')
    )
    op.create_index(op.f('ix_team_members_id'), 'team_members', ['id'], unique=False)
    op.create_index(op.f('ix_team_members_team_id'), 'team_members', ['team_id'], unique=False)
    op.create_index(op.f('ix_team_members_user_id'), 'team_members', ['user_id'], unique=False)
    op.create_table('analysis_training_plans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('report_message_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('recommendations_json', sa.Text(), nullable=False),
    sa.Column('total_trainings', sa.Integer(), nullable=False),
    sa.Column('completed_trainings', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['report_message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_analysis_training_plans_id'), 'analysis_training_plans', ['id'], unique=False)
    op.create_index(op.f('ix_analysis_training_plans_report_message_id'), 'analysis_training_plans', ['report_message_id'], unique=False)
    op.create_index(op.f('ix_analysis_training_plans_user_id'), 'analysis_training_plans', ['user_id'], unique=False)
    op.create_table('trainings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('recommendation', sa.Text(), nullable=False),
    sa.Column('scenario_type', sa.String(length=50), nullable=False),
    sa.Column('checklist_json', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('best_score', sa.Integer(), nullable=True),
    sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['analysis_training_plans.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_trainings_id'), 'trainings', ['id'], unique=False)
    op.create_index(op.f('ix_trainings_plan_id'), 'trainings', ['plan_id'], unique=False)
    op.create_table('training_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('training_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('transcript', sa.Text(), nullable=True),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('feedback', sa.Text(), nullable=True),
    sa.Column('checklist_results_json', sa.Text(), nullable=True),
    sa.Column('user_responses_count', sa.Integer(), nullable=False),
    sa.Column('ai_questions_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['training_id'], ['trainings.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_training_sessions_id'), 'training_sessions', ['id'], unique=False)
    op.create_index(op.f('ix_training_sessions_training_id'), 'training_sessions', ['training_id'], unique=False)
    op.create_index(op.f('ix_training_sessions_user_id'), 'training_sessions', ['user_id'], unique=False)


def downgrade():
    # Обратный порядок: сначала зависимые таблицы.
    op.drop_table('training_sessions')
    op.drop_table('trainings')
    op.drop_table('analysis_training_plans')
    op.drop_table('team_members')
    op.drop_table('teams')
    op.drop_table('attachments')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('users')
