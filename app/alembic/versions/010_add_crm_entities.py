"""Add CRM entity tables (deals, leads, contacts, companies, activities, deal products)

Revision ID: 010_add_crm_entities
Revises: 009_add_chat_support
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa

revision = '010_add_crm_entities'
down_revision = '009_add_chat_support'
branch_labels = None
depends_on = None


def _table_exists(conn, table_name):
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :t)"
    ), {"t": table_name})
    return result.scalar()


def _column_exists(conn, table_name, column_name):
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c)"
    ), {"t": table_name, "c": column_name})
    return result.scalar()


def upgrade():
    conn = op.get_bind()

    if not _table_exists(conn, 'crm_deals'):
        op.create_table(
            'crm_deals',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('integration_id', sa.Integer(), sa.ForeignKey('crm_integrations.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('bitrix_id', sa.Integer(), nullable=False, unique=True, index=True),
            sa.Column('title', sa.String(500), nullable=True),
            sa.Column('stage_id', sa.String(100), nullable=True),
            sa.Column('stage_name', sa.String(255), nullable=True),
            sa.Column('category_id', sa.Integer(), nullable=True),
            sa.Column('category_name', sa.String(255), nullable=True),
            sa.Column('opportunity', sa.Float(), nullable=True),
            sa.Column('currency_id', sa.String(10), nullable=True),
            sa.Column('closed', sa.Boolean(), server_default='false', nullable=False),
            sa.Column('is_won', sa.Boolean(), nullable=True),
            sa.Column('probability', sa.Integer(), nullable=True),
            sa.Column('source_id', sa.String(100), nullable=True),
            sa.Column('source_name', sa.String(255), nullable=True),
            sa.Column('assigned_by_id', sa.Integer(), nullable=True),
            sa.Column('assigned_by_name', sa.String(255), nullable=True),
            sa.Column('contact_id', sa.Integer(), nullable=True),
            sa.Column('company_id', sa.Integer(), nullable=True),
            sa.Column('close_date', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('loss_reason', sa.String(500), nullable=True),
            sa.Column('comments', sa.Text(), nullable=True),
            sa.Column('crm_metadata_json', sa.Text(), nullable=True),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'crm_leads'):
        op.create_table(
            'crm_leads',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('integration_id', sa.Integer(), sa.ForeignKey('crm_integrations.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('bitrix_id', sa.Integer(), nullable=False, unique=True, index=True),
            sa.Column('title', sa.String(500), nullable=True),
            sa.Column('status_id', sa.String(100), nullable=True),
            sa.Column('status_name', sa.String(255), nullable=True),
            sa.Column('source_id', sa.String(100), nullable=True),
            sa.Column('source_name', sa.String(255), nullable=True),
            sa.Column('opportunity', sa.Float(), nullable=True),
            sa.Column('currency_id', sa.String(10), nullable=True),
            sa.Column('assigned_by_id', sa.Integer(), nullable=True),
            sa.Column('assigned_by_name', sa.String(255), nullable=True),
            sa.Column('converted', sa.Boolean(), server_default='false', nullable=False),
            sa.Column('converted_deal_id', sa.Integer(), nullable=True),
            sa.Column('converted_contact_id', sa.Integer(), nullable=True),
            sa.Column('converted_company_id', sa.Integer(), nullable=True),
            sa.Column('name', sa.String(255), nullable=True),
            sa.Column('phone', sa.String(100), nullable=True),
            sa.Column('email', sa.String(255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('crm_metadata_json', sa.Text(), nullable=True),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'crm_contacts'):
        op.create_table(
            'crm_contacts',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('integration_id', sa.Integer(), sa.ForeignKey('crm_integrations.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('bitrix_id', sa.Integer(), nullable=False, unique=True, index=True),
            sa.Column('name', sa.String(255), nullable=True),
            sa.Column('last_name', sa.String(255), nullable=True),
            sa.Column('second_name', sa.String(255), nullable=True),
            sa.Column('full_name', sa.String(500), nullable=True),
            sa.Column('post', sa.String(255), nullable=True),
            sa.Column('phone', sa.String(100), nullable=True),
            sa.Column('email', sa.String(255), nullable=True),
            sa.Column('company_id', sa.Integer(), nullable=True),
            sa.Column('assigned_by_id', sa.Integer(), nullable=True),
            sa.Column('assigned_by_name', sa.String(255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('crm_metadata_json', sa.Text(), nullable=True),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'crm_companies'):
        op.create_table(
            'crm_companies',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('integration_id', sa.Integer(), sa.ForeignKey('crm_integrations.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('bitrix_id', sa.Integer(), nullable=False, unique=True, index=True),
            sa.Column('title', sa.String(500), nullable=True),
            sa.Column('industry', sa.String(255), nullable=True),
            sa.Column('phone', sa.String(100), nullable=True),
            sa.Column('email', sa.String(255), nullable=True),
            sa.Column('web', sa.String(500), nullable=True),
            sa.Column('revenue', sa.Float(), nullable=True),
            sa.Column('currency_id', sa.String(10), nullable=True),
            sa.Column('assigned_by_id', sa.Integer(), nullable=True),
            sa.Column('assigned_by_name', sa.String(255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('crm_metadata_json', sa.Text(), nullable=True),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'crm_deal_products'):
        op.create_table(
            'crm_deal_products',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('deal_id', sa.Integer(), sa.ForeignKey('crm_deals.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('product_id', sa.Integer(), nullable=True),
            sa.Column('product_name', sa.String(500), nullable=True),
            sa.Column('quantity', sa.Float(), nullable=True),
            sa.Column('price', sa.Float(), nullable=True),
            sa.Column('discount_sum', sa.Float(), nullable=True),
            sa.Column('tax_rate', sa.Float(), nullable=True),
            sa.Column('sum_total', sa.Float(), nullable=True),
        )

    if not _table_exists(conn, 'crm_activities'):
        op.create_table(
            'crm_activities',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('integration_id', sa.Integer(), sa.ForeignKey('crm_integrations.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('bitrix_id', sa.Integer(), nullable=False, unique=True, index=True),
            sa.Column('type_id', sa.Integer(), nullable=True),
            sa.Column('type_name', sa.String(100), nullable=True),
            sa.Column('subject', sa.String(500), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('owner_type_id', sa.Integer(), nullable=True),
            sa.Column('owner_id', sa.Integer(), nullable=True),
            sa.Column('responsible_id', sa.Integer(), nullable=True),
            sa.Column('responsible_name', sa.String(255), nullable=True),
            sa.Column('direction', sa.Integer(), nullable=True),
            sa.Column('completed', sa.Boolean(), server_default='false', nullable=False),
            sa.Column('start_time', sa.DateTime(), nullable=True),
            sa.Column('end_time', sa.DateTime(), nullable=True),
            sa.Column('duration_seconds', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('crm_metadata_json', sa.Text(), nullable=True),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _column_exists(conn, 'crm_recordings', 'deal_id'):
        op.add_column('crm_recordings', sa.Column('deal_id', sa.Integer(), sa.ForeignKey('crm_deals.id'), nullable=True))
        op.create_index('ix_crm_recordings_deal_id', 'crm_recordings', ['deal_id'])
    if not _column_exists(conn, 'crm_recordings', 'lead_id'):
        op.add_column('crm_recordings', sa.Column('lead_id', sa.Integer(), sa.ForeignKey('crm_leads.id'), nullable=True))
        op.create_index('ix_crm_recordings_lead_id', 'crm_recordings', ['lead_id'])
    if not _column_exists(conn, 'crm_recordings', 'contact_crm_id'):
        op.add_column('crm_recordings', sa.Column('contact_crm_id', sa.Integer(), sa.ForeignKey('crm_contacts.id'), nullable=True))
        op.create_index('ix_crm_recordings_contact_crm_id', 'crm_recordings', ['contact_crm_id'])


def downgrade():
    op.drop_index('ix_crm_recordings_contact_crm_id', 'crm_recordings')
    op.drop_index('ix_crm_recordings_lead_id', 'crm_recordings')
    op.drop_index('ix_crm_recordings_deal_id', 'crm_recordings')
    op.drop_column('crm_recordings', 'contact_crm_id')
    op.drop_column('crm_recordings', 'lead_id')
    op.drop_column('crm_recordings', 'deal_id')
    op.drop_table('crm_activities')
    op.drop_table('crm_deal_products')
    op.drop_table('crm_companies')
    op.drop_table('crm_contacts')
    op.drop_table('crm_leads')
    op.drop_table('crm_deals')
