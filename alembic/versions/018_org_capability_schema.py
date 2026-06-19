"""Org/capability schema + premium user fields

Revision ID: 018
Revises: 017
Create Date: 2026-06-17 00:00:00.000000

Переносит DDL из runtime-функций create_org_capability_schema() и
update_premium_schema() в Alembic, устраняя гонку при старте нескольких
воркеров. Все операции идемпотентны.

После применения этой миграции вызовы create_org_capability_schema() и
update_premium_schema() удалены из create_app(); Base.metadata.create_all()
оставлен только как страховка для таблиц из первоначальной схемы (001 empty).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = '018'
down_revision = '017'
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    return name in sa_inspect(conn).get_table_names()


def _column_exists(conn, table: str, column: str) -> bool:
    try:
        return any(c.get("name") == column for c in sa_inspect(conn).get_columns(table))
    except Exception:
        return False


def _index_exists(conn, name: str, table: str) -> bool:
    try:
        return any(i.get("name") == name for i in sa_inspect(conn).get_indexes(table))
    except Exception:
        return False


def upgrade():
    conn = op.get_bind()

    # ── 1. users — поля подписки/лимитов (из update_premium_schema) ──────────
    if not _column_exists(conn, 'users', 'is_premium'):
        op.add_column('users', sa.Column('is_premium', sa.Boolean(), nullable=False, server_default='false'))

    if not _column_exists(conn, 'users', 'free_analyses_limit'):
        op.add_column('users', sa.Column('free_analyses_limit', sa.Integer(), nullable=False, server_default='5'))

    if not _column_exists(conn, 'users', 'analyses_used'):
        op.add_column('users', sa.Column('analyses_used', sa.Integer(), nullable=False, server_default='0'))

    if not _column_exists(conn, 'users', 'premium_granted_by'):
        op.add_column('users', sa.Column('premium_granted_by', sa.Integer(), nullable=True))

    if not _column_exists(conn, 'users', 'premium_granted_at'):
        op.add_column('users', sa.Column('premium_granted_at', sa.DateTime(), nullable=True))

    # ── 2. organizations ──────────────────────────────────────────────────────
    if not _table_exists(conn, 'organizations'):
        op.create_table(
            'organizations',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('name', sa.String(255), nullable=False),
            sa.Column('sku', sa.String(50), nullable=False, server_default='FULL'),
            sa.Column('market', sa.String(20), nullable=True),
            sa.Column('data_residency', sa.String(20), nullable=True),
            sa.Column('capabilities_override', sa.Text(), nullable=True),
            sa.Column('assigned_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('assigned_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        )

    # ── 3. teams.organization_id ──────────────────────────────────────────────
    if not _column_exists(conn, 'teams', 'organization_id'):
        op.add_column('teams', sa.Column('organization_id', sa.Integer(),
                                         sa.ForeignKey('organizations.id'), nullable=True))
        if not _index_exists(conn, 'ix_teams_organization_id', 'teams'):
            op.create_index('ix_teams_organization_id', 'teams', ['organization_id'])

    # ── 4. users.organization_id + users.product_mode ─────────────────────────
    if not _column_exists(conn, 'users', 'organization_id'):
        op.add_column('users', sa.Column('organization_id', sa.Integer(),
                                          sa.ForeignKey('organizations.id'), nullable=True))
        if not _index_exists(conn, 'ix_users_organization_id', 'users'):
            op.create_index('ix_users_organization_id', 'users', ['organization_id'])

    if not _column_exists(conn, 'users', 'product_mode'):
        op.add_column('users', sa.Column('product_mode', sa.String(20), nullable=True))

    # ── 5. analysis_training_plans.plan_source ────────────────────────────────
    if _table_exists(conn, 'analysis_training_plans') and \
            not _column_exists(conn, 'analysis_training_plans', 'plan_source'):
        op.add_column('analysis_training_plans',
                      sa.Column('plan_source', sa.String(20), nullable=True, server_default='analysis'))

    # ── 6. training_programs ──────────────────────────────────────────────────
    if not _table_exists(conn, 'training_programs'):
        op.create_table(
            'training_programs',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('name', sa.String(255), nullable=False, server_default='Программа тренировок'),
            sa.Column('start_date', sa.DateTime(), nullable=True),
            sa.Column('cycle_days', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        )
        if not _index_exists(conn, 'ix_training_programs_id', 'training_programs'):
            op.create_index('ix_training_programs_id', 'training_programs', ['id'])
        if not _index_exists(conn, 'ix_training_programs_team_id', 'training_programs'):
            op.create_index('ix_training_programs_team_id', 'training_programs', ['team_id'])

    # ── 7. training_program_days ──────────────────────────────────────────────
    if not _table_exists(conn, 'training_program_days'):
        op.create_table(
            'training_program_days',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('program_id', sa.Integer(), sa.ForeignKey('training_programs.id'), nullable=False),
            sa.Column('day_index', sa.Integer(), nullable=False),
            sa.Column('stage_key', sa.String(50), nullable=True),
            sa.Column('training_id', sa.Integer(), sa.ForeignKey('trainings.id'), nullable=True),
        )
        if not _index_exists(conn, 'ix_training_program_days_id', 'training_program_days'):
            op.create_index('ix_training_program_days_id', 'training_program_days', ['id'])
        if not _index_exists(conn, 'ix_training_program_days_program_id', 'training_program_days'):
            op.create_index('ix_training_program_days_program_id', 'training_program_days', ['program_id'])

    # ── 8. notifications (не было в миграциях 001-017) ────────────────────────
    if not _table_exists(conn, 'notifications'):
        op.create_table(
            'notifications',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('type', sa.String(50), nullable=False),
            sa.Column('title', sa.String(255), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('icon', sa.String(10), server_default='🔔', nullable=True),
            sa.Column('link', sa.String(512), nullable=True),
            sa.Column('link_text', sa.String(100), nullable=True),
            sa.Column('is_read', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.Column('read_at', sa.DateTime(), nullable=True),
            sa.Column('metadata_json', sa.Text(), nullable=True),
        )
        if not _index_exists(conn, 'ix_notifications_id', 'notifications'):
            op.create_index('ix_notifications_id', 'notifications', ['id'])
        if not _index_exists(conn, 'ix_notifications_user_id', 'notifications'):
            op.create_index('ix_notifications_user_id', 'notifications', ['user_id'])
        if not _index_exists(conn, 'ix_notifications_created_at', 'notifications'):
            op.create_index('ix_notifications_created_at', 'notifications', ['created_at'])

    # ── 9. team_invitations (не было в миграциях 001-017) ────────────────────
    if not _table_exists(conn, 'team_invitations'):
        op.create_table(
            'team_invitations',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
            sa.Column('invited_email', sa.String(255), nullable=False),
            sa.Column('token', sa.String(128), nullable=False),
            sa.Column('status', sa.String(20), nullable=False, server_default='PENDING'),
            sa.Column('invited_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('accepted_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.Column('accepted_at', sa.DateTime(), nullable=True),
        )
        if not _index_exists(conn, 'ix_team_invitations_id', 'team_invitations'):
            op.create_index('ix_team_invitations_id', 'team_invitations', ['id'])
        if not _index_exists(conn, 'ix_team_invitations_team_id', 'team_invitations'):
            op.create_index('ix_team_invitations_team_id', 'team_invitations', ['team_id'])
        if not _index_exists(conn, 'ix_team_invitations_invited_email', 'team_invitations'):
            op.create_index('ix_team_invitations_invited_email', 'team_invitations', ['invited_email'])
        op.create_unique_constraint('uq_team_invitations_token', 'team_invitations', ['token'])

    # ── 10. team_scripts (не было в миграциях 001-017) ────────────────────────
    if not _table_exists(conn, 'team_scripts'):
        op.create_table(
            'team_scripts',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
            sa.Column('title', sa.String(255), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('script_json', sa.Text(), nullable=False),
            sa.Column('original_text', sa.Text(), nullable=True),
            sa.Column('uploaded_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        )
        if not _index_exists(conn, 'ix_team_scripts_id', 'team_scripts'):
            op.create_index('ix_team_scripts_id', 'team_scripts', ['id'])
        # team_id уникален (один скрипт на команду) — соответствует модели TeamScript
        if not _index_exists(conn, 'ix_team_scripts_team_id', 'team_scripts'):
            op.create_index('ix_team_scripts_team_id', 'team_scripts', ['team_id'], unique=True)

    # ── 11. training_conversion_metrics (не было в миграциях 001-017) ─────────
    if not _table_exists(conn, 'training_conversion_metrics'):
        op.create_table(
            'training_conversion_metrics',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=True),
            sa.Column('metric_date', sa.DateTime(), nullable=False),
            sa.Column('period_type', sa.String(20), server_default='daily', nullable=True),
            sa.Column('conversion_rates_json', sa.Text(), nullable=False),
            sa.Column('total_plans', sa.Integer(), server_default='0', nullable=True),
            sa.Column('active_plans', sa.Integer(), server_default='0', nullable=True),
            sa.Column('completed_plans', sa.Integer(), server_default='0', nullable=True),
            sa.Column('total_trainings', sa.Integer(), server_default='0', nullable=True),
            sa.Column('completed_trainings', sa.Integer(), server_default='0', nullable=True),
            sa.Column('avg_score', sa.Float(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        )
        if not _index_exists(conn, 'ix_training_conversion_metrics_id', 'training_conversion_metrics'):
            op.create_index('ix_training_conversion_metrics_id', 'training_conversion_metrics', ['id'])
        if not _index_exists(conn, 'ix_training_conversion_metrics_user_id', 'training_conversion_metrics'):
            op.create_index('ix_training_conversion_metrics_user_id', 'training_conversion_metrics', ['user_id'])
        if not _index_exists(conn, 'ix_training_conversion_metrics_team_id', 'training_conversion_metrics'):
            op.create_index('ix_training_conversion_metrics_team_id', 'training_conversion_metrics', ['team_id'])
        if not _index_exists(conn, 'ix_training_conversion_metrics_date', 'training_conversion_metrics'):
            op.create_index('ix_training_conversion_metrics_date', 'training_conversion_metrics', ['metric_date'])

    # ── 12. training_errors_corrections (не было в миграциях 001-017) ─────────
    if not _table_exists(conn, 'training_errors_corrections'):
        op.create_table(
            'training_errors_corrections',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=True),
            sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id'), nullable=False),
            sa.Column('message_id', sa.Integer(), sa.ForeignKey('messages.id'), nullable=False),
            sa.Column('error_type', sa.String(100), nullable=False),
            sa.Column('error_description', sa.Text(), nullable=False),
            sa.Column('error_severity', sa.String(20), server_default='medium', nullable=True),
            sa.Column('correction_text', sa.Text(), nullable=False),
            sa.Column('correction_applied', sa.Boolean(), server_default='false', nullable=True),
            sa.Column('correction_applied_at', sa.DateTime(), nullable=True),
            sa.Column('training_plan_id', sa.Integer(), sa.ForeignKey('analysis_training_plans.id'), nullable=True),
            sa.Column('training_id', sa.Integer(), sa.ForeignKey('trainings.id'), nullable=True),
            sa.Column('detected_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        )
        if not _index_exists(conn, 'ix_training_errors_corrections_id', 'training_errors_corrections'):
            op.create_index('ix_training_errors_corrections_id', 'training_errors_corrections', ['id'])
        if not _index_exists(conn, 'ix_training_errors_corrections_user_id', 'training_errors_corrections'):
            op.create_index('ix_training_errors_corrections_user_id', 'training_errors_corrections', ['user_id'])
        if not _index_exists(conn, 'ix_training_errors_corrections_team_id', 'training_errors_corrections'):
            op.create_index('ix_training_errors_corrections_team_id', 'training_errors_corrections', ['team_id'])

    # ── 13. research_logs (не было в миграциях 001-017) ─────────────────────
    if not _table_exists(conn, 'research_logs'):
        op.create_table(
            'research_logs',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('file_path', sa.String(500), nullable=False),
            sa.Column('file_name', sa.String(255), nullable=False),
            sa.Column('file_size_bytes', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('models_used', sa.Text(), nullable=True),
            sa.Column('total_input_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('total_output_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('stages_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('status', sa.String(20), nullable=False, server_default='running'),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
        )
        if not _index_exists(conn, 'ix_research_logs_id', 'research_logs'):
            op.create_index('ix_research_logs_id', 'research_logs', ['id'])
        if not _index_exists(conn, 'ix_research_logs_conversation_id', 'research_logs'):
            op.create_index('ix_research_logs_conversation_id', 'research_logs', ['conversation_id'])
        if not _index_exists(conn, 'ix_research_logs_user_id', 'research_logs'):
            op.create_index('ix_research_logs_user_id', 'research_logs', ['user_id'])


def downgrade():
    # Downgrade удаляет только то, что явно создано в upgrade.
    # Идемпотентно (guard'ы): таблицы могли быть уже удалены другой миграцией
    # или вовсе не существовать — drop не должен падать.
    conn = op.get_bind()

    for tbl in (
        'research_logs',
        'training_errors_corrections',
        'training_conversion_metrics',
        'team_scripts',
        'team_invitations',
        'notifications',
        'training_program_days',
        'training_programs',
    ):
        if _table_exists(conn, tbl):
            op.drop_table(tbl)

    for col in ('product_mode', 'organization_id'):
        if _column_exists(conn, 'users', col):
            op.drop_column('users', col)
    if _column_exists(conn, 'teams', 'organization_id'):
        op.drop_column('teams', 'organization_id')
    if _table_exists(conn, 'organizations'):
        op.drop_table('organizations')
    for col in ('premium_granted_at', 'premium_granted_by', 'analyses_used',
                'free_analyses_limit', 'is_premium'):
        if _column_exists(conn, 'users', col):
            op.drop_column('users', col)
