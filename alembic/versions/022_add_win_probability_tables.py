"""Win Probability: таблицы, появившиеся мимо миграций

Revision ID: 022
Revises: 021
Create Date: 2026-08-20

Фича Win Probability выкачена коммитом 8cbaeeb (2026-03-31) вообще без
миграции: три таблицы существуют на проде только потому, что их создал
Base.metadata.create_all на старте приложения. На чистой базе их нет,
поэтому `alembic upgrade head` давал схему без ядра расчёта вероятности
сделки, а create_all убрать было нельзя.

Данные на проде на момент написания: checklist_item_definitions 85 строк,
checklist_item_scores 985, win_probability_scores 13.

ПРО GUARD'Ы. Здесь они выражают конкретное известное расхождение:
на проде таблицы есть, на чистой базе — нет. Это не «на всякий случай».
После того как прод дойдёт до этой ревизии, guard'ы навсегда станут
мёртвым кодом — убрать их будет нельзя, история застынет. Цена принята
сознательно.

Guard индекса НЕ вложен в guard таблицы. Ровно эта вложенность в 018
(строки 79-83, 86-90) стоила проду двух индексов: колонка уже
существовала от create_all, внешнее условие было ложным, и создание
индекса пропускалось вместе с ней.

ВНИМАНИЕ: downgrade этой ревизии на проде запускать НЕЛЬЗЯ — он
уничтожит 1083 строки живых данных (85 + 985 + 13). Откат прода
делается откатом образа, а не downgrade'ом схемы.

Порядок создания фиксирован из-за FK:
    checklist_item_definitions -> checklist_item_scores (item_id)
"""
from alembic import op
import sqlalchemy as sa


revision = '022'
down_revision = '021'
branch_labels = None
depends_on = None


def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _insp().get_table_names()


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return name in {ix["name"] for ix in _insp().get_indexes(table)}


def upgrade():
    # ── checklist_item_definitions ────────────────────────────────────────
    if not _has_table('checklist_item_definitions'):
        op.create_table(
            'checklist_item_definitions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('checklist_id', sa.String(length=100), nullable=False),
            sa.Column('checklist_title', sa.String(length=255), nullable=False),
            sa.Column('block_title', sa.String(length=255), nullable=False),
            sa.Column('block_order', sa.Integer(), nullable=False),
            sa.Column('item_order', sa.Integer(), nullable=False),
            sa.Column('item_text', sa.Text(), nullable=False),
            sa.Column('item_code', sa.String(length=150), nullable=False),
            sa.Column('weight', sa.Float(), nullable=False),
            sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )

    # Индексы — отдельными плоскими условиями, НЕ внутри guard'а таблицы.
    if not _has_index('checklist_item_definitions', 'ix_checklist_item_definitions_checklist_id'):
        op.create_index(op.f('ix_checklist_item_definitions_checklist_id'),
                        'checklist_item_definitions', ['checklist_id'], unique=False)
    if not _has_index('checklist_item_definitions', 'ix_checklist_item_definitions_id'):
        op.create_index(op.f('ix_checklist_item_definitions_id'),
                        'checklist_item_definitions', ['id'], unique=False)
    if not _has_index('checklist_item_definitions', 'ix_checklist_item_definitions_item_code'):
        op.create_index(op.f('ix_checklist_item_definitions_item_code'),
                        'checklist_item_definitions', ['item_code'], unique=True)

    # ── checklist_item_scores (FK на checklist_item_definitions) ──────────
    if not _has_table('checklist_item_scores'):
        op.create_table(
            'checklist_item_scores',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('conversation_id', sa.Integer(), nullable=False),
            sa.Column('item_id', sa.Integer(), nullable=False),
            sa.Column('passed', sa.Boolean(), nullable=False),
            sa.Column('confidence', sa.Float(), nullable=False),
            sa.Column('ai_comment', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
            sa.ForeignKeyConstraint(['item_id'], ['checklist_item_definitions.id'], ),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('conversation_id', 'item_id', name='uq_conv_checklist_item'),
        )

    if not _has_index('checklist_item_scores', 'ix_checklist_item_scores_conversation_id'):
        op.create_index(op.f('ix_checklist_item_scores_conversation_id'),
                        'checklist_item_scores', ['conversation_id'], unique=False)
    if not _has_index('checklist_item_scores', 'ix_checklist_item_scores_id'):
        op.create_index(op.f('ix_checklist_item_scores_id'),
                        'checklist_item_scores', ['id'], unique=False)
    if not _has_index('checklist_item_scores', 'ix_checklist_item_scores_item_id'):
        op.create_index(op.f('ix_checklist_item_scores_item_id'),
                        'checklist_item_scores', ['item_id'], unique=False)

    # ── win_probability_scores ───────────────────────────────────────────
    if not _has_table('win_probability_scores'):
        op.create_table(
            'win_probability_scores',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('conversation_id', sa.Integer(), nullable=False),
            sa.Column('crm_recording_id', sa.Integer(), nullable=True),
            sa.Column('deal_id', sa.Integer(), nullable=True),
            sa.Column('lead_id', sa.Integer(), nullable=True),
            sa.Column('deal_status', sa.String(length=50), nullable=True),
            sa.Column('total_items', sa.Integer(), nullable=False),
            sa.Column('passed_items', sa.Integer(), nullable=False),
            sa.Column('failed_items', sa.Integer(), nullable=False),
            sa.Column('weighted_score', sa.Float(), nullable=False),
            sa.Column('win_probability', sa.Float(), nullable=False),
            sa.Column('max_probability', sa.Float(), nullable=False),
            sa.Column('score_breakdown_json', sa.Text(), nullable=True),
            sa.Column('ai_summary', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
            sa.ForeignKeyConstraint(['crm_recording_id'], ['crm_recordings.id'], ),
            sa.ForeignKeyConstraint(['deal_id'], ['crm_deals.id'], ),
            sa.ForeignKeyConstraint(['lead_id'], ['crm_leads.id'], ),
            sa.PrimaryKeyConstraint('id'),
        )

    if not _has_index('win_probability_scores', 'ix_win_probability_scores_conversation_id'):
        op.create_index(op.f('ix_win_probability_scores_conversation_id'),
                        'win_probability_scores', ['conversation_id'], unique=True)
    if not _has_index('win_probability_scores', 'ix_win_probability_scores_id'):
        op.create_index(op.f('ix_win_probability_scores_id'),
                        'win_probability_scores', ['id'], unique=False)


def downgrade():
    # НА ПРОДЕ НЕ ЗАПУСКАТЬ: уничтожит живые данные (см. докстринг).
    op.drop_table('win_probability_scores')
    op.drop_table('checklist_item_scores')
    op.drop_table('checklist_item_definitions')
