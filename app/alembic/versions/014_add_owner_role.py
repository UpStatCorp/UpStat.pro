"""Add owner role support (no schema changes - role is a string field)

Revision ID: 014
Revises: 013
Create Date: 2026-04-13 18:00:00.000000

Note: role_in_team already supports any string value.
This migration just documents that "owner" is now a valid role
alongside "member", "assistant_manager", "manager".
"""
from alembic import op
import sqlalchemy as sa


revision = '014'
down_revision = '013'
branch_labels = None
depends_on = None


def upgrade():
    # No schema changes needed.
    # role_in_team is String(50) and already accepts "owner" as a value.
    # The owner role is handled at the application level in:
    #   - app/routers/owner_dashboard.py
    #   - app/services/owner_analytics_service.py
    pass


def downgrade():
    # Optionally revert any "owner" roles back to "manager"
    op.execute(
        "UPDATE team_members SET role_in_team = 'manager' WHERE role_in_team = 'owner'"
    )
