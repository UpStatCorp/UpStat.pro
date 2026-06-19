"""
StreakService — единый источник правды по стрику тренировок и расписанию программы.

Правила (согласовано):
- День засчитывается, если в этот день (в часовом поясе платформы APP_TZ) есть
  хотя бы одна завершённая тренировка.
- Дни цикла без этапа (выходные по программе) НЕ ломают серию — пропускаются.
- Сегодняшний день не обрывает серию, пока он ещё «в процессе» (не натренирован).
- Стрик хранится в SellerPassport (current_streak / best_streak / last_trained_date),
  пересчитывается дёшево при загрузке дашборда/отчёта и при завершении сессии.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from time_utils import local_date, today_local

_MAX_LOOKBACK_DAYS = 400


def active_program(db: Session, user):
    """Активная программа тренировок для команд пользователя (или None)."""
    from models import TrainingProgram

    team_ids = [tm.team_id for tm in (user.team_memberships or [])]
    if not team_ids:
        return None
    return (
        db.query(TrainingProgram)
        .filter(
            TrainingProgram.team_id.in_(team_ids),
            TrainingProgram.is_active == True,  # noqa: E712
        )
        .first()
    )


def _scheduled_day_indices(db: Session, program) -> set[int]:
    """Множество day_index, у которых назначен этап (есть TrainingProgramDay)."""
    from models import TrainingProgramDay

    rows = (
        db.query(TrainingProgramDay.day_index)
        .filter(TrainingProgramDay.program_id == program.id)
        .all()
    )
    return {r[0] for r in rows}


def today_stage(db: Session, user, today: Optional[date] = None) -> Optional[dict]:
    """
    Этап программы на сегодня для пользователя.
    Возвращает {'stage_key', 'day_index', 'program'} или None (выходной / нет программы).
    """
    from models import TrainingProgramDay

    today = today or today_local()
    prog = active_program(db, user)
    if not prog or not prog.start_date:
        return None

    start = local_date(prog.start_date) or today
    # Программа ещё не началась — сегодня нет назначенного этапа
    if today < start:
        return None
    cycle = max(1, prog.cycle_days)
    day_idx = (today - start).days % cycle

    pd = (
        db.query(TrainingProgramDay)
        .filter(
            TrainingProgramDay.program_id == prog.id,
            TrainingProgramDay.day_index == day_idx,
        )
        .first()
    )
    if pd and pd.stage_key:
        return {"stage_key": pd.stage_key, "day_index": day_idx, "program": prog}
    return None


def _trained_dates(db: Session, user_id: int) -> set[date]:
    from models import TrainingSession

    rows = (
        db.query(TrainingSession.completed_at)
        .filter(
            TrainingSession.user_id == user_id,
            TrainingSession.status == "completed",
            TrainingSession.completed_at.isnot(None),
        )
        .all()
    )
    return {local_date(r[0]) for r in rows if r[0]}


def streak_from_dates(
    dates_trained: set[date],
    today: date,
    scheduled: Optional[set[int]] = None,
    start: Optional[date] = None,
    cycle: int = 1,
) -> int:
    """
    Чистый расчёт серии по уже загруженным данным (без обращений к БД).
    Используется и compute_streak, и пакетным отчётом команды (без N+1).
    """
    if not dates_trained:
        return 0

    cycle = max(1, cycle)
    streak = 0
    d = today
    first = True
    for _ in range(_MAX_LOOKBACK_DAYS):
        # выходной по программе — пропускаем, серию не рвём
        if scheduled is not None and start is not None and d >= start:
            day_idx = (d - start).days % cycle
            if day_idx not in scheduled:
                d -= timedelta(days=1)
                first = False
                continue

        if d in dates_trained:
            streak += 1
            d -= timedelta(days=1)
            first = False
            continue

        # запланированный день без тренировки
        if first:
            # сегодня ещё «в процессе» — не обрываем серию
            d -= timedelta(days=1)
            first = False
            continue
        break

    return streak


def program_schedule(db: Session, prog) -> tuple[Optional[set[int]], Optional[date], int]:
    """(scheduled_indices, start_date, cycle) активной программы или (None, None, 1)."""
    if prog and prog.start_date:
        sched = _scheduled_day_indices(db, prog)
        if sched:  # программа без дней — считаем как будто её нет
            return sched, local_date(prog.start_date), max(1, prog.cycle_days)
    return None, None, 1


def compute_streak(db: Session, user, today: Optional[date] = None) -> int:
    """Текущая серия тренировок с учётом часового пояса и выходных по программе."""
    today = today or today_local()
    dates_trained = _trained_dates(db, user.id)
    if not dates_trained:
        return 0

    scheduled, start, cycle = program_schedule(db, active_program(db, user))
    return streak_from_dates(dates_trained, today, scheduled, start, cycle)


def refresh_streak(db: Session, user) -> tuple[int, int]:
    """
    Пересчитывает стрик и сохраняет в SellerPassport.
    Возвращает (current_streak, best_streak). Коммитит изменения.
    """
    from models import SellerPassport

    streak = compute_streak(db, user)
    dates = _trained_dates(db, user.id)
    last_date = max(dates) if dates else None

    passport = db.query(SellerPassport).filter_by(user_id=user.id).first()
    if passport is None:
        passport = SellerPassport(user_id=user.id)
        db.add(passport)

    passport.current_streak = streak
    if streak > (passport.best_streak or 0):
        passport.best_streak = streak
    passport.last_trained_date = last_date

    db.commit()
    return passport.current_streak, passport.best_streak
