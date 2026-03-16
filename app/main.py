import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
import logging

from database import Base, engine
from routers import auth, chat, chat_trener, dashboard, public, settings, zoom_meetings, webrtc_meetings, admin, admin_prompts, tts_proxy, training_plans, crm_integration, teams, team_analytics, sales
import sqlite3


def create_training_tables():
    """Создает таблицы для системы тренировок"""
    try:
        # Получаем путь к базе данных из URL
        database_url = os.getenv("DATABASE_URL", "sqlite:///./app.db")
        if database_url.startswith("sqlite:///"):
            db_path = database_url.replace("sqlite:///", "")
            if db_path.startswith("./"):
                db_path = db_path[2:]
        else:
            return  # Не SQLite база данных
        
        if not os.path.exists(db_path):
            return  # База данных не существует
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Проверяем существует ли таблица analysis_training_plans
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_training_plans'")
        if not cursor.fetchone():
            print("Создаем таблицы для системы тренировок...")
            
            # Создаем таблицу analysis_training_plans
            cursor.execute("""
                CREATE TABLE analysis_training_plans (
                    id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    report_message_id INTEGER NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    recommendations_json TEXT NOT NULL,
                    total_trainings INTEGER DEFAULT 0,
                    completed_trainings INTEGER DEFAULT 0,
                    status VARCHAR(20) DEFAULT 'active',
                    created_at DATETIME,
                    PRIMARY KEY (id),
                    FOREIGN KEY(user_id) REFERENCES users (id),
                    FOREIGN KEY(report_message_id) REFERENCES messages (id)
                )
            """)
            cursor.execute("CREATE INDEX ix_analysis_training_plans_id ON analysis_training_plans(id)")
            cursor.execute("CREATE INDEX ix_analysis_training_plans_user_id ON analysis_training_plans(user_id)")
            cursor.execute("CREATE INDEX ix_analysis_training_plans_report_message_id ON analysis_training_plans(report_message_id)")
            print("Таблица analysis_training_plans создана")
            
            # Создаем таблицу trainings
            cursor.execute("""
                CREATE TABLE trainings (
                    id INTEGER NOT NULL,
                    plan_id INTEGER NOT NULL,
                    "order" INTEGER NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    description TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    scenario_type VARCHAR(50) DEFAULT 'custom',
                    checklist_json TEXT,
                    status VARCHAR(20) DEFAULT 'locked',
                    attempts INTEGER DEFAULT 0,
                    best_score INTEGER,
                    last_attempt_at DATETIME,
                    completed_at DATETIME,
                    created_at DATETIME,
                    PRIMARY KEY (id),
                    FOREIGN KEY(plan_id) REFERENCES analysis_training_plans (id)
                )
            """)
            cursor.execute("CREATE INDEX ix_trainings_id ON trainings(id)")
            cursor.execute("CREATE INDEX ix_trainings_plan_id ON trainings(plan_id)")
            print("Таблица trainings создана")
            
            # Создаем таблицу training_sessions
            cursor.execute("""
                CREATE TABLE training_sessions (
                    id INTEGER NOT NULL,
                    training_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    started_at DATETIME,
                    completed_at DATETIME,
                    duration_seconds INTEGER,
                    transcript TEXT,
                    score INTEGER,
                    feedback TEXT,
                    checklist_results_json TEXT,
                    user_responses_count INTEGER DEFAULT 0,
                    ai_questions_count INTEGER DEFAULT 0,
                    PRIMARY KEY (id),
                    FOREIGN KEY(training_id) REFERENCES trainings (id),
                    FOREIGN KEY(user_id) REFERENCES users (id)
                )
            """)
            cursor.execute("CREATE INDEX ix_training_sessions_id ON training_sessions(id)")
            cursor.execute("CREATE INDEX ix_training_sessions_training_id ON training_sessions(training_id)")
            cursor.execute("CREATE INDEX ix_training_sessions_user_id ON training_sessions(user_id)")
            print("Таблица training_sessions создана")
            
            # Создаём таблицу уведомлений
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    type VARCHAR(50) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    icon VARCHAR(10) DEFAULT '🔔',
                    link VARCHAR(512),
                    link_text VARCHAR(100),
                    is_read BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME,
                    read_at DATETIME,
                    metadata_json TEXT,
                    PRIMARY KEY (id),
                    FOREIGN KEY(user_id) REFERENCES users (id)
                )
            """)
            cursor.execute("CREATE INDEX ix_notifications_id ON notifications(id)")
            cursor.execute("CREATE INDEX ix_notifications_user_id ON notifications(user_id)")
            cursor.execute("CREATE INDEX ix_notifications_created_at ON notifications(created_at)")
            print("Таблица notifications создана")
            
            conn.commit()
            print("Таблицы для системы тренировок успешно созданы")
        else:
            print("Таблицы для системы тренировок уже существуют")
        
        # Создаем таблицы для аналитики
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='training_conversion_metrics'")
        if not cursor.fetchone():
            print("Создаем таблицы для аналитики...")
            
            # Создаем таблицу training_conversion_metrics
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS training_conversion_metrics (
                    id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    team_id INTEGER,
                    metric_date DATETIME NOT NULL,
                    period_type VARCHAR(20) DEFAULT 'daily',
                    conversion_rates_json TEXT NOT NULL,
                    total_plans INTEGER DEFAULT 0,
                    active_plans INTEGER DEFAULT 0,
                    completed_plans INTEGER DEFAULT 0,
                    total_trainings INTEGER DEFAULT 0,
                    completed_trainings INTEGER DEFAULT 0,
                    avg_score REAL,
                    created_at DATETIME,
                    PRIMARY KEY (id),
                    FOREIGN KEY(user_id) REFERENCES users (id),
                    FOREIGN KEY(team_id) REFERENCES teams (id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_training_conversion_metrics_user_id ON training_conversion_metrics(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_training_conversion_metrics_team_id ON training_conversion_metrics(team_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_training_conversion_metrics_date ON training_conversion_metrics(metric_date)")
            print("Таблица training_conversion_metrics создана")
            
            # Создаем таблицу training_errors_corrections
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS training_errors_corrections (
                    id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    team_id INTEGER,
                    conversation_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    error_type VARCHAR(100) NOT NULL,
                    error_description TEXT NOT NULL,
                    error_severity VARCHAR(20) DEFAULT 'medium',
                    correction_text TEXT NOT NULL,
                    correction_applied BOOLEAN DEFAULT 0,
                    correction_applied_at DATETIME,
                    training_plan_id INTEGER,
                    training_id INTEGER,
                    detected_at DATETIME NOT NULL,
                    created_at DATETIME,
                    PRIMARY KEY (id),
                    FOREIGN KEY(user_id) REFERENCES users (id),
                    FOREIGN KEY(team_id) REFERENCES teams (id),
                    FOREIGN KEY(conversation_id) REFERENCES conversations (id),
                    FOREIGN KEY(message_id) REFERENCES messages (id),
                    FOREIGN KEY(training_plan_id) REFERENCES analysis_training_plans (id),
                    FOREIGN KEY(training_id) REFERENCES trainings (id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_training_errors_corrections_user_id ON training_errors_corrections(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_training_errors_corrections_team_id ON training_errors_corrections(team_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_training_errors_corrections_detected_at ON training_errors_corrections(detected_at)")
            print("Таблица training_errors_corrections создана")
            
            conn.commit()
            print("Таблицы для аналитики успешно созданы")
        else:
            print("Таблицы для аналитики уже существуют")
        
        conn.close()
        
    except Exception as e:
        print(f"Ошибка при создании таблиц для системы тренировок: {e}")


def update_database_schema():
    """Обновляет схему базы данных для поддержки Google OAuth"""
    try:
        # Получаем путь к базе данных из URL
        database_url = os.getenv("DATABASE_URL", "sqlite:///./app.db")
        if database_url.startswith("sqlite:///"):
            db_path = database_url.replace("sqlite:///", "")
            if db_path.startswith("./"):
                db_path = db_path[2:]
        else:
            return  # Не SQLite база данных
        
        if not os.path.exists(db_path):
            return  # База данных не существует
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Проверяем существующие колонки
        cursor.execute("PRAGMA table_info(users)")
        columns_info = cursor.fetchall()
        columns = [column[1] for column in columns_info]
        
        # Проверяем, нужно ли исправлять ограничение NOT NULL для password_hash
        password_hash_nullable = True
        for col in columns_info:
            if col[1] == 'password_hash':
                password_hash_nullable = not col[3]  # col[3] = notnull flag
                break
        
        # Если password_hash все еще NOT NULL, нужно пересоздать таблицу
        if not password_hash_nullable:
            print("Исправляем ограничение NOT NULL для password_hash...")
            
            # Создаем новую таблицу с правильной схемой
            cursor.execute("""
                CREATE TABLE users_new (
                    id INTEGER NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    password_hash VARCHAR(255),
                    name VARCHAR(120) NOT NULL,
                    phone VARCHAR(20),
                    avatar VARCHAR(512),
                    role VARCHAR(10) NOT NULL DEFAULT 'user',
                    google_id VARCHAR(255),
                    is_oauth_user BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME,
                    updated_at VARCHAR,
                    PRIMARY KEY (id)
                )
            """)
            
            # Копируем данные из старой таблицы
            cursor.execute("""
                INSERT INTO users_new 
                (id, email, password_hash, name, phone, avatar, role, google_id, is_oauth_user, created_at, updated_at)
                SELECT 
                    id, email, password_hash, name, phone, avatar, role, 
                    COALESCE(google_id, NULL) as google_id,
                    COALESCE(is_oauth_user, 0) as is_oauth_user,
                    created_at, updated_at
                FROM users
            """)
            
            # Удаляем старую таблицу
            cursor.execute("DROP TABLE users")
            
            # Переименовываем новую таблицу
            cursor.execute("ALTER TABLE users_new RENAME TO users")
            
            # Создаем индексы
            cursor.execute("CREATE UNIQUE INDEX ix_users_email ON users(email)")
            cursor.execute("CREATE INDEX ix_users_id ON users(id)")
            cursor.execute("CREATE INDEX ix_users_role ON users(role)")
            cursor.execute("CREATE UNIQUE INDEX ix_users_google_id ON users(google_id)")
            
            print("Таблица users пересоздана с правильной схемой")
        else:
            # Добавляем поля если их нет
            if 'google_id' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN google_id VARCHAR(255)")
                print("Добавлено поле google_id")
            
            if 'is_oauth_user' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN is_oauth_user BOOLEAN DEFAULT 0")
                print("Добавлено поле is_oauth_user")
            
            # Создаем индекс для google_id если его нет
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_users_google_id'")
            if not cursor.fetchone():
                cursor.execute("CREATE UNIQUE INDEX ix_users_google_id ON users(google_id)")
                print("Создан индекс ix_users_google_id")
        
        conn.commit()
        conn.close()
        print("Схема базы данных обновлена для Google OAuth")
        
    except Exception as e:
        print(f"Ошибка при обновлении схемы базы данных: {e}")


def update_premium_schema():
    """Добавляет поля подписки/лимитов в таблицу users (PostgreSQL + SQLite)"""
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(engine)

        # Проверяем, что таблица users существует
        if 'users' not in inspector.get_table_names():
            return

        columns = [c['name'] for c in inspector.get_columns('users')]

        with engine.begin() as conn:
            if 'is_premium' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_premium BOOLEAN NOT NULL DEFAULT FALSE"))
                print("✅ Добавлено поле is_premium")

            if 'free_analyses_limit' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN free_analyses_limit INTEGER NOT NULL DEFAULT 5"))
                print("✅ Добавлено поле free_analyses_limit")

            if 'analyses_used' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN analyses_used INTEGER NOT NULL DEFAULT 0"))
                print("✅ Добавлено поле analyses_used")

            if 'premium_granted_by' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN premium_granted_by INTEGER"))
                print("✅ Добавлено поле premium_granted_by")

            if 'premium_granted_at' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN premium_granted_at TIMESTAMP"))
                print("✅ Добавлено поле premium_granted_at")

            # Расширяем поле role до VARCHAR(20) если PostgreSQL
            database_url = os.getenv("DATABASE_URL", "sqlite:///./app.db")
            if database_url.startswith("postgresql://") or database_url.startswith("postgres://"):
                try:
                    conn.execute(text("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(20)"))
                    print("✅ Расширено поле role до VARCHAR(20)")
                except Exception:
                    pass  # Уже правильного размера

        print("✅ Схема подписок обновлена")
    except Exception as e:
        print(f"⚠️ Ошибка при обновлении схемы подписок: {e}")


def create_app() -> FastAPI:
    """Create and configure a FastAPI application."""
    load_dotenv()
    
    # Настройка логирования (до использования logger)
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    secret_key = os.getenv("SECRET_KEY", "dev_secret_change_me")

    # Миграции через Alembic лучше, но для MVP создадим таблицы автоматически
    Base.metadata.create_all(bind=engine)
    
    # Обновляем схему базы данных для Google OAuth
    update_database_schema()
    
    # Создаем таблицы для системы тренировок
    create_training_tables()
    
    # Обновляем схему подписок/лимитов
    update_premium_schema()

    app = FastAPI(title="SaaS MVP (FastAPI)")
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        https_only=False,  # Изменено для локальной разработки
        same_site="lax",
    )

    # templates + partials
    templates = Jinja2Templates(directory="templates")
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory="static"), name="static")

    app.include_router(public.router)
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(chat_trener.router)
    app.include_router(dashboard.router)
    app.include_router(settings.router)
    app.include_router(zoom_meetings.router)
    app.include_router(webrtc_meetings.router)
    app.include_router(admin.router)
    app.include_router(admin_prompts.router)
    app.include_router(tts_proxy.router, prefix="/api")
    app.include_router(training_plans.router)  # Роутер планов тренировок
    app.include_router(crm_integration.router)  # Роутер CRM интеграций
    app.include_router(teams.router)  # Роутер команд и приглашений
    app.include_router(team_analytics.router)  # Роутер аналитики команды
    app.include_router(sales.router)  # Роутер панели продаж (Sale Manager)
    
    # Добавляем роутер уведомлений, прогресса и производительности
    from routers import notifications, progress, performance
    app.include_router(notifications.router)
    app.include_router(progress.router)
    app.include_router(performance.router)
    
    # Добавляем HTML роутер для WebRTC встреч
    from routers.webrtc_meetings import html_router
    app.include_router(html_router)
    
    # Настройка путей для voice_assistant (нужна для обоих роутеров)
    try:
        import sys
        # os уже импортирован в начале файла
        # Добавляем путь к voice_assistant в sys.path если его там нет
        # В Docker: /app/main.py -> /app/voice_assistant
        # Локально: ./app/main.py -> ./voice_assistant
        current_dir = os.path.dirname(os.path.abspath(__file__))  # /app или ./app
        project_root = os.path.dirname(current_dir)  # / или .
        voice_assistant_path = os.path.join(project_root, 'voice_assistant')
        voice_assistant_path = os.path.abspath(voice_assistant_path)
        
        if os.path.exists(voice_assistant_path) and voice_assistant_path not in sys.path:
            sys.path.insert(0, project_root)  # Добавляем корень проекта в sys.path
            logger.info(f"Added voice_assistant path to sys.path: {voice_assistant_path}")
    except Exception as e:
        logger.warning(f"Error setting up voice_assistant path: {e}")
    
    # Добавляем роутер голосового ассистента (старый, опциональный)
    try:
        from voice_assistant.router import router as voice_assistant_router
        app.include_router(voice_assistant_router)
        logger.info("Voice assistant router loaded successfully")
    except ImportError as e:
        logger.warning(f"Voice assistant router not available: {e}")
        logger.warning("To enable voice assistant, copy modules from reactive_voice_trener to voice_assistant/")
        import traceback
        logger.warning(traceback.format_exc())
    except Exception as e:
        logger.warning(f"Error loading voice assistant router: {e}")
        import traceback
        logger.warning(traceback.format_exc())
    
    # Подключаем новый масштабируемый роутер для голосовых тренировок (ОБЯЗАТЕЛЬНЫЙ)
    try:
        from voice_assistant.router_new import router as voice_training_router
        app.include_router(voice_training_router)
        logger.info("✅ Voice training router (scalable) loaded successfully")
    except ImportError as e:
        logger.error(f"❌ Voice training router not available: {e}")
        import traceback
        logger.error(traceback.format_exc())
    except Exception as e:
        logger.error(f"❌ Error loading voice training router: {e}")
        import traceback
        logger.error(traceback.format_exc())

    return app


app = create_app()

# Глобальный logger для middleware
logger = logging.getLogger(__name__)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Запрос: {request.method} {request.url}")
    try:
        response = await call_next(request)
        logger.info(f"Ответ: {response.status_code}")
        return response
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке запроса {request.method} {request.url}: {e}", exc_info=True)
        raise

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик исключений для логирования всех ошибок"""
    logger.error(f"❌ Необработанное исключение: {type(exc).__name__}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Внутренняя ошибка сервера: {str(exc)}"}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Обработчик ошибок валидации запросов"""
    logger.error(f"❌ Ошибка валидации запроса: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )
