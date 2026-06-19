"""Backfill product_mode='full' for legacy users (pre-018)

Revision ID: 020
Revises: 019
Create Date: 2026-06-20 00:00:00.000000

Миграция 018 добавила users.product_mode как nullable БЕЗ backfill. Система
доступа (capability_service) — fail-closed: product_mode IS NULL → SKU FREE →
урезанная платформа. Все аккаунты, созданные ДО релиза capability/SKU, остались
с NULL и потеряли доступ, хотя до релиза гейтинга не было (по факту были FULL).

Эта миграция восстанавливает прежнее поведение: всем существующим юзерам с
NULL product_mode проставляется 'full'. Новые регистрации получают корректный
product_mode в auth.py/google_oauth.py ('free'/'train'/'full'), поэтому NULL
после релиза появиться не может — backfill безопасен и одноразов.

Идемпотентна: повторный прогон / уже исправленные вручную строки не затрагивает.
"""
from alembic import op


revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET product_mode = 'full' WHERE product_mode IS NULL")


def downgrade():
    # Откат намеренно НЕ возвращает NULL: это привело бы к потере доступа.
    # Backfill — восстановление состояния до 018, отменять его незачем.
    pass
