"""
Конкурентный тест run_db (раздел A плана): каждая единица работы получает
СВОЮ короткоживущую Session (нет шаринга одной Session между задачами), всё
коммитится без ошибок «concurrent operations», соединение возвращается сразу.

Используем in-memory SQLite (StaticPool) вместо живого PostgreSQL — проверяем
именно механику run_db, а не драйвер.
"""

import asyncio
import os
import tempfile
import pytest
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from unittest.mock import patch

import voice_assistant.db_service as dbs

_Base = declarative_base()


class _Row(_Base):
    __tablename__ = "rows"
    id = Column(Integer, primary_key=True)
    val = Column(String)


@pytest.fixture
def sqlite_sessionmaker():
    # Файловый SQLite + NullPool: каждая Session получает СВОЁ соединение к одной БД
    # (как пер-оп сессии в проде). timeout сериализует конкурентные записи без
    # "database is locked".
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    _Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()
        os.unlink(path)


@pytest.mark.asyncio
async def test_run_db_uses_isolated_session_per_op(sqlite_sessionmaker):
    N = 50
    seen_session_ids = []

    def work(s, i):
        seen_session_ids.append(id(s))
        s.add(_Row(val=f"v{i}"))
        s.commit()
        return i

    with patch.object(dbs, "_get_session_local", return_value=sqlite_sessionmaker):
        results = await asyncio.gather(*[dbs.run_db(work, i) for i in range(N)])

    # все операции отработали (одна ОБЩАЯ Session под конкурентной нагрузкой
    # бросила бы "concurrent operations"/потеряла данные — gather бы упал)
    assert sorted(results) == list(range(N))
    # создаётся МНОГО разных Session (не одна общая). Точное число == N не
    # проверяем: id() переиспользуется после закрытия/GC ранних сессий.
    assert len(set(seen_session_ids)) >= 10
    # все строки реально закоммичены ровно по разу
    check = sqlite_sessionmaker()
    try:
        assert check.query(_Row).count() == N
    finally:
        check.close()


@pytest.mark.asyncio
async def test_run_db_propagates_return_value(sqlite_sessionmaker):
    def work(s):
        return "result-42"

    with patch.object(dbs, "_get_session_local", return_value=sqlite_sessionmaker):
        out = await dbs.run_db(work)
    assert out == "result-42"
