from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os

# Получаем URL из переменной окружения, по умолчанию SQLite для обратной совместимости
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///app.db")

# Определяем параметры пула в зависимости от типа БД
if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"):
    # PostgreSQL - мощный пул для production (100+ пользователей)
    engine = create_engine(
        DATABASE_URL,
        pool_size=20,           # Базовый размер пула
        max_overflow=40,        # Дополнительные соединения при нагрузке
        pool_timeout=30,        # Таймаут ожидания соединения
        pool_pre_ping=True,     # Проверка соединений перед использованием
        pool_recycle=3600,      # Пересоздание соединений каждый час
        echo=False              # Логирование SQL (False для production)
    )
else:
    # SQLite - для разработки, с увеличенным пулом
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        pool_size=20,
        max_overflow=40,
        pool_timeout=60,
        pool_pre_ping=True
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
