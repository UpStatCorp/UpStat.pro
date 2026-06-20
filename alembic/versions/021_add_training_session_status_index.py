"""add index on training_sessions.status (+ composite user_id, status)

Revision ID: 021
Revises: 020
Create Date: 2026-06-20

status часто фильтруется (расчёт стрика, выборки активных/завершённых сессий),
но индекса на нём не было — это приводило к полному скану. Композитный
(user_id, status) ускоряет per-user расчёт стрика.
Идемпотентно: создаём индекс только если его ещё нет.
"""
from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None

_TABLE = "training_sessions"
_IX_STATUS = "ix_training_sessions_status"
_IX_USER_STATUS = "ix_training_sessions_user_id_status"


def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _insp().get_table_names()


def _index_names(table: str) -> set:
    return {ix["name"] for ix in _insp().get_indexes(table)}


def upgrade():
    if not _has_table(_TABLE):
        return
    existing = _index_names(_TABLE)
    if _IX_STATUS not in existing:
        op.create_index(_IX_STATUS, _TABLE, ["status"])
    if _IX_USER_STATUS not in existing:
        op.create_index(_IX_USER_STATUS, _TABLE, ["user_id", "status"])


def downgrade():
    if not _has_table(_TABLE):
        return
    existing = _index_names(_TABLE)
    if _IX_USER_STATUS in existing:
        op.drop_index(_IX_USER_STATUS, table_name=_TABLE)
    if _IX_STATUS in existing:
        op.drop_index(_IX_STATUS, table_name=_TABLE)
