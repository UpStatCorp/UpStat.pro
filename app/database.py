from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os

# Получаем URL из переменной окружения (только PostgreSQL)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан. Укажите переменную окружения DATABASE_URL=postgresql://...")
if "sqlite" in DATABASE_URL.lower():
    raise ValueError("SQLite не поддерживается. Используйте PostgreSQL: DATABASE_URL=postgresql://...")

# Параметры пула для PostgreSQL — конфигурируемые через env, чтобы у каждой
# роли процесса был свой бюджет соединений. Суммарно по всем процессам должно
# выполняться: (web_workers + arq_replicas) * (pool_size + max_overflow)
# <= POSTGRES_MAX_CONNECTIONS (с запасом на админ/миграции). Напр. arq-воркерам
# можно задать меньший DB_POOL_SIZE/DB_MAX_OVERFLOW, чем веб-процессам.
# После того как голосовые WebSocket перестали «прибивать» соединение на всю
# сессию, реальный одновременный спрос на соединения существенно ниже теоретического.
engine = create_engine(
    DATABASE_URL,
    pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "40")),
    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    pool_pre_ping=True,     # Проверка соединений перед использованием
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "3600")),
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
