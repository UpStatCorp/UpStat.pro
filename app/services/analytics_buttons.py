"""
Каталог кнопок для навигации в чате аналитики.
2 уровня: блоки параметров -> вопросы.
"""

BLOCKS = {
    "main_menu": {
        "label": "Главная",
        "parent": None,
        "buttons": [
            {"id": "contact_establishment", "label": "🤝 Контакт", "full": "Установление контакта"},
            {"id": "structure", "label": "📐 Структура", "full": "Структура разговора"},
            {"id": "needs_discovery", "label": "🔍 Потребности", "full": "Выявление потребностей"},
            {"id": "presentation", "label": "🎯 Презентация", "full": "Презентация решения"},
            {"id": "objections", "label": "💬 Возражения", "full": "Работа с возражениями"},
            {"id": "closing", "label": "✅ Закрытие", "full": "Закрытие и следующий шаг"},
            {"id": "general_behavior", "label": "🎭 Поведение", "full": "Общее поведение менеджера"},
        ],
    },

    # ── БЛОК 0: Установление контакта ──
    "contact_establishment": {
        "label": "🤝 Установление контакта",
        "parent": "main_menu",
        "questions": [
            {
                "id": "q_greeting_quality",
                "short": "Качество приветствия",
                "full": "Средний показатель качества приветствия по команде",
                "params": ["greeting_quality"],
                "query_type": "avg_number",
            },
            {
                "id": "q_rapport",
                "short": "Раппорт установлен?",
                "full": "Процент звонков, где установлено открытое общение с клиентом",
                "params": ["rapport_established"],
                "query_type": "percent_true",
            },
            {
                "id": "q_opening_clarity",
                "short": "Ясность цели звонка",
                "full": "Процент звонков с чёткой целью и объяснением причины",
                "params": ["opening_clarity"],
                "query_type": "percent_true",
            },
            {
                "id": "q_permission",
                "short": "Разрешение получено?",
                "full": "Процент звонков, где клиент дал разрешение на разговор",
                "params": ["permission_granted"],
                "query_type": "percent_true",
            },
            {
                "id": "q_resistance",
                "short": "Сопротивление в начале",
                "full": "Процент звонков с сопротивлением клиента в начале",
                "params": ["resistance_at_start"],
                "query_type": "percent_true",
            },
            {
                "id": "q_contact_rating",
                "short": "Рейтинг менеджеров",
                "full": "Рейтинг менеджеров по качеству приветствия",
                "params": ["greeting_quality"],
                "query_type": "rating",
            },
        ],
    },

    # ── БЛОК 1: Структура ──
    "structure": {
        "label": "📐 Структура разговора",
        "parent": "main_menu",
        "questions": [
            {
                "id": "q_structure_followed",
                "short": "Соблюдена структура?",
                "full": "Процент звонков, где соблюдена структура разговора",
                "params": ["structure_followed"],
                "query_type": "percent_true",
            },
            {
                "id": "q_stage_sequence",
                "short": "Правильный порядок?",
                "full": "Процент звонков с правильной последовательностью этапов",
                "params": ["stage_sequence_correct"],
                "query_type": "percent_true",
            },
            {
                "id": "q_stage_completion",
                "short": "Завершённость этапов",
                "full": "Средний процент завершённых этапов разговора",
                "params": ["stage_completion_rate"],
                "query_type": "avg_number",
            },
            {
                "id": "q_clarity_of_flow",
                "short": "Ясность хода",
                "full": "Средняя оценка ясности хода разговора",
                "params": ["clarity_of_flow"],
                "query_type": "avg_number",
            },
            {
                "id": "q_chaos_flag",
                "short": "Хаос в разговоре",
                "full": "Процент звонков с хаотичным разговором",
                "params": ["chaos_flag"],
                "query_type": "percent_true",
            },
            {
                "id": "q_structure_rating",
                "short": "Рейтинг менеджеров",
                "full": "Рейтинг менеджеров по завершённости этапов",
                "params": ["stage_completion_rate"],
                "query_type": "rating",
            },
        ],
    },

    # ── БЛОК 2: Выявление потребностей ──
    "needs_discovery": {
        "label": "🔍 Выявление потребностей",
        "parent": "main_menu",
        "questions": [
            {
                "id": "q_questions_count",
                "short": "Кол-во вопросов",
                "full": "Среднее количество вопросов менеджера за звонок",
                "params": ["manager_questions_count"],
                "query_type": "avg_number",
            },
            {
                "id": "q_open_questions",
                "short": "Доля открытых вопросов",
                "full": "Средний процент открытых вопросов (как/почему/что)",
                "params": ["open_questions_ratio"],
                "query_type": "avg_number",
            },
            {
                "id": "q_needs_identified",
                "short": "Потребности выявлены?",
                "full": "Процент звонков, где потребности клиента выявлены",
                "params": ["needs_identified"],
                "query_type": "percent_true",
            },
            {
                "id": "q_listening_quality",
                "short": "Качество слушания",
                "full": "Средняя оценка качества слушания менеджером",
                "params": ["listening_quality"],
                "query_type": "avg_number",
            },
            {
                "id": "q_interruptions",
                "short": "Перебивания",
                "full": "Среднее количество перебиваний клиента менеджером",
                "params": ["manager_interruptions"],
                "query_type": "avg_number",
            },
            {
                "id": "q_needs_rating",
                "short": "Рейтинг менеджеров",
                "full": "Рейтинг менеджеров по количеству вопросов",
                "params": ["manager_questions_count"],
                "query_type": "rating",
            },
        ],
    },

    # ── БЛОК 3: Презентация ──
    "presentation": {
        "label": "🎯 Презентация",
        "parent": "main_menu",
        "questions": [
            {
                "id": "q_benefits_ratio",
                "short": "Функции vs выгоды",
                "full": "Среднее соотношение функций и выгод в презентации",
                "params": ["features_vs_benefits_ratio"],
                "query_type": "avg_number",
            },
            {
                "id": "q_benefits_presented",
                "short": "Выгоды представлены?",
                "full": "Процент звонков, где менеджер представил выгоды",
                "params": ["benefits_presented"],
                "query_type": "percent_true",
            },
            {
                "id": "q_personalization",
                "short": "Персонализация?",
                "full": "Процент звонков с персонализацией выгод под клиента",
                "params": ["benefit_personalization"],
                "query_type": "percent_true",
            },
            {
                "id": "q_value_linked",
                "short": "Связка с потребностями?",
                "full": "Процент звонков, где ценность увязана с потребностями",
                "params": ["value_linked_to_needs"],
                "query_type": "percent_true",
            },
            {
                "id": "q_overload",
                "short": "Перегруз информацией",
                "full": "Процент звонков с перегрузом информацией",
                "params": ["overload_of_information"],
                "query_type": "percent_true",
            },
            {
                "id": "q_client_interest",
                "short": "Интерес клиента",
                "full": "Процент звонков с интересом клиента при презентации",
                "params": ["client_interest_during_presentation"],
                "query_type": "percent_true",
            },
            {
                "id": "q_presentation_rating",
                "short": "Рейтинг менеджеров",
                "full": "Рейтинг менеджеров по соотношению функций/выгод",
                "params": ["features_vs_benefits_ratio"],
                "query_type": "rating",
            },
        ],
    },

    # ── БЛОК 4: Возражения ──
    "objections": {
        "label": "💬 Возражения",
        "parent": "main_menu",
        "questions": [
            {
                "id": "q_objections_count",
                "short": "Среднее кол-во",
                "full": "Среднее количество возражений клиента за звонок",
                "params": ["objections_count"],
                "query_type": "avg_number",
            },
            {
                "id": "q_objection_handled",
                "short": "% обработанных",
                "full": "Средний процент обработанных возражений",
                "params": ["objection_handled"],
                "query_type": "avg_number",
            },
            {
                "id": "q_handling_quality",
                "short": "Качество обработки",
                "full": "Средняя оценка качества обработки возражений",
                "params": ["handling_quality"],
                "query_type": "avg_number",
            },
            {
                "id": "q_objection_ignored",
                "short": "Проигнорированные",
                "full": "Процент звонков, где возражение проигнорировано",
                "params": ["objection_ignored"],
                "query_type": "percent_true",
            },
            {
                "id": "q_defensive",
                "short": "Защитное поведение",
                "full": "Процент звонков с защитным поведением менеджера",
                "params": ["defensive_behavior"],
                "query_type": "percent_true",
            },
            {
                "id": "q_objections_rating",
                "short": "Рейтинг менеджеров",
                "full": "Рейтинг менеджеров по проценту обработанных возражений",
                "params": ["objection_handled"],
                "query_type": "rating",
            },
        ],
    },

    # ── БЛОК 5: Закрытие ──
    "closing": {
        "label": "✅ Закрытие",
        "parent": "main_menu",
        "questions": [
            {
                "id": "q_closing_attempt",
                "short": "Попытка закрытия?",
                "full": "Процент звонков с попыткой закрытия сделки",
                "params": ["closing_attempt"],
                "query_type": "percent_true",
            },
            {
                "id": "q_next_step_defined",
                "short": "Следующий шаг?",
                "full": "Процент звонков, где определён следующий шаг",
                "params": ["next_step_defined"],
                "query_type": "percent_true",
            },
            {
                "id": "q_next_step_confirmed",
                "short": "Шаг подтверждён?",
                "full": "Процент звонков, где следующий шаг подтверждён клиентом",
                "params": ["next_step_confirmed"],
                "query_type": "percent_true",
            },
            {
                "id": "q_commitment",
                "short": "Обязательства клиента",
                "full": "Процент звонков, где клиент взял обязательства",
                "params": ["client_commitment"],
                "query_type": "percent_true",
            },
            {
                "id": "q_urgency",
                "short": "Срочность создана?",
                "full": "Процент звонков с созданной срочностью решения",
                "params": ["urgency_created"],
                "query_type": "percent_true",
            },
            {
                "id": "q_deal_momentum",
                "short": "Импульс сделки",
                "full": "Средняя оценка импульса (momentum) сделки",
                "params": ["deal_momentum"],
                "query_type": "avg_number",
            },
            {
                "id": "q_closing_rating",
                "short": "Рейтинг менеджеров",
                "full": "Рейтинг менеджеров по фиксации следующего шага",
                "params": ["next_step_defined"],
                "query_type": "rating_bool",
            },
        ],
    },

    # ── БЛОК 6: Общее поведение ──
    "general_behavior": {
        "label": "🎭 Общее поведение",
        "parent": "main_menu",
        "questions": [
            {
                "id": "q_talk_ratio",
                "short": "Talk ratio",
                "full": "Средний процент речи менеджера (talk-to-listen ratio)",
                "params": ["talk_listen_ratio"],
                "query_type": "avg_number",
            },
            {
                "id": "q_confidence",
                "short": "Уверенность",
                "full": "Средняя оценка уверенности менеджера",
                "params": ["confidence"],
                "query_type": "avg_number",
            },
            {
                "id": "q_filler_words",
                "short": "Слова-паразиты",
                "full": "Среднее количество слов-паразитов за звонок",
                "params": ["filler_words"],
                "query_type": "avg_number",
            },
            {
                "id": "q_empathy",
                "short": "Взаимопонимание",
                "full": "Средняя оценка взаимопонимания (эмпатии)",
                "params": ["empathy"],
                "query_type": "avg_number",
            },
            {
                "id": "q_over_talking",
                "short": "Чрезмерная речь",
                "full": "Процент звонков, где менеджер говорил слишком много",
                "params": ["over_talking"],
                "query_type": "percent_true",
            },
            {
                "id": "q_pressure",
                "short": "Давление на клиента",
                "full": "Процент звонков с давлением на клиента",
                "params": ["pressure_behavior"],
                "query_type": "percent_true",
            },
            {
                "id": "q_critical_error",
                "short": "Критические ошибки",
                "full": "Процент звонков с критическими ошибками",
                "params": ["critical_error"],
                "query_type": "percent_true",
            },
            {
                "id": "q_behavior_rating",
                "short": "Рейтинг менеджеров",
                "full": "Рейтинг менеджеров по уверенности",
                "params": ["confidence"],
                "query_type": "rating",
            },
        ],
    },
}


def get_context(context_id: str) -> dict:
    """Возвращает блок навигации по ID."""
    return BLOCKS.get(context_id, BLOCKS["main_menu"])


def find_question(question_id: str) -> tuple:
    """Находит вопрос по ID. Возвращает (block_id, question_dict) или (None, None)."""
    for block_id, block in BLOCKS.items():
        for q in block.get("questions", []):
            if q["id"] == question_id:
                return block_id, q
    return None, None
