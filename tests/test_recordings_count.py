"""
Счётчик записей в карточке интеграции.

На проде карточка показывала «Записей: 0» при 227 реально скачанных.
Причина — не в CRM: SessionLocal создан с autoflush=False, поэтому
добавленные через db.add записи до commit в БД не попадают, и COUNT по
той же сессии их не видит. При первой синхронизации сохранялся 0, при
последующих счётчик отставал на последнюю партию (у Bitrix-интеграции
на проде было 112 против 196 фактических).
"""

# Порядок импортов значим: переменные окружения выставляются до импорта
# app-модулей (database.py читает DATABASE_URL на импорте).
# isort: skip_file

import os
import tempfile
from datetime import datetime

import pytest
from cryptography.fernet import Fernet

os.environ["DATABASE_URL"] = "postgresql://unused:unused@localhost/unused"
os.environ.setdefault("CRM_ENCRYPTION_KEY", Fernet.generate_key().decode())

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from models import CRMRecording
from routers.crm_integration import _count_recordings


@pytest.fixture
def session():
    """Сессия с autoflush=False — как SessionLocal в приложении."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}",
                           connect_args={"check_same_thread": False},
                           poolclass=NullPool)
    CRMRecording.__table__.create(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()
        os.unlink(path)


def _add(db, integration_id, rec_id):
    db.add(CRMRecording(
        integration_id=integration_id, user_id=1, crm_record_id=rec_id,
        call_date=datetime(2026, 1, 1),
    ))


def test_counts_pending_records_on_first_sync(session):
    """
    Главный случай: БД пуста, все записи ещё не закоммичены.
    Прежний COUNT без flush возвращал 0 — это и видел пользователь.
    """
    for i in range(227):
        _add(session, 1, f"rec-{i}")

    assert _count_recordings(session, 1) == 227


def test_counts_pending_plus_already_saved(session):
    """Повторная синхронизация: часть записей уже в БД, часть добавлена сейчас."""
    for i in range(100):
        _add(session, 1, f"old-{i}")
    session.commit()

    for i in range(27):
        _add(session, 1, f"new-{i}")

    assert _count_recordings(session, 1) == 127


def test_counts_only_requested_integration(session):
    for i in range(5):
        _add(session, 1, f"a-{i}")
    for i in range(3):
        _add(session, 2, f"b-{i}")

    assert _count_recordings(session, 1) == 5
    assert _count_recordings(session, 2) == 3


def test_zero_when_there_are_no_records(session):
    """Ноль должен означать «записей нет», а не «не успели записаться»."""
    assert _count_recordings(session, 1) == 0


def test_pending_records_survive_the_flush(session):
    """flush не должен коммитить: откат обязан остаться возможным."""
    for i in range(3):
        _add(session, 1, f"rec-{i}")

    assert _count_recordings(session, 1) == 3
    session.rollback()

    assert _count_recordings(session, 1) == 0
