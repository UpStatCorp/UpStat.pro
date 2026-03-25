"""
Сервис маскирования персональных данных (PII) в тексте транскрипций.

Заменяет телефоны, e-mail, URL, ИНН/КПП/ОГРН, номера карт/паспортов,
имена людей и названия компаний на единые плейсхолдеры:
  [PHONE], [EMAIL], [URL], [INN], [KPP], [OGRN], [PASSPORT],
  [CARD], [SNILS], [PERSON], [COMPANY], [ADDRESS]
"""

import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_PHONE_PATTERNS = [
    # +7 (999) 123-45-67  /  8 999 123 45 67  /  +7-999-123-45-67
    re.compile(
        r'(?<!\d)'
        r'(?:\+7|8)[\s\-]*'
        r'[\(\s]?\d{3}[\)\s]?[\s\-]*'
        r'\d{3}[\s\-]*\d{2}[\s\-]*\d{2}'
        r'(?!\d)'
    ),
    # краткие 10-11 цифр подряд (без +7/8): 9991234567
    re.compile(r'(?<!\d)\d{10,11}(?!\d)'),
]

_EMAIL_PATTERN = re.compile(
    r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}',
    re.IGNORECASE,
)

_URL_PATTERN = re.compile(
    r'(?:https?://|www\.)[^\s<>\"\']+',
    re.IGNORECASE,
)

# ИНН (10 или 12 цифр), КПП (9 цифр), ОГРН/ОГРНИП (13/15 цифр)
_INN_PATTERN = re.compile(r'\bИНН[\s:]*(\d{10,12})\b', re.IGNORECASE)
_KPP_PATTERN = re.compile(r'\bКПП[\s:]*(\d{9})\b', re.IGNORECASE)
_OGRN_PATTERN = re.compile(r'\bОГРН(?:ИП)?[\s:]*(\d{13,15})\b', re.IGNORECASE)

# Банковская карта (16 цифр через пробелы/дефисы)
_CARD_PATTERN = re.compile(
    r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b'
)

# СНИЛС: 123-456-789 01
_SNILS_PATTERN = re.compile(
    r'\b\d{3}[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{2}\b'
)

# Паспорт: серия 1234 номер 567890 или 12 34 567890
_PASSPORT_PATTERN = re.compile(
    r'\b\d{2}\s?\d{2}\s?\d{6}\b'
)

# Юридические формы (ООО, ОАО, ПАО, ЗАО, АО, ИП) + 1-4 слова после
_COMPANY_PATTERN = re.compile(
    r'\b(?:ООО|ОАО|ПАО|ЗАО|АО|ИП|НКО|ТОО|ФГУП|МУП)'
    r'[\s\«\"\']*'
    r'(?:[А-ЯЁA-Z][а-яёa-z\-]+[\s]*){1,4}'
    r'[\»\"\']*',
    re.UNICODE,
)

# Русское полное имя: Фамилия Имя (Отчество)
# Минимум 2 слова с заглавной буквы подряд, каждое >= 3 букв
_PERSON_PATTERN = re.compile(
    r'\b([А-ЯЁ][а-яё]{2,})[\s]+([А-ЯЁ][а-яё]{2,})'
    r'(?:[\s]+([А-ЯЁ][а-яё]{2,}(?:вич|вна|ич|тич|рич|ьич|еевич|еевна|овна|ович|ьевич|ьевна)))?\b',
    re.UNICODE,
)

# Типичные почтовые адреса: ул./пр./г./д./кв./пер./наб.
_ADDRESS_PATTERN = re.compile(
    r'(?:ул\.|улица|пр\.|проспект|пер\.|переулок|наб\.|набережная|'
    r'бул\.|бульвар|ш\.|шоссе|пл\.|площадь)'
    r'[\s]*[А-ЯЁа-яё\.\-\s\d,/]+(?:д\.|дом|кв\.|квартира|стр\.|корп\.|к\.)[\s]*[\d/\-а-яА-Я]*',
    re.IGNORECASE | re.UNICODE,
)

# Слова-исключения: распространённые слова, которые regex может ошибочно
# принять за имена (Например, "Добрый день", "Большое спасибо")
_PERSON_EXCLUSIONS = {
    "добрый", "доброе", "добрая", "большое", "большая", "большой",
    "хорошо", "конечно", "спасибо", "пожалуйста", "здравствуйте",
    "подскажите", "расскажите", "скажите", "извините", "простите",
    "минуту", "секунду", "момент", "правильно", "отлично",
    "давайте", "послушайте", "смотрите", "получается",
}


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def redact_pii(text: str) -> str:
    """
    Заменяет персональные данные в тексте на плейсхолдеры.
    Порядок замен важен: сначала более специфичные паттерны,
    потом более общие.
    """
    if not text or not text.strip():
        return text

    result = text

    # 1) E-mail (до URL, чтобы не «съесть» адрес)
    result = _EMAIL_PATTERN.sub("[EMAIL]", result)

    # 2) URL
    result = _URL_PATTERN.sub("[URL]", result)

    # 3) ИНН / КПП / ОГРН (до телефонов, чтобы не спутать с числами)
    result = _INN_PATTERN.sub("[INN]", result)
    result = _KPP_PATTERN.sub("[KPP]", result)
    result = _OGRN_PATTERN.sub("[OGRN]", result)

    # 4) Банковские карты
    result = _CARD_PATTERN.sub("[CARD]", result)

    # 5) СНИЛС
    result = _SNILS_PATTERN.sub("[SNILS]", result)

    # 6) Паспорт
    result = _PASSPORT_PATTERN.sub("[PASSPORT]", result)

    # 7) Телефоны
    for pat in _PHONE_PATTERNS:
        result = pat.sub("[PHONE]", result)

    # 8) Адреса
    result = _ADDRESS_PATTERN.sub("[ADDRESS]", result)

    # 9) Компании (ООО/АО/ИП + название)
    result = _COMPANY_PATTERN.sub("[COMPANY]", result)

    # 10) Имена людей (Фамилия Имя Отчество)
    result = _redact_persons(result)

    # Убираем случайные двойные плейсхолдеры
    result = re.sub(r'(\[(?:PERSON|COMPANY|PHONE|EMAIL)\])(\s*\1)+', r'\1', result)

    return result


def _redact_persons(text: str) -> str:
    """
    Заменяет ФИО на [PERSON], с проверкой на слова-исключения.
    """
    def _replace_match(m: re.Match) -> str:
        first_word = m.group(1).lower()
        second_word = m.group(2).lower()
        if first_word in _PERSON_EXCLUSIONS or second_word in _PERSON_EXCLUSIONS:
            return m.group(0)
        return "[PERSON]"

    return _PERSON_PATTERN.sub(_replace_match, text)


def redact_pii_in_dialogue(dialogue: Dict[str, Any]) -> Dict[str, Any]:
    """
    Применяет redact_pii к каждой реплике в dialogue JSON
    (формат с turns[].text).
    """
    if not dialogue:
        return dialogue

    turns = dialogue.get("turns", [])
    for turn in turns:
        if "text" in turn and turn["text"]:
            turn["text"] = redact_pii(turn["text"])

    return dialogue
