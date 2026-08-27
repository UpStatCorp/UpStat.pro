import os
import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

_APP_DIR = Path(__file__).parent
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
import logging

from database import engine, SessionLocal
from routers import auth, chat, chat_trener, dashboard, public, settings, zoom_meetings, webrtc_meetings, admin, admin_prompts, admin_research, admin_trainings, tts_proxy, training_plans, crm_integration, teams, team_analytics, sales, analytics, owner_dashboard
from routers import training_catalog, training_program, train_report


class BrandMiddleware(BaseHTTPMiddleware):
    """
    Определяет бренд по hostname запроса и кладёт в request.state.brand.
    'train' — train.upstat.pro (и любой из TRAIN_HOSTS).
    Локально: ?brand=train переключает бренд для разработки.
    """
    TRAIN_HOSTS: set[str] = set(
        h.strip() for h in os.getenv("TRAIN_HOSTS", "train.upstat.pro").split(",") if h.strip()
    )

    async def dispatch(self, request: Request, call_next):
        hostname = request.url.hostname or ""
        brand = "full"

        if any(hostname == h or hostname.startswith("train.") for h in self.TRAIN_HOSTS):
            brand = "train"

        # Локальный переключатель для разработки (query или cookie)
        if os.getenv("ALLOW_BRAND_OVERRIDE", "true").lower() == "true":
            q_brand = request.query_params.get("brand")
            if q_brand in ("train", "full"):
                brand = q_brand

        request.state.brand = brand
        response = await call_next(request)
        return response


async def _session_cleanup_loop():
    """Фоновая задача: каждые 5 минут закрывает неактивные WS-сессии."""
    log = logging.getLogger("session_cleanup")
    while True:
        await asyncio.sleep(300)
        try:
            from voice_assistant.session_manager import get_session_manager
            mgr = get_session_manager()
            await mgr.cleanup_inactive_sessions(timeout_seconds=3600)
            stats = mgr.get_stats()
            log.info("Session cleanup complete", extra={"sessions": stats["total_sessions"]})
        except Exception as exc:
            log.error("Session cleanup error", extra={"error": str(exc)}, exc_info=True)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_session_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Закрываем arq-пул, если он создавался для постановки задач
        try:
            from services.queue import close_arq_pool
            await close_arq_pool()
        except Exception:  # noqa: BLE001
            pass


