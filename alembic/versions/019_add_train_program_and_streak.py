"""add streak columns to seller_passports

Revision ID: 019
Revises: 018
Create Date: 2026-06-18

Таблицы training_programs / training_program_days создаёт миграция 018,
поэтому здесь — только новые streak-колонки SellerPassport.
Идемпотентно: колонки могли быть добавлены страховочным create_all / ручным
ALTER, поэтому добавляем только недостающее (через инспектор).
"""
from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _insp().get_table_names()


def _has_col(table: str, col: str) -> bool:
    return any(c["name"] == col for c in _insp().get_columns(table))


def upgrade():
    if _has_table("seller_passports"):
        if not _has_col("seller_passports", "current_streak"):
            op.add_column("seller_passports", sa.Column("current_streak", sa.Integer, nullable=False, server_default="0"))
        if not _has_col("seller_passports", "best_streak"):
            op.add_column("seller_passports", sa.Column("best_streak", sa.Integer, nullable=False, server_default="0"))
        if not _has_col("seller_passports", "last_trained_date"):
            op.add_column("seller_passports", sa.Column("last_trained_date", sa.Date, nullable=True))


def downgrade():
    if _has_table("seller_passports"):
        for col in ("last_trained_date", "best_streak", "current_streak"):
            if _has_col("seller_passports", col):
                op.drop_column("seller_passports", col)
