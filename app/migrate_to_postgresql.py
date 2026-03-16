"""
Скрипт для миграции данных из SQLite в PostgreSQL
Запустите один раз после настройки PostgreSQL

Использование:
    python migrate_to_postgresql.py
"""
import os
import sqlite3
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# Подключение к SQLite
# Проверяем несколько возможных путей, начиная с самого большого файла (вероятно, с данными)
sqlite_paths = ["/app/root_app.db", "./app.db", "app.db", "app/app.db", "./app/app.db"]
sqlite_path = None
best_path = None
best_count = 0

for path in sqlite_paths:
    if os.path.exists(path):
        # Проверяем, что это действительно база с данными
        try:
            test_conn = sqlite3.connect(path)
            test_cursor = test_conn.cursor()
            test_cursor.execute("SELECT COUNT(*) FROM users")
            count = test_cursor.fetchone()[0]
            test_conn.close()
            if count > best_count:  # Находим базу с наибольшим количеством данных
                best_path = path
                best_count = count
        except Exception as e:
            continue

if best_path and best_count > 0:
    sqlite_path = best_path
    print(f"✅ Найдена база данных с {best_count} пользователями: {sqlite_path}")
else:
    print(f"❌ SQLite файл с данными не найден")
    print(f"💡 Проверенные пути: {', '.join(sqlite_paths)}")
    print("💡 Если вы начинаете с нуля, просто запустите приложение - таблицы создадутся автоматически")
    exit(0)

print(f"📂 Найден SQLite файл: {sqlite_path}")

sqlite_conn = sqlite3.connect(sqlite_path)
sqlite_cursor = sqlite_conn.cursor()

# Подключение к PostgreSQL
postgres_url = os.getenv("DATABASE_URL")
if not postgres_url or not (postgres_url.startswith("postgresql://") or postgres_url.startswith("postgres://")):
    print("❌ DATABASE_URL не настроен для PostgreSQL")
    print("💡 Убедитесь, что в .env файле указан DATABASE_URL=postgresql://...")
    exit(1)

print(f"🔌 Подключаемся к PostgreSQL...")
try:
    postgres_engine = create_engine(postgres_url)
    postgres_conn = postgres_engine.connect()
    
    # Проверяем подключение
    postgres_conn.execute(text("SELECT 1"))
    print("✅ Подключение к PostgreSQL успешно")
except Exception as e:
    print(f"❌ Ошибка подключения к PostgreSQL: {e}")
    exit(1)