def create_app() -> FastAPI:
    """Create and configure a FastAPI application."""
    load_dotenv()

    # Настройка структурированного логирования и Sentry (до любых logger-вызовов)
    from logging_config import setup_logging
    setup_logging()
    logger = logging.getLogger(__name__)

    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY не задан. Укажите переменную окружения SECRET_KEY "
            "(сгенерируйте: python -c \"import secrets; print(secrets.token_urlsafe(32))\")"
        )
    if len(secret_key) < 32:
        raise RuntimeError(
            f"SECRET_KEY слишком короткий ({len(secret_key)} байт). Минимум — 32 символа."
        )

    # Схема БД создаётся ТОЛЬКО миграциями. Base.metadata.create_all убран:
    # он чинил лишь отсутствующие таблицы (колонки в существующих не добавлял),
    # а расхождения при этом маскировал — создавал таблицу, после чего guard
    # в миграции пропускал её и штамповал ревизию как применённую.
    # Несоответствие схемы — фатально: `alembic upgrade head` обязан быть
    # выполнен ДО старта. См. docs/runbook.md, раздел «Порядок деплоя».
    from db_guard import assert_schema_current

    assert_schema_current(engine)

    # Ключ шифрования CRM-токенов: проверяем пробным шифрованием-расшифровкой
    # ЗДЕСЬ, а не при первом обращении к CRM. Раньше отсутствующий ключ молча
    # подменялся сгенерированным, а невалидный обнаруживался только у
    # случайного пользователя в середине дня и в виде 500.
    from services.crm_service import assert_encryption_key_valid

    assert_encryption_key_valid()

    # Синхронизируем справочник пунктов чеклистов для Win Probability
    try:
        from services.checklist_registry_service import sync_checklist_definitions
        db_session = SessionLocal()
        try:
            sync_checklist_definitions(db_session)
        finally:
            db_session.close()
    except Exception as e:
        logger.warning(f"Ошибка синхронизации справочника чеклистов: {e}")

    app = FastAPI(title="SaaS MVP (FastAPI)", lifespan=_lifespan)

    # CORS: разрешаем только явный список доменов из CORS_ORIGINS.
    # В dev можно задать "*", в prod — конкретные домены через запятую.
    _cors_raw = os.getenv("CORS_ORIGINS", "")
    _cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Порядок add_middleware: последний добавленный = первый на входящий запрос.
    # SessionMiddleware должен быть самым внешним, чтобы request.session доступен ниже.
    # В production cookie сессии помечается Secure по умолчанию (переопределяется HTTPS_ONLY).
    _env = os.getenv("ENVIRONMENT", "").lower()
    _https_default = "true" if _env in ("production", "prod") else "false"
    https_only = os.getenv("HTTPS_ONLY", _https_default).lower() == "true"
    session_max_age = int(os.getenv("SESSION_MAX_AGE", str(60 * 60 * 8)))  # 8 часов (было 14 дней)

    from security import SecurityHeaders as _SH

    class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            for header, value in _SH.get_security_headers().items():
                response.headers.setdefault(header, value)
            return response

    # CSRF-защита для изменяющих запросов: Origin/Referer должны совпадать с хостом.
    # Дополняет SameSite=lax cookie без необходимости вставлять токены во все формы.
    class _CSRFOriginMiddleware(BaseHTTPMiddleware):
        SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
        EXEMPT_PREFIXES = ("/crm/webhook/",)  # внешние вебхуки без браузерного Origin

        async def dispatch(self, request: Request, call_next):
            from urllib.parse import urlparse
            if request.method in self.SAFE_METHODS or any(
                request.url.path.startswith(p) for p in self.EXEMPT_PREFIXES
            ):
                return await call_next(request)
            host = request.headers.get("host", "")
            origin = request.headers.get("origin")
            if origin:
                if urlparse(origin).netloc != host:
                    return JSONResponse({"detail": "CSRF: origin mismatch"}, status_code=403)
            else:
                referer = request.headers.get("referer")
                if referer and urlparse(referer).netloc != host:
                    return JSONResponse({"detail": "CSRF: referer mismatch"}, status_code=403)
            return await call_next(request)

    app.add_middleware(_SecurityHeadersMiddleware)

    from middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)

    app.add_middleware(_CSRFOriginMiddleware)

    app.add_middleware(BrandMiddleware)

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        https_only=https_only,
        same_site="lax",
        max_age=session_max_age,
    )

    # templates + partials
    templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))
    # Удобная глобальная функция: в шаблонах {{ brand }} = request.state.brand
    templates.env.globals["get_brand"] = lambda req: getattr(req.state, "brand", "full")

    # i18n: context-aware перевод и локализованные даты.
    # Локаль берётся из `user`/`current_user` в контексте (EN только для TRAIN_GLOBAL,
    # иначе RU). Для RU перевод 1:1 — поведение существующих шаблонов не меняется.
    try:
        from services.i18n_service import (
            make_jinja_translator,
            make_jinja_dateformat,
            resolve_locale,
        )
    except ImportError:
        from app.services.i18n_service import (
            make_jinja_translator,
            make_jinja_dateformat,
            resolve_locale,
        )
    _translator = make_jinja_translator()
    templates.env.globals["_"] = _translator
    templates.env.globals["gettext"] = _translator
    templates.env.globals["resolve_locale"] = resolve_locale
    templates.env.filters["localdate"] = make_jinja_dateformat()

    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")

    app.include_router(public.router)
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(chat_trener.router)
    app.include_router(settings.router)
    app.include_router(dashboard.router)
    app.include_router(zoom_meetings.router)
    app.include_router(webrtc_meetings.router)
    app.include_router(admin.router)
    app.include_router(admin_prompts.router)
    app.include_router(admin_research.router)
    app.include_router(admin_trainings.router)  # Голосовые тренировки без анализа (админка)
    app.include_router(tts_proxy.router, prefix="/api")
    app.include_router(training_plans.router)  # Роутер планов тренировок
    app.include_router(crm_integration.router)  # Роутер CRM интеграций
    app.include_router(teams.router)  # Роутер команд и приглашений
    app.include_router(team_analytics.router)  # Роутер аналитики команды
    app.include_router(sales.router)  # Роутер панели продаж (Sale Manager)
    app.include_router(analytics.router)  # Роутер аналитики звонков (ИИ-ассистент для РОПа)
    app.include_router(owner_dashboard.router)  # Роутер экрана владельца (Owner Command Center)
    app.include_router(training_catalog.router)  # Каталог тренировок (train-режим)
    app.include_router(training_program.router)  # Программы тренировок для РОПа
    app.include_router(train_report.router)       # Отчёт РОПу по тренировкам (train-режим)
    
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


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

def _safe_log_url(request: Request) -> str:
    """URL для логов без чувствительных частей (секрет вебхука, токены в query)."""
    path = request.url.path
    # Секрет CRM-вебхука в пути: /crm/webhook/{id}/{secret} → маскируем secret
    if path.startswith("/crm/webhook/"):
        parts = path.split("/")
        if len(parts) >= 5:
            parts[4] = "***"
            path = "/".join(parts)
        return path
    # query может содержать token=... → не логируем query целиком, только путь
    if request.url.query:
        return f"{path}?<redacted>"
    return path


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    safe_url = _safe_log_url(request)
    logger.info(f"Запрос: {request.method} {safe_url} request_id={request_id}")
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        logger.info(f"Ответ: {response.status_code} request_id={request_id}")
        return response
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса {request.method} {safe_url} request_id={request_id}: {e}", exc_info=True)
        raise

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    logger.error(f"Необработанное исключение {type(exc).__name__} request_id={request_id}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка сервера", "request_id": request_id},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Обработчик ошибок валидации запросов"""
    logger.error(f"❌ Ошибка валидации запроса: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )
