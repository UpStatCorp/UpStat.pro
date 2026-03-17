import os
import json
import uuid
import asyncio
import httpx
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, and_

from database import get_db, SessionLocal
from deps import require_user
from models import User, CRMIntegration, CRMRecording, Conversation, Message, Attachment, AnalysisTrainingPlan
from services.crm_service import CRMServiceFactory, AmoCRMService, Bitrix24Service, Bitrix24WebhookService
from services.pipeline import run_pipeline, run_pipeline_from_raw_text
from services.notification_service import NotificationService, NotificationType
from services.training_plan_service import TrainingPlanService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm"])

AMOCRM_CLIENT_ID = os.getenv("AMOCRM_CLIENT_ID", "")
AMOCRM_CLIENT_SECRET = os.getenv("AMOCRM_CLIENT_SECRET", "")
AMOCRM_REDIRECT_URI = os.getenv("AMOCRM_REDIRECT_URI", "http://localhost:8000/crm/oauth/callback")

BITRIX24_CLIENT_ID = os.getenv("BITRIX24_CLIENT_ID", "")
BITRIX24_CLIENT_SECRET = os.getenv("BITRIX24_CLIENT_SECRET", "")
BITRIX24_REDIRECT_URI = os.getenv("BITRIX24_REDIRECT_URI", "http://localhost:8000/crm/oauth/callback")

MAX_BATCH_SIZE = 30


@router.get("/crm", response_class=HTMLResponse)
async def crm_page(request: Request, db: Session = Depends(get_db)):
    """Страница управления CRM интеграциями"""
    user = require_user(request, db)

    integrations = db.query(CRMIntegration).filter(
        CRMIntegration.user_id == user.id
    ).order_by(desc(CRMIntegration.created_at)).all()

    supported_crms = CRMServiceFactory.get_supported_crms()

    total_recordings = db.query(func.count(CRMRecording.id)).filter(
        CRMRecording.user_id == user.id
    ).scalar() or 0

    analyzed_recordings = db.query(func.count(CRMRecording.id)).filter(
        CRMRecording.user_id == user.id,
        CRMRecording.sync_status == "completed"
    ).scalar() or 0

    avg_score = db.query(func.avg(CRMRecording.analysis_score)).filter(
        CRMRecording.user_id == user.id,
        CRMRecording.analysis_score.isnot(None)
    ).scalar()

    return request.app.state.templates.TemplateResponse(
        "crm_integration.html",
        {
            "request": request,
            "user": user,
            "integrations": integrations,
            "supported_crms": supported_crms,
            "amocrm_client_id": AMOCRM_CLIENT_ID,
            "bitrix24_client_id": BITRIX24_CLIENT_ID,
            "total_recordings": total_recordings,
            "analyzed_recordings": analyzed_recordings,
            "avg_score": round(avg_score, 1) if avg_score else None,
        }
    )


@router.post("/crm/connect/{crm_type}")
async def connect_crm(crm_type: str, request: Request, db: Session = Depends(get_db)):
    """Начать процесс подключения CRM"""
    user = require_user(request, db)

    if crm_type == "amocrm":
        form_data = await request.form()
        domain = form_data.get("domain", "").strip()
        if not domain:
            raise HTTPException(400, "Домен AmoCRM обязателен")

        integration = CRMIntegration(
            user_id=user.id, crm_type="amocrm",
            crm_name=f"AmoCRM ({domain})", crm_domain=domain, is_active=False,
            client_id=AmoCRMService(CRMIntegration())._encrypt_token(AMOCRM_CLIENT_ID),
            client_secret=AmoCRMService(CRMIntegration())._encrypt_token(AMOCRM_CLIENT_SECRET),
        )
        db.add(integration)
        db.commit()

        state = f"{integration.id}:{uuid.uuid4().hex}"
        service = AmoCRMService(integration)
        oauth_url = service.get_oauth_url(client_id=AMOCRM_CLIENT_ID, redirect_uri=AMOCRM_REDIRECT_URI, state=state)
        return JSONResponse({"oauth_url": oauth_url})

    elif crm_type == "bitrix24":
        form_data = await request.form()
        domain = form_data.get("domain", "").strip()
        if not domain:
            raise HTTPException(400, "Домен Bitrix24 обязателен")
        domain = domain.replace(".bitrix24.ru", "").replace(".bitrix24.com", "")

        integration = CRMIntegration(
            user_id=user.id, crm_type="bitrix24",
            crm_name=f"Bitrix24 ({domain})", crm_domain=f"{domain}.bitrix24.ru", is_active=False,
            client_id=Bitrix24Service(CRMIntegration())._encrypt_token(BITRIX24_CLIENT_ID),
            client_secret=Bitrix24Service(CRMIntegration())._encrypt_token(BITRIX24_CLIENT_SECRET),
        )
        db.add(integration)
        db.commit()

        state = f"{integration.id}:{uuid.uuid4().hex}"
        service = Bitrix24Service(integration)
        oauth_url = service.get_oauth_url(client_id=BITRIX24_CLIENT_ID, redirect_uri=BITRIX24_REDIRECT_URI, state=state)
        return JSONResponse({"oauth_url": oauth_url})

    else:
        raise HTTPException(400, f"CRM тип {crm_type} пока не поддерживается")


