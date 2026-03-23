"""Add analytics tables: parameter_definitions, parameter_values, crm_manager_mappings, analytics_messages

Revision ID: 008
Revises: 007
Create Date: 2026-03-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('parameter_definitions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('value_type', sa.String(length=20), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=False),
        sa.Column('unit', sa.String(length=50), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index(op.f('ix_parameter_definitions_id'), 'parameter_definitions', ['id'], unique=False)

    op.create_table('parameter_values',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('parameter_id', sa.Integer(), nullable=False),
        sa.Column('value_number', sa.Float(), nullable=True),
        sa.Column('value_text', sa.Text(), nullable=True),
        sa.Column('value_bool', sa.Boolean(), nullable=True),
        sa.Column('confidence', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.ForeignKeyConstraint(['parameter_id'], ['parameter_definitions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'parameter_id', name='uq_conv_param'),
    )
    op.create_index(op.f('ix_parameter_values_id'), 'parameter_values', ['id'], unique=False)
    op.create_index(op.f('ix_parameter_values_conversation_id'), 'parameter_values', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_parameter_values_parameter_id'), 'parameter_values', ['parameter_id'], unique=False)

    op.create_table('crm_manager_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('integration_id', sa.Integer(), nullable=False),
        sa.Column('crm_manager_name', sa.String(length=255), nullable=False),
        sa.Column('crm_manager_id', sa.String(length=100), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['integration_id'], ['crm_integrations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('integration_id', 'crm_manager_name', name='uq_integ_crm_mgr'),
    )
    op.create_index(op.f('ix_crm_manager_mappings_id'), 'crm_manager_mappings', ['id'], unique=False)
    op.create_index(op.f('ix_crm_manager_mappings_integration_id'), 'crm_manager_mappings', ['integration_id'], unique=False)

    op.create_table('analytics_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=10), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_analytics_messages_id'), 'analytics_messages', ['id'], unique=False)
    op.create_index(op.f('ix_analytics_messages_user_id'), 'analytics_messages', ['user_id'], unique=False)

    op.execute("""
        INSERT INTO parameter_definitions (code, title, description, value_type, category, unit) VALUES
        ('talk_listen_ratio', 'Talk-to-Listen Ratio', 'Соотношение времени речи менеджера к времени речи клиента', 'number', 'dialogue_dynamics', '%%'),
        ('avg_manager_reply_len', 'Средняя длина реплики менеджера', 'Среднее количество слов в одной реплике менеджера', 'number', 'dialogue_dynamics', 'слов'),
        ('avg_client_reply_len', 'Средняя длина реплики клиента', 'Среднее количество слов в одной реплике клиента', 'number', 'dialogue_dynamics', 'слов'),
        ('dialogue_density', 'Плотность диалога', 'Количество смен ролей (реплик) на минуту разговора', 'number', 'dialogue_dynamics', 'реплик/мин'),
        ('manager_questions_count', 'Количество вопросов менеджера', 'Общее число вопросительных предложений менеджера', 'number', 'questions', 'шт'),
        ('questions_by_stage', 'Распределение вопросов по этапам', 'JSON: количество вопросов на каждом этапе разговора', 'text', 'questions', NULL),
        ('system_identified', 'Зафиксирована ли система клиента', 'Выявил ли менеджер текущую систему/процесс клиента', 'boolean', 'needs_analysis', NULL),
        ('problem_identified', 'Зафиксирована ли проблема', 'Выявил ли менеджер проблему/боль клиента', 'boolean', 'needs_analysis', NULL),
        ('consequences_identified', 'Зафиксированы ли последствия', 'Обсудил ли менеджер последствия нерешённой проблемы', 'boolean', 'needs_analysis', NULL),
        ('price_devaluation', 'Обесценивание цены менеджером', 'Обесценивал ли менеджер собственный продукт/цену', 'boolean', 'sales_technique', NULL),
        ('objections_count', 'Количество возражений', 'Сколько возражений высказал клиент за звонок', 'number', 'objections', 'шт');
    """)


def downgrade():
    op.drop_table('analytics_messages')
    op.drop_table('crm_manager_mappings')
    op.drop_table('parameter_values')
    op.drop_table('parameter_definitions')
