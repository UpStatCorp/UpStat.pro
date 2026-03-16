"""
Скрипт для миграции trainings, training_sessions и voice_training_messages
"""
import os
import sqlite3
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# Проверяем несколько возможных путей
sqlite_paths = ["/app/root_app.db", "./app.db", "app.db", "app/app.db"]
sqlite_path = None

for path in sqlite_paths:
    try:
        if os.path.exists(path):
            test_conn = sqlite3.connect(path)
            test_cursor = test_conn.cursor()
            test_cursor.execute("SELECT COUNT(*) FROM trainings")
            count = test_cursor.fetchone()[0]
            test_conn.close()
            if count > 0:
                sqlite_path = path
                print(f"✅ Найдена база данных с {count} trainings: {path}")
                break
    except:
        continue

if not sqlite_path:
    sqlite_path = "app.db"  # Fallback
    print(f"⚠️  Используем путь по умолчанию: {sqlite_path}")
postgres_url = os.getenv("DATABASE_URL", "postgresql://saas_user:Bloody0987q@postgres:5432/saas")

print("🔌 Подключаемся к базам данных...")
sqlite_conn = sqlite3.connect(sqlite_path)
sqlite_cursor = sqlite_conn.cursor()

postgres_engine = create_engine(postgres_url)
postgres_conn = postgres_engine.connect()

def migrate_table(table_name, batch_size=1000):
    """Мигрирует таблицу батчами"""
    print(f"\n🔄 Мигрируем таблицу: {table_name}")
    
    try:
        # Получаем данные из SQLite
        sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        total_rows = sqlite_cursor.fetchone()[0]
        
        if total_rows == 0:
            print(f"  ⚠️  Таблица {table_name} пуста")
            return 0
        
        print(f"  📊 Всего строк для миграции: {total_rows}")
        
        # Получаем колонки
        sqlite_cursor.execute(f"PRAGMA table_info({table_name})")
        columns_info = sqlite_cursor.fetchall()
        columns = [col[1] for col in columns_info]
        
        # Получаем типы колонок в PostgreSQL
        pg_cols = postgres_conn.execute(
            text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = :name"),
            {"name": table_name}
        ).fetchall()
        pg_types = {col[0]: col[1] for col in pg_cols}
        
        # Получаем данные батчами
        inserted = 0
        offset = 0
        
        while offset < total_rows:
            sqlite_cursor.execute(f"SELECT * FROM {table_name} LIMIT {batch_size} OFFSET {offset}")
            rows = sqlite_cursor.fetchall()
            
            if not rows:
                break
            
            batch_inserted = 0
            for row in rows:
                try:
                    # Преобразуем значения
                    row_values = []
                    for i, val in enumerate(row):
                        col_name = columns[i]
                        pg_type = pg_types.get(col_name, '')
                        
                        if val is None:
                            row_values.append(None)
                        elif pg_type == 'boolean':
                            row_values.append(bool(val) if isinstance(val, (int, bool)) else val)
                        else:
                            row_values.append(val)
                    
                    placeholders = ", ".join([f":{i}" for i in range(len(columns))])
                    # Экранируем имена колонок кавычками для PostgreSQL
                    columns_str = ", ".join([f'"{col}"' for col in columns])
                    params = {str(i): val for i, val in enumerate(row_values)}
                    
                    # Используем ON CONFLICT для пропуска дубликатов
                    postgres_conn.execute(
                        text(f"INSERT INTO {table_name} ({columns_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"),
                        params
                    )
                    batch_inserted += 1
                    inserted += 1
                    
                except Exception as e:
                    # Пропускаем ошибки (дубликаты, foreign key и т.д.)
                    if "duplicate" not in str(e).lower() and "foreign key" not in str(e).lower():
                        if batch_inserted % 100 == 0:  # Показываем только каждую 100-ю ошибку
                            print(f"  ⚠️  Ошибка при вставке: {str(e)[:80]}")
                    continue
            
            # Коммитим после каждого батча
            postgres_conn.commit()
            
            if inserted % 1000 == 0 or offset + batch_size >= total_rows:
                print(f"  ✅ Мигрировано {inserted} из {total_rows} строк ({int(inserted/total_rows*100)}%)")
            
            offset += batch_size
        
        print(f"  ✅ Завершено: {inserted} из {total_rows} строк мигрировано")
        return inserted
        
    except Exception as e:
        print(f"  ❌ Ошибка при миграции {table_name}: {e}")
        postgres_conn.rollback()
        return 0

# Мигрируем в правильном порядке
print("=" * 60)
print("Начинаем миграцию таблиц тренировок")
print("=" * 60)

total = 0
total += migrate_table("trainings", batch_size=500)
total += migrate_table("training_sessions", batch_size=1000)
total += migrate_table("voice_training_messages", batch_size=1000)

print("\n" + "=" * 60)
print(f"✅ Миграция завершена! Всего мигрировано строк: {total}")
print("=" * 60)

sqlite_conn.close()
postgres_conn.close()