@router.post("/crm/connect/bitrix24/webhook")
async def connect_bitrix24_webhook(request: Request, db: Session = Depends(get_db)):
    """Подключить Bitrix24 через входящий вебхук"""
    user = require_user(request, db)
    form_data = await request.form()
    webhook_url = form_data.get("webhook_url", "").strip()

    if not webhook_url:
        raise HTTPException(400, "URL вебхука обязателен")
    if not webhook_url.startswith("http") or "bitrix24" not in webhook_url:
        raise HTTPException(400, "Некорректный URL вебхука Bitrix24")

    try:
        domain = webhook_url.split("//")[1].split("/")[0]
    except Exception:
        raise HTTPException(400, "Не удалось извлечь домен из URL")

    try:
        async with httpx.AsyncClient() as client:
            test_url = f"{webhook_url.rstrip('/')}/user.current.json"
            response = await client.get(test_url)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise HTTPException(400, f"Ошибка вебхука: {data.get('error_description', 'Неизвестная ошибка')}")
    except httpx.HTTPError:
        raise HTTPException(400, "Не удалось подключиться к вебхуку. Проверьте URL.")

    integration = CRMIntegration(
        user_id=user.id, crm_type="bitrix24",
        crm_name=f"Bitrix24 ({domain}) [Вебхук]", crm_domain=domain, is_active=True,
        access_token=Bitrix24WebhookService(CRMIntegration())._encrypt_token(webhook_url),
        refresh_token=None,
    )
    db.add(integration)
    db.commit()
    return JSONResponse({"status": "success", "message": "Bitrix24 подключен через вебхук"})


