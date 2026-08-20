from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))

from database import Base
from models import *

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def get_url():
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section)
    configuration['sqlalchemy.url'] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Задано явно, а не оставлено на дефолт версии: критерий приёмки
            # (пустой autogenerate) зависит от этих флагов, и обновление
            # alembic не должно менять его молча. compare_type в 1.12.1
            # уже True по умолчанию — фиксируем.
            compare_type=True,
            # server_default НЕ сравниваем: в моделях значения по умолчанию
            # питоновские (default=), в миграциях — серверные
            # (server_default=). Сравнение дало бы вечный ложный дифф.
            compare_server_default=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
