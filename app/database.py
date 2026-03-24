from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os

# Получаем URL из переменной окружения (только PostgreSQL)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан. Укажите переменную окружения DATABASE_URL=postgresql://...")
if "sqlite" in DATABASE_URL.lower():
    raise ValueError("SQLite не поддерживается. Используйте PostgreSQL: DATABASE_URL=postgresql://...")

# Определяем параметры пула для PostgreSQL
engine = create_engine(
    DATABASE_URL,
    pool_size=20,           # Базовый размер пула
    max_overflow=40,        # Дополнительные соединения при нагрузке
    pool_timeout=30,        # Таймаут ожидания соединения
    pool_pre_ping=True,     # Проверка соединений перед использованием
    pool_recycle=3600,      # Пересоздание соединений каждый час
    echo=False              # Логирование SQL (False для production)
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