@router.get("/crm/oauth/callback")
async def oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Callback для OAuth авторизации"""
    if error:
        return HTMLResponse(f'<html><body><h2>Ошибка авторизации</h2><p>{error}</p><a href="/crm">Назад</a></body></html>')

    if not code or not state:
        raise HTTPException(400, "Отсутствует код или state")

    try:
        integration_id = int(state.split(":")[0])
    except Exception:
        raise HTTPException(400, "Некорректный state")

    integration = db.query(CRMIntegration).filter(CRMIntegration.id == integration_id).first()
    if not integration:
        raise HTTPException(404, "Интеграция не найдена")

    if integration.crm_type == "amocrm":
        service = AmoCRMService(integration)
        client_id, client_secret, redirect_uri = AMOCRM_CLIENT_ID, AMOCRM_CLIENT_SECRET, AMOCRM_REDIRECT_URI
    elif integration.crm_type == "bitrix24":
        service = Bitrix24Service(integration)
        client_id, client_secret, redirect_uri = BITRIX24_CLIENT_ID, BITRIX24_CLIENT_SECRET, BITRIX24_REDIRECT_URI
    else:
        return HTMLResponse(f'<html><body><h2>Ошибка</h2><p>Неподдерживаемый тип CRM</p><a href="/crm">Назад</a></body></html>')

    try:
        token_data = await service.exchange_code_for_tokens(
            code=code, redirect_uri=redirect_uri, client_id=client_id, client_secret=client_secret,
            domain=integration.crm_domain.replace(".bitrix24.ru", "").replace(".amocrm.ru", ""),
        )
        if integration.crm_type == "bitrix24" and "domain" in token_data:
            integration.crm_domain = token_data["domain"]

        service.save_tokens(db, access_token=token_data["access_token"],
                            refresh_token=token_data.get("refresh_token"), expires_in=token_data.get("expires_in"))
        integration.is_active = True
        integration.webhook_url = redirect_uri
        db.commit()
        return RedirectResponse("/crm?success=connected", status_code=302)
    except Exception as e:
        return HTMLResponse(f'<html><body><h2>Ошибка подключения</h2><p>{e}</p><a href="/crm">Назад</a></body></html>')


# ── Синхронизация ──────────────────────────────────────

_sync_status: dict = {}

@router.post("/crm/sync/{integration_id}")
async def sync_recordings(integration_id: int, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """Синхронизировать записи из CRM"""
    logger.info(f"[Sync] POST /crm/sync/{integration_id} received")
    user = require_user(request, db)
    integration = db.query(CRMIntegration).filter(CRMIntegration.id == integration_id, CRMIntegration.user_id == user.id).first()
    if not integration or not integration.is_active:
        logger.warning(f"[Sync] Integration {integration_id} not found or inactive")
        raise HTTPException(404, "Интеграция не найдена или не активна")
    logger.info(f"[Sync] Starting sync for integration {integration_id} ({integration.crm_type})")
    _sync_status[integration_id] = {"phase": "starting", "found": 0, "saved": 0, "done": False, "error": None}
    background_tasks.add_task(sync_crm_recordings_task, integration_id)
    return JSONResponse({"status": "started"})


@router.get("/crm/sync-status/{integration_id}")
async def sync_status(integration_id: int, request: Request, db: Session = Depends(get_db)):
    """Проверить статус текущей синхронизации"""
    user = require_user(request, db)
    integration = db.query(CRMIntegration).filter(CRMIntegration.id == integration_id, CRMIntegration.user_id == user.id).first()
    if not integration:
        raise HTTPException(404)
    status = _sync_status.get(integration_id, {"phase": "idle", "done": True})
    return JSONResponse({
        **status,
        "recordings_count": integration.recordings_count,
        "last_sync_at": integration.last_sync_at.strftime("%d.%m %H:%M") if integration.last_sync_at else None,
    })


async def sync_crm_recordings_task(integration_id: int):
    """Фоновая задача синхронизации записей"""
    logger.info(f"[Sync] Background task started for integration {integration_id}")
    status = _sync_status.setdefault(integration_id, {})
    status.update(phase="fetching", found=0, saved=0, done=False, error=None)
    db = SessionLocal()
    try:
        integration = db.query(CRMIntegration).filter(CRMIntegration.id == integration_id).first()
        if not integration:
            status.update(phase="error", error="Интеграция не найдена", done=True)
            logger.warning(f"[Sync] Integration {integration_id} not found in DB")
            return
        logger.info(f"[Sync] Creating service for {integration.crm_type}, domain={integration.crm_domain}")
        service = CRMServiceFactory.create(integration)
        status["phase"] = "fetching"
        logger.info(f"[Sync] Calling get_recordings...")
        recordings_data = await service.get_recordings(db)
        logger.info(f"[Sync] get_recordings returned {len(recordings_data)} items")
        status.update(phase="saving", found=len(recordings_data))

        new_count = 0
        updated_count = 0
        for i, rec_data in enumerate(recordings_data):
            existing = db.query(CRMRecording).filter(
                CRMRecording.integration_id == integration.id,
                CRMRecording.crm_record_id == rec_data["crm_record_id"],
            ).first()
            if existing:
                changed = False
                if (existing.duration_seconds or 0) <= 1 and rec_data["duration_seconds"] > 1:
                    existing.duration_seconds = rec_data["duration_seconds"]
                    changed = True
                if not existing.recording_url and rec_data.get("recording_url"):
                    existing.recording_url = rec_data["recording_url"]
                    changed = True
                if not existing.manager_name and rec_data.get("manager_name"):
                    existing.manager_name = rec_data["manager_name"]
                    changed = True
                if not existing.client_name and rec_data.get("client_name"):
                    existing.client_name = rec_data["client_name"]
                    changed = True
                if changed:
                    updated_count += 1
            else:
                recording = CRMRecording(
                    integration_id=integration.id, user_id=integration.user_id,
                    crm_record_id=rec_data["crm_record_id"], crm_call_id=rec_data.get("crm_call_id"),
                    call_date=rec_data["call_date"], duration_seconds=rec_data["duration_seconds"],
                    direction=rec_data["direction"], recording_url=rec_data["recording_url"],
                    manager_name=rec_data.get("manager_name"), client_name=rec_data.get("client_name"),
                    client_phone=rec_data.get("client_phone"), client_company=rec_data.get("client_company"),
                    crm_metadata_json=json.dumps(rec_data.get("crm_metadata", {}), ensure_ascii=False),
                    sync_status="available",
                )
                db.add(recording)
                new_count += 1
            status["saved"] = new_count

        integration.last_sync_at = datetime.utcnow()
        integration.recordings_count = db.query(CRMRecording).filter(CRMRecording.integration_id == integration.id).count()
        db.commit()
        status.update(phase="done", saved=new_count, done=True)
        logger.info(f"[Sync] Integration {integration_id}: {new_count} new, {updated_count} updated")

        if new_count > 0 or updated_count > 0:
            parts = []
            if new_count > 0:
                parts.append(f"{new_count} новых")
            if updated_count > 0:
                parts.append(f"{updated_count} обновлённых")
            try:
                notification_service = NotificationService()
                notification_service.add_notification(
                    user_id=integration.user_id,
                    type=NotificationType.SUCCESS,
                    title="Синхронизация завершена",
                    message=f"Записей: {', '.join(parts)} из {integration.crm_name}",
                    action_url="/crm/recordings",
                    action_label="Посмотреть записи",
                )
            except Exception as ne:
                logger.warning(f"Could not send notification: {ne}")
    except Exception as e:
        logger.error(f"Sync error for integration {integration_id}: {e}", exc_info=True)
        status.update(phase="error", error=str(e)[:200], done=True)
    finally:
        db.close()


# ── Синхронизация чатов ─────────────────────────────────

@router.post("/crm/sync-chats/{integration_id}")
async def sync_chats(integration_id: int, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """Синхронизировать чаты из Открытых Линий Bitrix24"""
    user = require_user(request, db)
    integration = db.query(CRMIntegration).filter(CRMIntegration.id == integration_id, CRMIntegration.user_id == user.id).first()
    if not integration or not integration.is_active:
        raise HTTPException(404, "Интеграция не найдена или не активна")
    if "bitrix24" not in integration.crm_type:
        raise HTTPException(400, "Синхронизация чатов поддерживается только для Bitrix24")
    background_tasks.add_task(sync_crm_chats_task, integration_id)
    return JSONResponse({"status": "started", "message": "Синхронизация чатов запущена"})


async def sync_crm_chats_task(integration_id: int):
    """Фоновая задача синхронизации чатов из Открытых Линий"""
    db = SessionLocal()
    try:
        integration = db.query(CRMIntegration).filter(CRMIntegration.id == integration_id).first()
        if not integration:
            return
        service = CRMServiceFactory.create(integration)
        
        if not hasattr(service, 'get_chats'):
            logger.warning(f"Service for integration {integration_id} does not support chats")
            return
        
        chats_data = await service.get_chats(db)
        
        new_count = 0
        for chat_data in chats_data:
            existing = db.query(CRMRecording).filter(
                CRMRecording.integration_id == integration.id,
                CRMRecording.crm_record_id == chat_data["crm_record_id"],
                CRMRecording.record_type == "chat",
            ).first()
            if not existing:
                recording = CRMRecording(
                    integration_id=integration.id, user_id=integration.user_id,
                    crm_record_id=chat_data["crm_record_id"],
                    crm_call_id=chat_data.get("crm_call_id"),
                    call_date=chat_data["call_date"],
                    duration_seconds=chat_data.get("duration_seconds", 0),
                    direction=chat_data.get("direction", "inbound"),
                    recording_url=None,
                    record_type="chat",
                    chat_text=chat_data.get("chat_text", ""),
                    manager_name=chat_data.get("manager_name"),
                    client_name=chat_data.get("client_name"),
                    client_phone=chat_data.get("client_phone"),
                    client_company=chat_data.get("client_company"),
                    crm_metadata_json=json.dumps(chat_data.get("crm_metadata", {}), ensure_ascii=False),
                    sync_status="available",
                )
                db.add(recording)
                new_count += 1

        integration.last_sync_at = datetime.utcnow()
        integration.recordings_count = db.query(CRMRecording).filter(CRMRecording.integration_id == integration.id).count()
        db.commit()

        if new_count > 0:
            try:
                notification_service = NotificationService()
                notification_service.add_notification(
                    user_id=integration.user_id,
                    type=NotificationType.SUCCESS,
                    title="Синхронизация чатов завершена",
                    message=f"Найдено {new_count} новых чатов из {integration.crm_name}",
                    action_url="/crm/recordings?record_type=chat",
                    action_label="Посмотреть чаты",
                )
            except Exception as ne:
                logger.warning(f"Could not send notification: {ne}")
    except Exception as e:
        import traceback
        logger.error(f"Chat sync error for integration {integration_id}: {e}\n{traceback.format_exc()}")
    finally:
        db.close()


# ── Записи: список с фильтрами и пагинацией ───────────

@router.get("/crm/recordings", response_class=HTMLResponse)
async def recordings_page(
    request: Request, db: Session = Depends(get_db),
    status: Optional[str] = Query(None), integration_id: Optional[int] = Query(None),
    manager: Optional[str] = Query(None),
    record_type: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1), per_page: int = Query(50, ge=10, le=200),
):
    """Страница со списком записей из CRM"""
    user = require_user(request, db)
    query = db.query(CRMRecording).filter(CRMRecording.user_id == user.id)

    if status:
        query = query.filter(CRMRecording.sync_status == status)
    if integration_id:
        query = query.filter(CRMRecording.integration_id == integration_id)
    if manager:
        query = query.filter(CRMRecording.manager_name == manager)
    if record_type:
        query = query.filter(CRMRecording.record_type == record_type)
    if date_from:
        try:
            query = query.filter(CRMRecording.call_date >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(CRMRecording.call_date < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass

    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)

    recordings = query.order_by(desc(CRMRecording.call_date)).offset((page - 1) * per_page).limit(per_page).all()

    integrations = db.query(CRMIntegration).filter(CRMIntegration.user_id == user.id, CRMIntegration.is_active == True).all()

    managers_q = db.query(CRMRecording.manager_name).filter(
        CRMRecording.user_id == user.id, CRMRecording.manager_name.isnot(None), CRMRecording.manager_name != ""
    ).distinct().all()
    manager_names = sorted([m[0] for m in managers_q])

    stats = {
        "total": total_count,
        "available": db.query(func.count(CRMRecording.id)).filter(CRMRecording.user_id == user.id, CRMRecording.sync_status == "available").scalar() or 0,
        "analyzing": db.query(func.count(CRMRecording.id)).filter(CRMRecording.user_id == user.id, CRMRecording.sync_status.in_(["downloading", "analyzing"])).scalar() or 0,
        "completed": db.query(func.count(CRMRecording.id)).filter(CRMRecording.user_id == user.id, CRMRecording.sync_status == "completed").scalar() or 0,
        "failed": db.query(func.count(CRMRecording.id)).filter(CRMRecording.user_id == user.id, CRMRecording.sync_status == "failed").scalar() or 0,
    }

    active_batch = db.query(CRMRecording).filter(
        CRMRecording.user_id == user.id, CRMRecording.batch_id.isnot(None),
        CRMRecording.sync_status.in_(["downloading", "analyzing"]),
    ).first()

    batch_progress = None
    if active_batch and active_batch.batch_id:
        bid = active_batch.batch_id
        bt = db.query(func.count(CRMRecording.id)).filter(CRMRecording.batch_id == bid).scalar() or 0
        bd = db.query(func.count(CRMRecording.id)).filter(CRMRecording.batch_id == bid, CRMRecording.sync_status.in_(["completed", "failed"])).scalar() or 0
        batch_progress = {"batch_id": bid, "total": bt, "done": bd, "percent": round(bd / bt * 100) if bt else 0}

    return request.app.state.templates.TemplateResponse("crm_recordings.html", {
        "request": request, "user": user, "recordings": recordings, "integrations": integrations,
        "manager_names": manager_names, "current_status": status, "current_integration_id": integration_id,
        "current_manager": manager, "current_record_type": record_type or "", "current_date_from": date_from or "", "current_date_to": date_to or "",
        "page": page, "per_page": per_page, "total_pages": total_pages, "total_count": total_count,
        "stats": stats, "batch_progress": batch_progress,
    })


# ── Анализ одной записи ───────────────────────────────

@router.post("/crm/recordings/{recording_id}/analyze")
async def analyze_recording_endpoint(recording_id: int, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """Начать анализ записи"""
    user = require_user(request, db)
    recording = db.query(CRMRecording).filter(CRMRecording.id == recording_id, CRMRecording.user_id == user.id).first()
    if not recording:
        raise HTTPException(404, "Запись не найдена")
    if recording.sync_status in ["downloading", "analyzing"]:
        raise HTTPException(400, f"Запись уже в обработке: {recording.sync_status}")

    recording.sync_status = "downloading"
    recording.error_message = None
    db.commit()

    background_tasks.add_task(analyze_recording_task, recording_id)
    return JSONResponse({"status": "started"})


# ── Пакетный анализ (Вариант А) ───────────────────────

@router.post("/crm/recordings/batch-analyze")
async def batch_analyze_recordings(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Запуск пакетного анализа выбранных записей"""
    user = require_user(request, db)
    form_data = await request.form()
    recording_ids_raw = form_data.get("recording_ids", "")

    if not recording_ids_raw:
        raise HTTPException(400, "Не выбрано ни одной записи")
    try:
        recording_ids = [int(rid) for rid in recording_ids_raw.split(",") if rid.strip()]
    except ValueError:
        raise HTTPException(400, "Некорректные ID записей")

    if len(recording_ids) > MAX_BATCH_SIZE:
        raise HTTPException(400, f"Максимум {MAX_BATCH_SIZE} записей за раз. Выбрано: {len(recording_ids)}")

    recordings = db.query(CRMRecording).filter(
        CRMRecording.id.in_(recording_ids), CRMRecording.user_id == user.id,
        CRMRecording.sync_status.in_(["available", "failed"]),
    ).all()
    if not recordings:
        raise HTTPException(400, "Нет записей, доступных для анализа")

    batch_id = uuid.uuid4().hex[:12]
    for rec in recordings:
        rec.sync_status = "downloading"
        rec.batch_id = batch_id
        rec.error_message = None
    db.commit()

    background_tasks.add_task(batch_analyze_task, batch_id, user.id)
    return JSONResponse({"status": "started", "batch_id": batch_id, "count": len(recordings),
                         "message": f"Запущен анализ {len(recordings)} записей"})


