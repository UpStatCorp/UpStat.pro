"""Выравнивание прод-схемы и схемы «с нуля» по app/models.py

Revision ID: 023
Revises: 022
Create Date: 2026-08-20

Прод и чистая база разошлись, потому что прод построил Base.metadata.create_all
ИЗ МОДЕЛЕЙ, а чистая база строится миграциями, написанными руками. Проверка
показала: DDL миграций на проде практически не исполнялся — ни один индекс,
создаваемый только миграцией и не объявленный в моделях, на проде не существует.
Схему целиком сделал create_all; миграции внесли только данные (сиды 015/016,
бэкфилл 020).

Отсюда асимметрия правок ниже: подавляющее большинство операций на проде —
no-op, а по-настоящему выполняются на чистой базе. Реальных изменений на проде
ровно 9, и они подтверждены прогоном autogenerate против копии прод-базы:

    1. crm_recordings.batch_id     varchar(100) -> varchar(20)   (0 строк данных)
    2. CREATE INDEX ix_crm_recordings_batch_id
    3. CREATE INDEX ix_teams_organization_id
    4. CREATE INDEX ix_users_organization_id
    5. FK users_premium_granted_by_fkey
    6. DROP COLUMN users.google_access_token
    7. DROP COLUMN users.google_refresh_token
    8. DROP COLUMN users.google_token_expires_at
    9. DROP COLUMN users.is_google_user

ПРО ИДЕМПОТЕНТНОСТЬ. Вместо Python-guard'ов используется нативный синтаксис
PostgreSQL (IF EXISTS / IF NOT EXISTS, DO-блок для констрейнта). Проект
Postgres-only — app/database.py:9-10 явно запрещает sqlite, — поэтому нативный
синтаксис ничего не ломает. Для SET NOT NULL guard не нужен вовсе: на уже
NOT NULL колонке PostgreSQL выполняет это как no-op, без ошибки и без
сканирования таблицы.

Как и в 022, идемпотентность здесь выражает конкретное известное расхождение
прод/чистая база, а не написана «на всякий случай». После того как прод дойдёт
до этой ревизии, все IF EXISTS станут мёртвым кодом — убрать их будет нельзя,
история застынет. Цена принята сознательно.

Перед написанием проверено на копии прод-базы:
  * дубликатов bitrix_id в пяти CRM-таблицах и user_id в seller_passports нет
    (0 строк) — ни один CREATE UNIQUE INDEX не упрётся в данные;
  * сирот для FK users.premium_granted_by нет (0 из 45 пользователей,
    заполнено у одного, ссылка валидна);
  * колонки users.google_* не читаются приложением ни разу — грепом по
    *.py/*.html/*.js/*.sql/*.json найдена только сама миграция 017.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '023'
down_revision = '022'
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. SET NOT NULL: 70 колонок ────────────────────────────────────
    # Без guard'ов: ALTER COLUMN ... SET NOT NULL на уже NOT NULL колонке
    # в PostgreSQL — no-op без ошибки и без сканирования таблицы.
    # Прод удовлетворяет все 70, там это бесплатно; чистая база чинится.
    # action_patterns
    op.alter_column('action_patterns', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    op.alter_column('action_patterns', 'updated_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # analytics_messages
    op.alter_column('analytics_messages', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # crm_activities
    op.alter_column('crm_activities', 'synced_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # crm_companies
    op.alter_column('crm_companies', 'synced_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # crm_contacts
    op.alter_column('crm_contacts', 'synced_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # crm_deals
    op.alter_column('crm_deals', 'synced_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # crm_integrations
    op.alter_column('crm_integrations', 'recordings_count',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.alter_column('crm_integrations', 'analyzed_count',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.alter_column('crm_integrations', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # crm_leads
    op.alter_column('crm_leads', 'synced_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # crm_manager_mappings
    op.alter_column('crm_manager_mappings', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # crm_recordings
    op.alter_column('crm_recordings', 'duration_seconds',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.alter_column('crm_recordings', 'direction',
                    existing_type=sa.VARCHAR(length=20),
                    nullable=False)
    op.alter_column('crm_recordings', 'sync_status',
                    existing_type=sa.VARCHAR(length=20),
                    nullable=False)
    op.alter_column('crm_recordings', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # custom_meeting_transcripts
    op.alter_column('custom_meeting_transcripts', 'content',
                    existing_type=sa.TEXT(),
                    nullable=False)
    op.alter_column('custom_meeting_transcripts', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False)
    # custom_meetings
    op.alter_column('custom_meetings', 'meeting_id',
                    existing_type=sa.VARCHAR(length=255),
                    nullable=False)
    op.alter_column('custom_meetings', 'status',
                    existing_type=sa.VARCHAR(length=20),
                    nullable=False)
    op.alter_column('custom_meetings', 'max_participants',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.alter_column('custom_meetings', 'duration_minutes',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.alter_column('custom_meetings', 'ai_agent_enabled',
                    existing_type=sa.BOOLEAN(),
                    nullable=False)
    op.alter_column('custom_meetings', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False)
    # manager_actions
    op.alter_column('manager_actions', 'action_type',
                    existing_type=sa.VARCHAR(length=50),
                    nullable=False,
                    existing_server_default=sa.text("'phrase'::character varying"))
    op.alter_column('manager_actions', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # meeting_participants
    op.alter_column('meeting_participants', 'joined_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False)
    op.alter_column('meeting_participants', 'role',
                    existing_type=sa.VARCHAR(length=20),
                    nullable=False)
    # meeting_transcripts
    op.alter_column('meeting_transcripts', 'full_transcript',
                    existing_type=sa.TEXT(),
                    nullable=False)
    op.alter_column('meeting_transcripts', 'summary',
                    existing_type=sa.TEXT(),
                    nullable=False)
    op.alter_column('meeting_transcripts', 'participants_count',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.alter_column('meeting_transcripts', 'duration_seconds',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.alter_column('meeting_transcripts', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False)
    # notifications
    op.alter_column('notifications', 'icon',
                    existing_type=sa.VARCHAR(length=10),
                    nullable=False,
                    existing_server_default=sa.text("'🔔'::character varying"))
    op.alter_column('notifications', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # organizations
    op.alter_column('organizations', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # parameter_definitions
    op.alter_column('parameter_definitions', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # parameter_values
    op.alter_column('parameter_values', 'confidence',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.alter_column('parameter_values', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # passport_snapshots
    op.alter_column('passport_snapshots', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # prompts
    op.alter_column('prompts', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False)
    # research_logs
    op.alter_column('research_logs', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # seller_passports
    op.alter_column('seller_passports', 'last_updated_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    op.alter_column('seller_passports', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # team_invitations
    op.alter_column('team_invitations', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # team_scripts
    op.alter_column('team_scripts', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    op.alter_column('team_scripts', 'updated_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # training_conversion_metrics
    op.alter_column('training_conversion_metrics', 'period_type',
                    existing_type=sa.VARCHAR(length=20),
                    nullable=False,
                    existing_server_default=sa.text("'daily'::character varying"))
    op.alter_column('training_conversion_metrics', 'total_plans',
                    existing_type=sa.INTEGER(),
                    nullable=False,
                    existing_server_default=sa.text('0'))
    op.alter_column('training_conversion_metrics', 'active_plans',
                    existing_type=sa.INTEGER(),
                    nullable=False,
                    existing_server_default=sa.text('0'))
    op.alter_column('training_conversion_metrics', 'completed_plans',
                    existing_type=sa.INTEGER(),
                    nullable=False,
                    existing_server_default=sa.text('0'))
    op.alter_column('training_conversion_metrics', 'total_trainings',
                    existing_type=sa.INTEGER(),
                    nullable=False,
                    existing_server_default=sa.text('0'))
    op.alter_column('training_conversion_metrics', 'completed_trainings',
                    existing_type=sa.INTEGER(),
                    nullable=False,
                    existing_server_default=sa.text('0'))
    op.alter_column('training_conversion_metrics', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # training_errors_corrections
    op.alter_column('training_errors_corrections', 'error_severity',
                    existing_type=sa.VARCHAR(length=20),
                    nullable=False,
                    existing_server_default=sa.text("'medium'::character varying"))
    op.alter_column('training_errors_corrections', 'correction_applied',
                    existing_type=sa.BOOLEAN(),
                    nullable=False,
                    existing_server_default=sa.text('false'))
    op.alter_column('training_errors_corrections', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # training_programs
    op.alter_column('training_programs', 'start_date',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False)
    op.alter_column('training_programs', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # training_sessions
    op.alter_column('training_sessions', 'session_type',
                    existing_type=sa.VARCHAR(length=50),
                    nullable=False,
                    existing_server_default=sa.text("'text'::character varying"))
    op.alter_column('training_sessions', 'status',
                    existing_type=sa.VARCHAR(length=20),
                    nullable=False,
                    existing_server_default=sa.text("'active'::character varying"))
    # users
    op.alter_column('users', 'role',
                    existing_type=sa.VARCHAR(length=10),
                    type_=sa.String(length=20),
                    nullable=False,
                    existing_server_default=sa.text("'user'::character varying"))
    # voice_training_messages
    op.alter_column('voice_training_messages', 'timestamp',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False,
                    existing_server_default=sa.text('now()'))
    # zoom_meetings
    op.alter_column('zoom_meetings', 'topic',
                    existing_type=sa.VARCHAR(length=255),
                    nullable=False)
    op.alter_column('zoom_meetings', 'start_time',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False)
    op.alter_column('zoom_meetings', 'duration_minutes',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.alter_column('zoom_meetings', 'status',
                    existing_type=sa.VARCHAR(length=20),
                    nullable=False)
    op.alter_column('zoom_meetings', 'join_url',
                    existing_type=sa.VARCHAR(length=512),
                    nullable=False)
    op.alter_column('zoom_meetings', 'ai_agent_enabled',
                    existing_type=sa.BOOLEAN(),
                    nullable=False)
    op.alter_column('zoom_meetings', 'created_at',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False)

    # ── 2. Индексы: снос лишних ────────────────────────────────────────
    # Нативный DROP INDEX IF EXISTS вместо Python-guard'а.
    op.execute("DROP INDEX IF EXISTS ix_crm_activities_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_companies_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_contacts_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_deals_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_leads_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_recordings_conversation_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_recordings_sync_status")
    op.execute("DROP INDEX IF EXISTS ix_prompts_created_by")
    op.execute("DROP INDEX IF EXISTS ix_prompts_is_active")
    op.execute("DROP INDEX IF EXISTS ix_prompts_name")
    op.execute("DROP INDEX IF EXISTS ix_seller_passports_user_id")
    op.execute("DROP INDEX IF EXISTS ix_training_conversion_metrics_date")
    op.execute("DROP INDEX IF EXISTS ix_users_role")

    # ── 3. Индексы: создание недостающих ───────────────────────────────
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_activities_bitrix_id ON crm_activities (bitrix_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_activities_id ON crm_activities (id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_companies_bitrix_id ON crm_companies (bitrix_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_companies_id ON crm_companies (id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_contacts_bitrix_id ON crm_contacts (bitrix_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_contacts_id ON crm_contacts (id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_deal_products_id ON crm_deal_products (id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_deals_bitrix_id ON crm_deals (bitrix_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_deals_id ON crm_deals (id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_integrations_id ON crm_integrations (id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_leads_bitrix_id ON crm_leads (bitrix_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_leads_id ON crm_leads (id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_recordings_id ON crm_recordings (id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_organizations_id ON organizations (id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_seller_passports_user_id ON seller_passports (user_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_team_invitations_token ON team_invitations (token)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_training_conversion_metrics_metric_date ON training_conversion_metrics (metric_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_training_errors_corrections_conversation_id ON training_errors_corrections (conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_training_errors_corrections_detected_at ON training_errors_corrections (detected_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_training_errors_corrections_message_id ON training_errors_corrections (message_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_id ON users (google_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_voice_training_messages_id ON voice_training_messages (id)")

    # ── 3b. Расхождения, видимые ТОЛЬКО со стороны прода ───────────────
    # Всё выше выведено из диффа на ЧИСТОЙ базе. Эти четыре операции туда
    # не попадают: на чистой базе они уже выполнены более ранними
    # миграциями (008 создаёт batch_id varchar(20) и его индекс, 018 —
    # индексы на organization_id), а на проде их нет — там DDL этих
    # миграций не отработал, потому что колонки уже существовали от
    # create_all и guard'ы пропустили блоки целиком (в 018 создание
    # индекса вложено внутрь guard'а колонки, строки 79-83 и 86-90).
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_organization_id ON users (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_teams_organization_id ON teams (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_recordings_batch_id ON crm_recordings (batch_id)")
    # batch_id: на проде varchar(100) вместо varchar(20) из 008 и моделей.
    # Данных 0 строк из 650 — фича батчей не использовалась ни разу,
    # сужение типа безопасно. На чистой базе тип уже varchar(20), no-op.
    op.execute("ALTER TABLE crm_recordings ALTER COLUMN batch_id TYPE VARCHAR(20)")

    # ── 4. Констрейнты ─────────────────────────────────────────────────
    op.execute("ALTER TABLE seller_passports DROP CONSTRAINT IF EXISTS seller_passports_user_id_key")
    op.execute("ALTER TABLE team_invitations DROP CONSTRAINT IF EXISTS uq_team_invitations_token")

    # ── 5. Колонки, которые не создаёт ни одна миграция ────────────────
    # На проде обе есть (их сделал create_all), на чистой базе — нет.
    # voice_training_messages.duration_seconds — Mapped[Optional[int]], nullable.
    op.execute("ALTER TABLE voice_training_messages ADD COLUMN IF NOT EXISTS duration_seconds INTEGER")
    # zoom_meetings.agent_active — Mapped[bool], NOT NULL. Проставляем значение
    # перед SET NOT NULL: на чистой базе таблица пуста, на проде колонка уже
    # NOT NULL и оба выражения — no-op.
    op.execute("ALTER TABLE zoom_meetings ADD COLUMN IF NOT EXISTS agent_active BOOLEAN")
    op.execute("UPDATE zoom_meetings SET agent_active = false WHERE agent_active IS NULL")
    op.execute("ALTER TABLE zoom_meetings ALTER COLUMN agent_active SET NOT NULL")

    # ── 6. Мёртвые колонки users.google_* ──────────────────────────────
    # Добавлены 017, приложением не читаются НИ РАЗУ (проверено грепом
    # по *.py/*.html/*.js/*.sql/*.json — только сама 017). На проде
    # is_google_user = 0 из 45, реально используется is_oauth_user.
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS google_token_expires_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS google_refresh_token")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_google_user")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS google_access_token")

    # ── 7. FK users.premium_granted_by ────────────────────────────────
    # 018 добавила колонку без внешнего ключа, модель объявляет
    # ForeignKey('users.id'). Сирот на проде 0 (проверено), заполнено
    # у 1 пользователя из 45. Имя совпадает с тем, что дал бы PostgreSQL
    # по умолчанию, — иначе autogenerate увидит расхождение.
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conname = 'users_premium_granted_by_fkey'
                   AND conrelid = 'users'::regclass
            ) THEN
                ALTER TABLE users
                    ADD CONSTRAINT users_premium_granted_by_fkey
                    FOREIGN KEY (premium_granted_by) REFERENCES users(id);
            END IF;
        END $$;
    """)

