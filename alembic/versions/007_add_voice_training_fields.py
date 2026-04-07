"""Placeholder revision: поля voice training уже в 005_add_voice_training_fields.

Нужен как звено цепочки (раньше ссылались 006_add_webrtc_tables -> 007 без файла 006).

Revision ID: 007_add_voice_training_fields
Revises: 006_add_webrtc_tables
Create Date: 2025-03-10
"""


revision = "007_add_voice_training_fields"
down_revision = "006_add_webrtc_tables"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