@router.get("/crm/batch-progress/{batch_id}")
async def get_batch_progress(batch_id: str, request: Request, db: Session = Depends(get_db)):
    """Получить прогресс пакетного анализа"""
    user = require_user(request, db)
    recs = db.query(CRMRecording).filter(CRMRecording.batch_id == batch_id, CRMRecording.user_id == user.id).all()
    if not recs:
        raise HTTPException(404, "Батч не найден")

    total = len(recs)
    completed = sum(1 for r in recs if r.sync_status == "completed")
    failed = sum(1 for r in recs if r.sync_status == "failed")
    done = completed + failed
    current = next((r for r in recs if r.sync_status in ["downloading", "analyzing"]), None)

    return JSONResponse({
        "batch_id": batch_id, "total": total, "completed": completed, "failed": failed,
        "done": done, "percent": round(done / total * 100) if total else 0, "is_finished": done >= total,
        "current": {"id": current.id, "manager": current.manager_name or "\u2014",
                     "date": current.call_date.strftime("%d.%m.%Y %H:%M") if current.call_date else "",
                     "status": current.sync_status} if current else None,
    })


async def batch_analyze_task(batch_id: str, user_id: int):
    """Фоновая: последовательный анализ всех записей в батче"""
    db = SessionLocal()
    try:
        recordings = db.query(CRMRecording).filter(
            CRMRecording.batch_id == batch_id, CRMRecording.user_id == user_id,
        ).order_by(CRMRecording.call_date).all()

        for recording in recordings:
            try:
                db.refresh(recording)
                await _process_single_recording(db, recording)
            except Exception as e:
                logger.error(f"Batch analysis error for recording {recording.id}: {e}")
                recording.sync_status = "failed"
                recording.error_message = str(e)[:500]
                db.commit()

        completed = sum(1 for r in recordings if r.sync_status == "completed")
        failed = sum(1 for r in recordings if r.sync_status == "failed")
        try:
            notification_service = NotificationService()
            notification_service.add_notification(
                user_id=user_id,
                type=NotificationType.SUCCESS if failed == 0 else NotificationType.WARNING,
                title="Пакетный анализ завершён",
                message=f"Проанализировано: {completed}, ошибок: {failed} из {len(recordings)}",
                action_url="/crm/recordings?status=completed",
                action_label="Посмотреть результаты",
            )
        except Exception as ne:
            logger.warning(f"Could not send notification: {ne}")
    except Exception as e:
        logger.error(f"Batch task fatal error: {e}")
    finally:
        db.close()