try:
    # Получаем список таблиц из SQLite
    sqlite_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in sqlite_cursor.fetchall()]
    
    # Определяем порядок миграции с учетом foreign key зависимостей
    # Порядок: сначала родительские таблицы, потом дочерние
    migration_order = [
        'users',                    # Базовые таблицы
        'prompts',
        'messages',                 # Для analysis_training_plans
        'conversations',            # Для messages
        'analysis_training_plans',  # Для trainings
        'trainings',                # Для training_sessions
        'training_sessions',        # Для voice_training_messages
        'voice_training_messages',
        'attachments',
        'zoom_meetings',
        'meeting_transcripts',
        'custom_meetings',
        'meeting_participants',
        'custom_meeting_transcripts',
        'crm_integrations',
        'crm_recordings',
        'notifications',
        'alembic_version'
    ]
    
    # Сортируем таблицы согласно порядку миграции
    ordered_tables = []
    for table in migration_order:
        if table in tables:
            ordered_tables.append(table)
    
    # Добавляем оставшиеся таблицы
    remaining = [t for t in tables if t not in ordered_tables]
    ordered_tables.extend(remaining)
    tables = ordered_tables
    
    if not tables:
        print("⚠️  В SQLite нет таблиц для миграции")
        exit(0)
    
    print(f"\n📋 Найдено таблиц для миграции: {len(tables)}")
    print(f"   {', '.join(tables)}\n")
    
    # Спрашиваем подтверждение (можно пропустить через переменную окружения AUTO_MIGRATE=yes)
    auto_migrate = os.getenv("AUTO_MIGRATE", "").lower() == "yes"
    if not auto_migrate:
        try:
            response = input("⚠️  ВНИМАНИЕ: Это перезапишет данные в PostgreSQL. Продолжить? (yes/no): ")
            if response.lower() != "yes":
                print("❌ Миграция отменена")
                exit(0)
        except EOFError:
            # Если нет интерактивного ввода (например, в Docker), используем переменную окружения
            print("⚠️  Интерактивный ввод недоступен. Используйте AUTO_MIGRATE=yes для автоматической миграции")
            exit(1)
    
    total_migrated = 0
    
    for table in tables:
        print(f"🔄 Мигрируем таблицу: {table}")
        
        try:
            # Получаем данные из SQLite
            sqlite_cursor.execute(f"SELECT * FROM {table}")
            rows = sqlite_cursor.fetchall()
            
            if not rows:
                print(f"  ⚠️  Таблица {table} пуста, пропускаем")
                continue
            
            # Получаем названия колонок
            sqlite_cursor.execute(f"PRAGMA table_info({table})")
            columns_info = sqlite_cursor.fetchall()
            columns = [col[1] for col in columns_info]
            
            # Проверяем, существует ли таблица в PostgreSQL
            check_table = postgres_conn.execute(
                text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = :table_name
                    )
                """),
                {"table_name": table}
            ).scalar()
            
            if not check_table:
                print(f"  ⚠️  Таблица {table} не существует в PostgreSQL, пропускаем")
                print(f"  💡 Создайте таблицы через: python -c 'from app.main import create_app; from database import Base, engine; app = create_app(); Base.metadata.create_all(bind=engine)'")
                continue
            
            # Очищаем таблицу в PostgreSQL только если она пуста или это первая миграция
            # Для таблиц с данными пропускаем очистку, чтобы не потерять уже мигрированные данные
            try:
                existing_count = postgres_conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                if existing_count > 0:
                    print(f"  ⚠️  В таблице {table} уже есть {existing_count} записей. Пропускаем очистку.")
                else:
                    postgres_conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
            except Exception as e:
                print(f"  ⚠️  Не удалось очистить таблицу (возможно, есть зависимости): {e}")
            
            # Получаем информацию о типах колонок в PostgreSQL для конвертации
            pg_columns_info = postgres_conn.execute(
                text("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_name = :table_name
                """),
                {"table_name": table}
            ).fetchall()
            
            pg_column_types = {col[0]: col[1] for col in pg_columns_info}
            
            # Вставляем данные
            inserted = 0
            for row in rows:
                try:
                    # Преобразуем значения с учетом типов данных
                    row_values = []
                    for i, val in enumerate(row):
                        col_name = columns[i]
                        pg_type = pg_column_types.get(col_name, '')
                        
                        if val is None:
                            # Если значение NULL, но поле обязательное, используем значение по умолчанию
                            # Для role используем 'user' по умолчанию
                            if col_name == 'role':
                                row_values.append('user')
                            else:
                                row_values.append(None)
                        elif pg_type == 'boolean':
                            # SQLite хранит boolean как integer (0/1), конвертируем в boolean
                            row_values.append(bool(val) if isinstance(val, (int, bool)) else val)
                        else:
                            row_values.append(val)
                    
                    placeholders = ", ".join([f":{i}" for i in range(len(columns))])
                    columns_str = ", ".join(columns)
                    
                    params = {str(i): val for i, val in enumerate(row_values)}
                    
                    # Используем ON CONFLICT для пропуска дубликатов
                    # Для PostgreSQL используем ON CONFLICT DO NOTHING
                    try:
                        postgres_conn.execute(
                            text(f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"),
                            params
                        )
                    except Exception as insert_error:
                        # Если ON CONFLICT не поддерживается (старая версия PostgreSQL), пробуем обычный INSERT
                        if "syntax error" in str(insert_error).lower() or "ON CONFLICT" in str(insert_error):
                            # Пробуем обычный INSERT
                            postgres_conn.execute(
                                text(f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders})"),
                                params
                            )
                        else:
                            raise
                    inserted += 1
                except Exception as e:
                    # Если ошибка транзакции, откатываем и продолжаем со следующей строки
                    if "InFailedSqlTransaction" in str(e) or "current transaction is aborted" in str(e):
                        try:
                            postgres_conn.rollback()
                        except:
                            pass
                        # Пропускаем эту строку и продолжаем
                        continue
                    # Для других ошибок выводим предупреждение, но продолжаем
                    if inserted % 1000 == 0:  # Показываем ошибку только каждую 1000-ю строку
                        print(f"  ⚠️  Ошибка при вставке строки {inserted}: {str(e)[:100]}")
                    # Пропускаем эту строку и продолжаем
                    continue
            
            # Коммитим транзакцию после каждой таблицы
            try:
                postgres_conn.commit()
                print(f"  ✅ Мигрировано {inserted} из {len(rows)} строк")
                total_migrated += inserted
            except Exception as e:
                print(f"  ⚠️  Ошибка при коммите: {e}")
                postgres_conn.rollback()
                # Пытаемся переподключиться
                postgres_conn.close()
                postgres_conn = postgres_engine.connect()
            
        except Exception as e:
            print(f"  ❌ Ошибка при миграции таблицы {table}: {e}")
            postgres_conn.rollback()
            continue
    
    print(f"\n✅ Миграция завершена! Всего мигрировано строк: {total_migrated}")
    print("\n💡 Рекомендации:")
    print("   1. Проверьте данные в PostgreSQL")
    print("   2. Создайте backup: docker-compose exec postgres pg_dump -U saas_user saas > backup.sql")
    print("   3. Можно удалить app.db после проверки (но лучше оставить как backup)")
    
except Exception as e:
    print(f"\n❌ Ошибка миграции: {e}")
    import traceback
    traceback.print_exc()
    postgres_conn.rollback()
finally:
    sqlite_conn.close()
    postgres_conn.close()
    print("\n🔌 Соединения закрыты")

