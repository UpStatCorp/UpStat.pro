"""
Единый часовой пояс платформы для «дневной» логики (стрик, тренировка на сегодня).

completed_at и прочие даты в БД хранятся как наивный UTC (datetime.utcnow()).
Для подсчёта «дня» их нужно приводить к локальному поясу платформы, иначе у
не-UTC пользователей день «сдвигается» и стрик считается некорректно.

Пояс задаётся переменной окружения APP_TZ (по умолчанию Europe/Moscow).
"""
from __future__ import annotations

import os
from datetime import datetime, date, timezone

try:
    from zoneinfo import ZoneInfo
    _APP_TZ_NAME = os.getenv("APP_TZ", "Europe/Moscow")
    try:
        APP_TZ = ZoneInfo(_APP_TZ_NAME)
    except Exception:
        APP_TZ = timezone.utc
except Exception:  # pragma: no cover — zoneinfo есть в stdlib c 3.9
    APP_TZ = timezone.utc


def local_date(dt: datetime | None) -> date | None:
    """Приводит наивный UTC-datetime к дате в часовом поясе платформы."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(APP_TZ).date()


def today_local() -> date:
    """Сегодняшняя дата в часовом поясе платформы."""
    return datetime.now(APP_TZ).date()