# ── Webhook (Вариант Б — подготовка) ──────────────────

@router.post("/crm/webhook/{integration_id}/{webhook_secret}")
async def crm_webhook_receiver(integration_id: int, webhook_secret: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Принимает webhook от CRM при завершении звонка (ONVOXIMPLANTCALLEND)"""
    integration = db.query(CRMIntegration).filter(CRMIntegration.id == integration_id, CRMIntegration.is_active == True).first()
    if not integration:
        raise HTTPException(404, "Integration not found")
    if not integration.webhook_secret or integration.webhook_secret != webhook_secret:
        raise HTTPException(403, "Invalid webhook secret")

    body = {}
    try:
        content_type = request.headers.get("content-type", "")
        if "json" in content_type:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
    except Exception:
        pass
    logger.info(f"[Webhook] Received ONVOXIMPLANTCALLEND for integration {integration_id}: {json.dumps(body, default=str)[:500]}")
    background_tasks.add_task(_delayed_sync, integration_id)
    return JSONResponse({"status": "ok"})


async def _delayed_sync(integration_id: int, delay: int = 15):
    """Запустить синхронизацию с задержкой — даём Bitrix24 время обработать запись"""
    import asyncio
    logger.info(f"[Webhook] Waiting {delay}s for recording to be processed...")
    await asyncio.sleep(delay)
    await sync_crm_recordings_task(integration_id)


@router.post("/crm/integrations/{integration_id}/enable-webhook")
async def enable_webhook(integration_id: int, request: Request, db: Session = Depends(get_db)):
    """Включить webhook для авто-синхронизации (Вариант Б)"""
    user = require_user(request, db)
    integration = db.query(CRMIntegration).filter(CRMIntegration.id == integration_id, CRMIntegration.user_id == user.id).first()
    if not integration:
        raise HTTPException(404, "Интеграция не найдена")

    secret = uuid.uuid4().hex[:16]
    integration.webhook_secret = secret
    base_url = os.getenv("APP_BASE_URL", str(request.base_url).rstrip("/"))
    webhook_url_out = f"{base_url}/crm/webhook/{integration.id}/{secret}"
    db.commit()
    return JSONResponse({"webhook_url": webhook_url_out, "secret": secret,
                         "instructions": "Укажите этот URL в настройках CRM для получения уведомлений о новых звонках."})


# ── Общая логика обработки одной записи ────────────────

async def _process_single_recording(db: Session, recording: CRMRecording):
    """Скачивает/обрабатывает запись (звонок или чат) и создаёт план тренировок"""
    integration = recording.integration
    service = CRMServiceFactory.create(integration)
    
    is_chat = getattr(recording, 'record_type', 'call') == 'chat'
    
    if is_chat:
        await _process_chat_recording(db, recording, integration)
    else:
        await _process_call_recording(db, recording, integration, service)


async def _process_chat_recording(db: Session, recording: CRMRecording, integration: CRMIntegration):
    """Обрабатывает чат-запись: текст → анализ через run_pipeline_from_raw_text"""
    recording.sync_status = "analyzing"
    db.commit()
    
    chat_text = recording.chat_text or ""
    if not chat_text.strip():
        recording.sync_status = "failed"
        recording.error_message = "Текст чата пуст"
        db.commit()
        return
    
    type_label = "Чат"
    conversation = Conversation(
        user_id=recording.user_id,
        title=f"CRM {type_label}: {recording.manager_name or 'Переписка'} \u2014 {recording.call_date.strftime('%d.%m.%Y %H:%M')}",
    )
    db.add(conversation)
    db.flush()
    recording.conversation_id = conversation.id
    
    msg = Message(
        conversation_id=conversation.id, user_id=recording.user_id, role="user",
        text=(f"Чат из {integration.crm_name}\n"
              f"Дата: {recording.call_date.strftime('%d.%m.%Y %H:%M')}\n"
              f"Менеджер: {recording.manager_name or 'Не указан'}\n"
              f"Клиент: {recording.client_name or 'Не указан'}\n"
              f"Компания: {recording.client_company or 'Не указана'}\n"
              f"Направление: {'Входящий' if recording.direction == 'inbound' else 'Исходящий'}\n\n"
              f"--- Переписка ---\n{chat_text}"),
    )
    db.add(msg)
    db.commit()
    
    try:
        await run_pipeline_from_raw_text(recording.user_id, conversation.id, chat_text)
    except Exception as e:
        recording.sync_status = "failed"
        recording.error_message = f"Ошибка анализа чата: {str(e)[:400]}"
        db.commit()
        return

    report_msg = db.query(Message).filter(
        Message.conversation_id == conversation.id, Message.role == "bot",
    ).order_by(desc(Message.id)).first()

    analysis_score = None
    if report_msg and report_msg.text:
        analysis_score = _extract_score_from_report(report_msg.text)

    recording.sync_status = "completed"
    recording.analyzed_at = datetime.utcnow()
    recording.analysis_score = analysis_score

    if report_msg:
        try:
            plan = await TrainingPlanService.create_training_plan(
                db=db, user_id=recording.user_id,
                report_message_id=report_msg.id, analysis_text=report_msg.text,
            )
            recording.training_plan_id = plan.id
            logger.info(f"Auto-created training plan {plan.id} for CRM chat {recording.id}")
        except Exception as e:
            logger.warning(f"Could not create training plan for chat {recording.id}: {e}")
    
    integration.analyzed_count = db.query(CRMRecording).filter(
        CRMRecording.integration_id == integration.id, CRMRecording.sync_status == "completed",
    ).count()
    db.commit()


async def _process_call_recording(db: Session, recording: CRMRecording, integration: CRMIntegration, service):
    """Обрабатывает звонок: скачивание → транскрибация → анализ"""
    upload_dir = os.path.abspath("uploads")
    user_dir = os.path.join(upload_dir, str(recording.user_id), "crm_recordings")
    os.makedirs(user_dir, exist_ok=True)

    file_ext = ".mp3"
    if recording.recording_url:
        url_path = recording.recording_url.split("?")[0]
        url_parts = url_path.split(".")
        if len(url_parts) > 1:
            possible_ext = f".{url_parts[-1].lower()}"
            if possible_ext in [".mp3", ".wav", ".m4a", ".opus", ".ogg", ".flac", ".webm"]:
                file_ext = possible_ext

    file_name = f"crm_{recording.crm_record_id}_{uuid.uuid4().hex[:8]}{file_ext}"
    file_path = os.path.join(user_dir, file_name)

    recording.sync_status = "downloading"
    db.commit()

    download_url = recording.recording_url
    try:
        success = False

        # crm_show_file.php не работает через webhook — сразу идём на voximplant
        if "crm_show_file.php" not in (download_url or ""):
            success = await service.download_recording(download_url, file_path)

            if success and os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    head = f.read(15)
                if head.startswith(b"<!DOC") or head.startswith(b"<html") or head.startswith(b"<!"):
                    os.remove(file_path)
                    success = False
                    logger.warning(f"Downloaded file is HTML, not audio for recording {recording.id}")
        else:
            logger.info(f"Skipping crm_show_file.php URL for recording {recording.id}, using voximplant")

        if not success and hasattr(service, '_try_voximplant_download'):
            logger.info(f"Trying voximplant fallback for recording {recording.id}")
            voxi_url = await service._try_voximplant_download(recording)
            if voxi_url:
                success = await service.download_recording(voxi_url, file_path)
                if success and os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        head = f.read(15)
                    if head.startswith(b"<!DOC") or head.startswith(b"<html") or head.startswith(b"<!"):
                        os.remove(file_path)
                        success = False
                    else:
                        recording.recording_url = voxi_url

        if not success:
            raise Exception("Не удалось скачать аудио файл")

        file_size = os.path.getsize(file_path)
        recording.local_file_path = os.path.relpath(file_path, start=upload_dir)
        recording.file_size_bytes = file_size
        recording.downloaded_at = datetime.utcnow()
        recording.sync_status = "analyzing"
        db.commit()
    except Exception as e:
        recording.sync_status = "failed"
        recording.error_message = f"Ошибка скачивания: {str(e)[:400]}"
        db.commit()
        return

    conversation = Conversation(
        user_id=recording.user_id,
        title=f"CRM: {recording.manager_name or 'Звонок'} \u2014 {recording.call_date.strftime('%d.%m.%Y %H:%M')}",
    )
    db.add(conversation)
    db.flush()
    recording.conversation_id = conversation.id

    msg = Message(
        conversation_id=conversation.id, user_id=recording.user_id, role="user",
        text=(f"Звонок из {integration.crm_name}\n"
              f"Дата: {recording.call_date.strftime('%d.%m.%Y %H:%M')}\n"
              f"Менеджер: {recording.manager_name or 'Не указан'}\n"
              f"Клиент: {recording.client_name or 'Не указан'}\n"
              f"Компания: {recording.client_company or 'Не указана'}\n"
              f"Направление: {'Входящий' if recording.direction == 'inbound' else 'Исходящий'}\n"
              f"Длительность: {recording.duration_seconds // 60}:{recording.duration_seconds % 60:02d}"),
    )
    db.add(msg)
    db.flush()

    attachment = Attachment(
        message_id=msg.id, file_name=file_name,
        mime_type="audio/mpeg" if file_ext == ".mp3" else f"audio/{file_ext[1:]}",
        size_bytes=file_size, storage_key=recording.local_file_path,
    )
    db.add(attachment)
    db.commit()

    try:
        await run_pipeline(recording.user_id, conversation.id, attachment.id)
    except Exception as e:
        recording.sync_status = "failed"
        recording.error_message = f"Ошибка анализа: {str(e)[:400]}"
        db.commit()
        return

    report_msg = db.query(Message).filter(
        Message.conversation_id == conversation.id, Message.role == "bot",
    ).order_by(desc(Message.id)).first()

    analysis_score = None
    if report_msg and report_msg.text:
        analysis_score = _extract_score_from_report(report_msg.text)

    recording.sync_status = "completed"
    recording.analyzed_at = datetime.utcnow()
    recording.analysis_score = analysis_score

    if report_msg:
        try:
            plan = await TrainingPlanService.create_training_plan(
                db=db, user_id=recording.user_id,
                report_message_id=report_msg.id, analysis_text=report_msg.text,
            )
            recording.training_plan_id = plan.id
            logger.info(f"Auto-created training plan {plan.id} for CRM recording {recording.id}")
        except Exception as e:
            logger.warning(f"Could not create training plan for recording {recording.id}: {e}")

    integration.analyzed_count = db.query(CRMRecording).filter(
        CRMRecording.integration_id == integration.id, CRMRecording.sync_status == "completed",
    ).count()
    db.commit()


async def analyze_recording_task(recording_id: int):
    """Фоновая задача: анализ одной записи"""
    db = SessionLocal()
    try:
        recording = db.query(CRMRecording).filter(CRMRecording.id == recording_id).first()
        if not recording:
            return
        await _process_single_recording(db, recording)

        if not recording.batch_id:
            try:
                notification_service = NotificationService()
                if recording.sync_status == "completed":
                    score_text = f" — Оценка: {recording.analysis_score}/100" if recording.analysis_score else ""
                    notif_title = "Анализ завершён"
                    notif_msg = f"Анализ записи от {recording.call_date.strftime('%d.%m.%Y %H:%M')} завершён{score_text}"
                    notif_type = NotificationType.SUCCESS
                    notif_url = f"/chat?conversation_id={recording.conversation_id}" if recording.conversation_id else "/crm/recordings"
                else:
                    notif_title = "Ошибка анализа"
                    notif_msg = recording.error_message or f"Не удалось проанализировать запись от {recording.call_date.strftime('%d.%m.%Y %H:%M')}"
                    notif_type = NotificationType.ERROR
                    notif_url = "/crm/recordings"
                notification_service.add_notification(
                    user_id=recording.user_id,
                    type=notif_type,
                    title=notif_title,
                    message=notif_msg,
                    action_url=notif_url,
                    action_label="Посмотреть результат" if recording.sync_status == "completed" else "Перейти к записям",
                )
            except Exception as ne:
                logger.warning(f"Could not send notification: {ne}")
    except Exception as e:
        logger.error(f"Analyze recording task error: {e}")
        try:
            rec = db.query(CRMRecording).filter(CRMRecording.id == recording_id).first()
            if rec:
                rec.sync_status = "failed"
                rec.error_message = str(e)[:500]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _extract_score_from_report(report_text: str) -> Optional[int]:
    """Извлекает числовую оценку из текста отчёта"""
    import re
    patterns = [
        r'(?:Общая оценка|Итоговая оценка|Оценка|Score|Балл)[:\s]*(\d{1,3})\s*(?:/\s*100|из\s*100|баллов)?',
        r'(\d{1,3})\s*(?:/\s*100|из\s*100)\s*(?:баллов|points)?',
        r'(?:Итого|Результат)[:\s]*(\d{1,3})',
    ]
    for pattern in patterns:
        match = re.search(pattern, report_text, re.IGNORECASE)
        if match:
            score = int(match.group(1))
            if 0 <= score <= 100:
                return score
    return None


# ── Удаление интеграции ────────────────────────────────

@router.delete("/crm/integrations/{integration_id}")
async def delete_integration(integration_id: int, request: Request, db: Session = Depends(get_db)):
    """Удалить интеграцию"""
    user = require_user(request, db)
    integration = db.query(CRMIntegration).filter(CRMIntegration.id == integration_id, CRMIntegration.user_id == user.id).first()
    if not integration:
        raise HTTPException(404, "Интеграция не найдена")
    db.delete(integration)
    db.commit()
    return JSONResponse({"status": "deleted"})


# ── Диагностика AmoCRM API ────────────────────────────

@router.get("/crm/debug/{integration_id}")
async def debug_crm_api(integration_id: int, request: Request, db: Session = Depends(get_db)):
    """Диагностика: показывает что возвращает AmoCRM API (звонки, notes, leads)"""
    user = require_user(request, db)
    integration = db.query(CRMIntegration).filter(
        CRMIntegration.id == integration_id, CRMIntegration.user_id == user.id
    ).first()
    if not integration or not integration.is_active:
        raise HTTPException(404, "Интеграция не найдена или не активна")

    service = CRMServiceFactory.create(integration)
    if not hasattr(service, "debug_api"):
        raise HTTPException(400, "Debug не поддерживается для этого типа CRM")

    debug_data = await service.debug_api()
    return JSONResponse(debug_data, media_type="application/json")
