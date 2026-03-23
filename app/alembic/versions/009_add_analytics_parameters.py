"""Add 54 new analytics parameters to parameter_definitions

Revision ID: 009
Revises: 008
Create Date: 2026-03-23

"""
from alembic import op


revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO parameter_definitions (code, title, description, value_type, category, unit) VALUES
        -- БЛОК 0: contact_establishment
        ('greeting_quality', 'Качество приветствия', 'Оценка качества приветствия 0-100', 'number', 'contact_establishment', '%%'),
        ('rapport_established', 'Открытое общение клиента установлено', 'Клиент отвечает развёрнуто, нет отказа', 'boolean', 'contact_establishment', NULL),
        ('client_engagement_start', 'Вовлечённость клиента в начале', 'Длина ответа клиента в начале звонка (слов)', 'number', 'contact_establishment', 'слов'),
        ('opening_clarity', 'Ясность цели звонка', 'Есть цель звонка и объяснение причины', 'boolean', 'contact_establishment', NULL),
        ('permission_granted', 'Разрешение на разговор', 'Клиент дал разрешение («да, давайте», «можно»)', 'boolean', 'contact_establishment', NULL),
        ('trust_signal_start', 'Сигнал доверия в начале', 'Признаки доверия и вовлечённости в начале', 'boolean', 'contact_establishment', NULL),
        ('resistance_at_start', 'Сопротивление в начале', 'Клиент выразил сопротивление («мне не интересно», «не сейчас»)', 'boolean', 'contact_establishment', NULL),
        -- БЛОК 1: structure
        ('structure_followed', 'Соблюдена структура', 'Соблюдена последовательность блоков разговора', 'boolean', 'structure', NULL),
        ('stage_sequence_correct', 'Правильная последовательность этапов', 'Этапы идут в правильном порядке', 'boolean', 'structure', NULL),
        ('conversation_control', 'Контроль разговора', 'Кто ведёт: manager / client / balanced', 'text', 'structure', NULL),
        ('clarity_of_flow', 'Ясность хода разговора', 'Оценка ясности 0-100', 'number', 'structure', '%%'),
        ('stage_completion_rate', 'Процент завершённых этапов', 'Доля завершённых этапов 0-100', 'number', 'structure', '%%'),
        ('chaos_flag', 'Хаос в разговоре', 'Признаки хаотичности разговора', 'boolean', 'structure', NULL),
        -- БЛОК 2: needs_discovery
        ('open_questions_ratio', 'Доля открытых вопросов', 'Процент открытых вопросов (как/почему/что) 0-100', 'number', 'needs_discovery', '%%'),
        ('depth_of_questions', 'Глубина вопросов', 'Оценка глубины вопросов 0-100', 'number', 'needs_discovery', '%%'),
        ('needs_identified', 'Потребности выявлены', 'Менеджер выявил потребности клиента', 'boolean', 'needs_discovery', NULL),
        ('needs_clarity', 'Чёткость понимания потребностей', 'Оценка чёткости 0-100', 'number', 'needs_discovery', '%%'),
        ('client_engagement', 'Вовлечённость клиента', 'Оценка вовлечённости клиента 0-100', 'number', 'needs_discovery', '%%'),
        ('manager_interruptions', 'Перебивания менеджером', 'Количество перебиваний клиента менеджером', 'number', 'needs_discovery', 'шт'),
        ('listening_quality', 'Качество слушания', 'Оценка качества слушания 0-100', 'number', 'needs_discovery', '%%'),
        -- БЛОК 3: presentation
        ('features_vs_benefits_ratio', 'Соотношение функции/выгоды', 'Доля выгод в презентации 0-100', 'number', 'presentation', '%%'),
        ('benefits_presented', 'Выгоды представлены', 'Менеджер представил выгоды', 'boolean', 'presentation', NULL),
        ('benefit_clarity', 'Ясность выгод', 'Оценка ясности выгод 0-100', 'number', 'presentation', '%%'),
        ('benefit_personalization', 'Персонализация выгод', 'Выгоды адаптированы под клиента', 'boolean', 'presentation', NULL),
        ('value_linked_to_needs', 'Связка ценности с потребностями', 'Ценность увязана с потребностями клиента', 'boolean', 'presentation', NULL),
        ('client_interest_during_presentation', 'Интерес клиента при презентации', 'Клиент проявлял интерес во время презентации', 'boolean', 'presentation', NULL),
        ('client_questions', 'Вопросы клиента', 'Количество вопросов клиента', 'number', 'presentation', 'шт'),
        ('overload_of_information', 'Перегруз информацией', 'Слишком много информации за раз', 'boolean', 'presentation', NULL),
        ('clarity_of_explanation', 'Ясность объяснения', 'Оценка ясности 0-100', 'number', 'presentation', '%%'),
        ('emotional_impact', 'Эмоциональный эффект', 'Оценка эмоционального эффекта 0-100', 'number', 'presentation', '%%'),
        ('value_confirmation', 'Подтверждение ценности', 'Клиент подтвердил ценность предложения', 'boolean', 'presentation', NULL),
        -- БЛОК 4: objections
        ('objection_types', 'Типы возражений', 'JSON: ["дорого","подумаю","не сейчас"] и др.', 'text', 'objections', NULL),
        ('objection_handled', 'Процент обработанных возражений', 'Доля обработанных возражений 0-100', 'number', 'objections', '%%'),
        ('handling_quality', 'Качество обработки', 'Оценка качества обработки возражений 0-100', 'number', 'objections', '%%'),
        ('objection_ignored', 'Возражение проигнорировано', 'Менеджер проигнорировал возражение', 'boolean', 'objections', NULL),
        ('defensive_behavior', 'Защитное поведение', 'Менеджер вёл себя защищаясь', 'boolean', 'objections', NULL),
        ('objection_reframed', 'Возражение переформулировано', 'Возражение было переформулировано', 'boolean', 'objections', NULL),
        ('client_reaction', 'Реакция клиента', 'Оценка реакции клиента после ответа 0-100', 'number', 'objections', '%%'),
        -- БЛОК 5: closing
        ('closing_attempt', 'Попытка закрытия', 'Была попытка закрыть сделку', 'boolean', 'closing', NULL),
        ('closing_timing', 'Тайминг закрытия', 'Оценка тайминга закрытия 0-100', 'number', 'closing', '%%'),
        ('next_step_defined', 'Следующий шаг определён', 'Определён следующий шаг', 'boolean', 'closing', NULL),
        ('next_step_confirmed', 'Следующий шаг подтверждён', 'Следующий шаг подтверждён клиентом', 'boolean', 'closing', NULL),
        ('client_commitment', 'Обязательства клиента', 'Клиент взял обязательства', 'boolean', 'closing', NULL),
        ('urgency_created', 'Срочность создана', 'Создана срочность принятия решения', 'boolean', 'closing', NULL),
        ('deal_momentum', 'Импульс сделки', 'Оценка импульса сделки 0-100', 'number', 'closing', '%%'),
        -- БЛОК 6: general_behavior
        ('filler_words', 'Слова-паразиты', 'Количество слов-паразитов', 'number', 'general_behavior', 'шт'),
        ('confidence', 'Уверенность', 'Оценка уверенности менеджера 0-100', 'number', 'general_behavior', '%%'),
        ('speech_clarity', 'Чёткость речи', 'Оценка чёткости речи 0-100', 'number', 'general_behavior', '%%'),
        ('over_talking', 'Чрезмерная речь', 'Менеджер говорил слишком много', 'boolean', 'general_behavior', NULL),
        ('pressure_behavior', 'Давление на клиента', 'Признаки давления на клиента', 'boolean', 'general_behavior', NULL),
        ('empathy', 'Взаимопонимание', 'Оценка взаимопонимания 0-100', 'number', 'general_behavior', '%%'),
        ('value_quality', 'Качество ценности', 'Оценка качества передачи ценности 0-100', 'number', 'general_behavior', '%%'),
        ('critical_error', 'Критическая ошибка', 'Допущена критическая ошибка', 'boolean', 'general_behavior', NULL),
        ('strong_moment', 'Сильный момент', 'Был сильный момент в разговоре', 'boolean', 'general_behavior', NULL)
    """)


def downgrade():
    op.execute("""
        DELETE FROM parameter_definitions WHERE code IN (
            'greeting_quality', 'rapport_established', 'client_engagement_start', 'opening_clarity',
            'permission_granted', 'trust_signal_start', 'resistance_at_start',
            'structure_followed', 'stage_sequence_correct', 'conversation_control', 'clarity_of_flow',
            'stage_completion_rate', 'chaos_flag',
            'open_questions_ratio', 'depth_of_questions', 'needs_identified', 'needs_clarity',
            'client_engagement', 'manager_interruptions', 'listening_quality',
            'features_vs_benefits_ratio', 'benefits_presented', 'benefit_clarity', 'benefit_personalization',
            'value_linked_to_needs', 'client_interest_during_presentation', 'client_questions',
            'overload_of_information', 'clarity_of_explanation', 'emotional_impact', 'value_confirmation',
            'objection_types', 'objection_handled', 'handling_quality', 'objection_ignored',
            'defensive_behavior', 'objection_reframed', 'client_reaction',
            'closing_attempt', 'closing_timing', 'next_step_defined', 'next_step_confirmed',
            'client_commitment', 'urgency_created', 'deal_momentum',
            'filler_words', 'confidence', 'speech_clarity', 'over_talking', 'pressure_behavior',
            'empathy', 'value_quality', 'critical_error', 'strong_moment'
        )
    """)