def downgrade():
    # ── Индексы: снимаем созданные в upgrade ──────────────────────────
    op.execute("DROP INDEX IF EXISTS ix_crm_activities_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_activities_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_companies_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_companies_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_contacts_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_contacts_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_deal_products_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_deals_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_deals_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_integrations_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_leads_bitrix_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_leads_id")
    op.execute("DROP INDEX IF EXISTS ix_crm_recordings_id")
    op.execute("DROP INDEX IF EXISTS ix_organizations_id")
    op.execute("DROP INDEX IF EXISTS ix_seller_passports_user_id")
    op.execute("DROP INDEX IF EXISTS ix_team_invitations_token")
    op.execute("DROP INDEX IF EXISTS ix_training_conversion_metrics_metric_date")
    op.execute("DROP INDEX IF EXISTS ix_training_errors_corrections_conversation_id")
    op.execute("DROP INDEX IF EXISTS ix_training_errors_corrections_detected_at")
    op.execute("DROP INDEX IF EXISTS ix_training_errors_corrections_message_id")
    op.execute("DROP INDEX IF EXISTS ix_users_google_id")
    op.execute("DROP INDEX IF EXISTS ix_voice_training_messages_id")

    # ── Индексы: возвращаем снесённые, в исходном виде ────────────────
    # Без этого цепочка вниз рвётся: downgrade 017 безусловно дропает
    # ix_prompts_*, а upgrade 023 их снял — 'index does not exist'.
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_crm_activities_bitrix_id ON public.crm_activities USING btree (bitrix_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_crm_companies_bitrix_id ON public.crm_companies USING btree (bitrix_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_crm_contacts_bitrix_id ON public.crm_contacts USING btree (bitrix_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_crm_deals_bitrix_id ON public.crm_deals USING btree (bitrix_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_crm_leads_bitrix_id ON public.crm_leads USING btree (bitrix_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_recordings_conversation_id ON public.crm_recordings USING btree (conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_recordings_sync_status ON public.crm_recordings USING btree (sync_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_prompts_created_by ON public.prompts USING btree (created_by)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_prompts_is_active ON public.prompts USING btree (is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_prompts_name ON public.prompts USING btree (name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_seller_passports_user_id ON public.seller_passports USING btree (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_training_conversion_metrics_date ON public.training_conversion_metrics USING btree (metric_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_role ON public.users USING btree (role)")

    # ── Констрейнты (не индексы — возвращаем через ADD CONSTRAINT) ────
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'seller_passports_user_id_key') THEN
                ALTER TABLE seller_passports ADD CONSTRAINT seller_passports_user_id_key UNIQUE (user_id);
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_team_invitations_token') THEN
                ALTER TABLE team_invitations ADD CONSTRAINT uq_team_invitations_token UNIQUE (token);
            END IF;
        END $$;
    """)

    # FK и констрейнты
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_premium_granted_by_fkey")

    # Колонки users.google_* — возвращаем в форме из 017,
    # включая server_default='false' для is_google_user.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_access_token TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_refresh_token TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_token_expires_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_google_user BOOLEAN DEFAULT false")

    # batch_id обратно в varchar(100)
    op.execute("ALTER TABLE crm_recordings ALTER COLUMN batch_id TYPE VARCHAR(100)")

    # Индексы из п.3b (ix_users_organization_id, ix_teams_organization_id,
    # ix_crm_recordings_batch_id) НЕ снимаются намеренно. На чистой базе их
    # создали 008 и 018, то есть после 022 они уже существовали, и снятие
    # сломало бы инверс: downgrade 018 дропает ix_teams_organization_id
    # безусловно. На проде их после 022 не было, поэтому там они переживут
    # откат лишними — но они совпадают с моделями и безвредны, а откат
    # прода ниже 022 запрещён (см. докстринг 022).

    # Колонки, добавленные в п.5
    op.execute("ALTER TABLE voice_training_messages DROP COLUMN IF EXISTS duration_seconds")
    op.execute("ALTER TABLE zoom_meetings DROP COLUMN IF EXISTS agent_active")

    # DROP NOT NULL по всем 70 — иначе downgrade base -> upgrade head
    # (шаг 5 верификации) не пройдёт.
    # action_patterns
    op.execute("ALTER TABLE action_patterns ALTER COLUMN created_at DROP NOT NULL")
    op.execute("ALTER TABLE action_patterns ALTER COLUMN updated_at DROP NOT NULL")
    # analytics_messages
    op.execute("ALTER TABLE analytics_messages ALTER COLUMN created_at DROP NOT NULL")
    # crm_activities
    op.execute("ALTER TABLE crm_activities ALTER COLUMN synced_at DROP NOT NULL")
    # crm_companies
    op.execute("ALTER TABLE crm_companies ALTER COLUMN synced_at DROP NOT NULL")
    # crm_contacts
    op.execute("ALTER TABLE crm_contacts ALTER COLUMN synced_at DROP NOT NULL")
    # crm_deals
    op.execute("ALTER TABLE crm_deals ALTER COLUMN synced_at DROP NOT NULL")
    # crm_integrations
    op.execute("ALTER TABLE crm_integrations ALTER COLUMN recordings_count DROP NOT NULL")
    op.execute("ALTER TABLE crm_integrations ALTER COLUMN analyzed_count DROP NOT NULL")
    op.execute("ALTER TABLE crm_integrations ALTER COLUMN created_at DROP NOT NULL")
    # crm_leads
    op.execute("ALTER TABLE crm_leads ALTER COLUMN synced_at DROP NOT NULL")
    # crm_manager_mappings
    op.execute("ALTER TABLE crm_manager_mappings ALTER COLUMN created_at DROP NOT NULL")
    # crm_recordings
    op.execute("ALTER TABLE crm_recordings ALTER COLUMN duration_seconds DROP NOT NULL")
    op.execute("ALTER TABLE crm_recordings ALTER COLUMN direction DROP NOT NULL")
    op.execute("ALTER TABLE crm_recordings ALTER COLUMN sync_status DROP NOT NULL")
    op.execute("ALTER TABLE crm_recordings ALTER COLUMN created_at DROP NOT NULL")
    # custom_meeting_transcripts
    op.execute("ALTER TABLE custom_meeting_transcripts ALTER COLUMN content DROP NOT NULL")
    op.execute("ALTER TABLE custom_meeting_transcripts ALTER COLUMN created_at DROP NOT NULL")
    # custom_meetings
    op.execute("ALTER TABLE custom_meetings ALTER COLUMN meeting_id DROP NOT NULL")
    op.execute("ALTER TABLE custom_meetings ALTER COLUMN status DROP NOT NULL")
    op.execute("ALTER TABLE custom_meetings ALTER COLUMN max_participants DROP NOT NULL")
    op.execute("ALTER TABLE custom_meetings ALTER COLUMN duration_minutes DROP NOT NULL")
    op.execute("ALTER TABLE custom_meetings ALTER COLUMN ai_agent_enabled DROP NOT NULL")
    op.execute("ALTER TABLE custom_meetings ALTER COLUMN created_at DROP NOT NULL")
    # manager_actions
    op.execute("ALTER TABLE manager_actions ALTER COLUMN action_type DROP NOT NULL")
    op.execute("ALTER TABLE manager_actions ALTER COLUMN created_at DROP NOT NULL")
    # meeting_participants
    op.execute("ALTER TABLE meeting_participants ALTER COLUMN joined_at DROP NOT NULL")
    op.execute("ALTER TABLE meeting_participants ALTER COLUMN role DROP NOT NULL")
    # meeting_transcripts
    op.execute("ALTER TABLE meeting_transcripts ALTER COLUMN full_transcript DROP NOT NULL")
    op.execute("ALTER TABLE meeting_transcripts ALTER COLUMN summary DROP NOT NULL")
    op.execute("ALTER TABLE meeting_transcripts ALTER COLUMN participants_count DROP NOT NULL")
    op.execute("ALTER TABLE meeting_transcripts ALTER COLUMN duration_seconds DROP NOT NULL")
    op.execute("ALTER TABLE meeting_transcripts ALTER COLUMN created_at DROP NOT NULL")
    # notifications
    op.execute("ALTER TABLE notifications ALTER COLUMN icon DROP NOT NULL")
    op.execute("ALTER TABLE notifications ALTER COLUMN created_at DROP NOT NULL")
    # organizations
    op.execute("ALTER TABLE organizations ALTER COLUMN created_at DROP NOT NULL")
    # parameter_definitions
    op.execute("ALTER TABLE parameter_definitions ALTER COLUMN created_at DROP NOT NULL")
    # parameter_values
    op.execute("ALTER TABLE parameter_values ALTER COLUMN confidence DROP NOT NULL")
    op.execute("ALTER TABLE parameter_values ALTER COLUMN created_at DROP NOT NULL")
    # passport_snapshots
    op.execute("ALTER TABLE passport_snapshots ALTER COLUMN created_at DROP NOT NULL")
    # prompts
    op.execute("ALTER TABLE prompts ALTER COLUMN created_at DROP NOT NULL")
    # research_logs
    op.execute("ALTER TABLE research_logs ALTER COLUMN created_at DROP NOT NULL")
    # seller_passports
    op.execute("ALTER TABLE seller_passports ALTER COLUMN last_updated_at DROP NOT NULL")
    op.execute("ALTER TABLE seller_passports ALTER COLUMN created_at DROP NOT NULL")
    # team_invitations
    op.execute("ALTER TABLE team_invitations ALTER COLUMN created_at DROP NOT NULL")
    # team_scripts
    op.execute("ALTER TABLE team_scripts ALTER COLUMN created_at DROP NOT NULL")
    op.execute("ALTER TABLE team_scripts ALTER COLUMN updated_at DROP NOT NULL")
    # training_conversion_metrics
    op.execute("ALTER TABLE training_conversion_metrics ALTER COLUMN period_type DROP NOT NULL")
    op.execute("ALTER TABLE training_conversion_metrics ALTER COLUMN total_plans DROP NOT NULL")
    op.execute("ALTER TABLE training_conversion_metrics ALTER COLUMN active_plans DROP NOT NULL")
    op.execute("ALTER TABLE training_conversion_metrics ALTER COLUMN completed_plans DROP NOT NULL")
    op.execute("ALTER TABLE training_conversion_metrics ALTER COLUMN total_trainings DROP NOT NULL")
    op.execute("ALTER TABLE training_conversion_metrics ALTER COLUMN completed_trainings DROP NOT NULL")
    op.execute("ALTER TABLE training_conversion_metrics ALTER COLUMN created_at DROP NOT NULL")
    # training_errors_corrections
    op.execute("ALTER TABLE training_errors_corrections ALTER COLUMN error_severity DROP NOT NULL")
    op.execute("ALTER TABLE training_errors_corrections ALTER COLUMN correction_applied DROP NOT NULL")
    op.execute("ALTER TABLE training_errors_corrections ALTER COLUMN created_at DROP NOT NULL")
    # training_programs
    op.execute("ALTER TABLE training_programs ALTER COLUMN start_date DROP NOT NULL")
    op.execute("ALTER TABLE training_programs ALTER COLUMN created_at DROP NOT NULL")
    # training_sessions
    op.execute("ALTER TABLE training_sessions ALTER COLUMN session_type DROP NOT NULL")
    op.execute("ALTER TABLE training_sessions ALTER COLUMN status DROP NOT NULL")
    # users
    op.execute("ALTER TABLE users ALTER COLUMN role DROP NOT NULL")
    # voice_training_messages
    op.execute("ALTER TABLE voice_training_messages ALTER COLUMN timestamp DROP NOT NULL")
    # zoom_meetings
    op.execute("ALTER TABLE zoom_meetings ALTER COLUMN topic DROP NOT NULL")
    op.execute("ALTER TABLE zoom_meetings ALTER COLUMN start_time DROP NOT NULL")
    op.execute("ALTER TABLE zoom_meetings ALTER COLUMN duration_minutes DROP NOT NULL")
    op.execute("ALTER TABLE zoom_meetings ALTER COLUMN status DROP NOT NULL")
    op.execute("ALTER TABLE zoom_meetings ALTER COLUMN join_url DROP NOT NULL")
    op.execute("ALTER TABLE zoom_meetings ALTER COLUMN ai_agent_enabled DROP NOT NULL")
    op.execute("ALTER TABLE zoom_meetings ALTER COLUMN created_at DROP NOT NULL")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(10)")
