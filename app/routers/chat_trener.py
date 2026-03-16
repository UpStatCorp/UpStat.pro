import os
import uuid
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Conversation, Message, Attachment
from deps import require_user
from services.pipeline_trener import run_pipeline_trener, run_pipeline_from_text_trener, run_pipeline_from_raw_text_trener
from services.image_pipeline import run_pipeline_from_images_trener
from services.error_handler import ErrorHandler, ValidationError, FileProcessingError
from utils.file_validator import FileValidator, validate_uploaded_file

import io, zipfile
from datetime import datetime
from fastapi.responses import StreamingResponse


router = APIRouter(tags=["chat_trener"])
UPLOAD_DIR = os.path.abspath("uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_MIME = {
    "image/png","image/jpeg","image/webp","image/gif",
    "application/pdf","text/plain","application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",  # .doc
    # Расширенный список аудио форматов
    "audio/mpeg","audio/mp3","audio/wav","audio/x-wav","audio/wave",
    "audio/m4a","audio/x-m4a","audio/mp4","audio/aac","audio/x-aac",
    "audio/opus","audio/ogg","audio/ogg; codecs=opus","audio/vorbis",
    "audio/flac","audio/x-flac","audio/x-ms-wma","audio/wma",
    "audio/aiff","audio/x-aiff","audio/amr","audio/3gpp","audio/3gp",
    "audio/x-matroska","audio/webm","audio/x-m4b","audio/m4b",
    "audio/basic","audio/x-au","audio/midi","audio/mid","audio/x-midi"
}
MAX_FILE_MB = 25


def _secure_filename(name: str) -> str:
    name = (name or "").strip().replace("\\", "_").replace("/", "_")
    return name[:120] or "file"


# Минимальная длина текста для автоматического запуска анализа (символов)
MIN_TEXT_LENGTH_FOR_ANALYSIS = 100


def _looks_like_transcript(text: str) -> bool:
    """Определяет, похож ли текст на транскрибацию звонка для анализа"""
    if len(text) < MIN_TEXT_LENGTH_FOR_ANALYSIS:
        return False
    return True


def scripted_reply(text: str, files_count: int, user_name: str) -> Optional[str]:
    t = (text or "").lower().strip()
    if not t and files_count > 0:
        return None  # если пришло аудио/скриншоты — пайплайн всё скажет сам
    if any(w in t for w in ["привет", "здравствуйте", "hello", "hi"]):
        return (f"Привет, {user_name}! Пришли запись звонка (mp3/m4a/wav) — я расшифрую и сделаю анализ по чек-листам.\n"
                "📸 Также можно отправить скриншоты переписки с клиентом — я распознаю текст и проанализирую.\n"
                "Или вставь текст транскрибации прямо в чат / отправь файл (txt/docx).")
    if t and not _looks_like_transcript(text or ""):
        return ("Принял. Если есть запись разговора — прикрепи аудио файл, запущу анализ.\n"
                "📸 Также можно отправить скриншоты переписки — я распознаю и проанализирую.\n"
                "Или вставь текст транскрибации прямо в чат / отправь файл (txt/docx).")
    return None


@router.get("/chat_trener", response_class=HTMLResponse)
def chat_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    # Ищем конкретно диалог тренера
    conv = db.query(Conversation).filter(
        Conversation.user_id == user.id, 
        Conversation.title == "Чат Тренера"
    ).order_by(Conversation.id.desc()).first()
    if not conv:
        conv = Conversation(user_id=user.id, title="Чат Тренера")
        db.add(conv); db.commit()

    has_msgs = db.query(Message).filter(Message.conversation_id == conv.id).count() > 0
    if not has_msgs:
        hello = Message(conversation_id=conv.id, user_id=None, role="bot",
                        text=f"Привет, {user.name}! Я — ИИ-тренер по продажам. Вот что я умею:\n\n"
                             "🎙 Аудио звонков (mp3/m4a/wav/opus) — расшифрую и сделаю анализ по чек-листам\n\n"
                             "📸 Скриншоты переписок — распознаю текст, определю кто менеджер, кто клиент, и проанализирую\n"
                             "   💡 Совет: делайте скриншоты со стороны менеджера — так ваши сообщения будут справа, "
                             "и я точно определю роли. Можно отправить несколько скриншотов одной переписки сразу.\n\n"
                             "📝 Текст транскрибации — вставьте прямо в чат или отправьте файл (txt/docx)\n\n"
                             "Просто прикрепите файл и отправьте — я всё сделаю автоматически! 🚀")
        db.add(hello); db.commit()

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
        .limit(400)
        .all()
    )
    return request.app.state.templates.TemplateResponse(
        "chat_trener.html", {"request": request, "user": user, "conversation": conv, "messages": messages}
    )


@router.get("/chat_trener/poll", response_class=HTMLResponse)
def chat_poll(request: Request, last_id: int = 0, db: Session = Depends(get_db)):
    """HTMX-пуллинг: если новых сообщений нет — 204, иначе вернём partial."""
    user = require_user(request, db)
    print(f"Poll request from user {user.id}, last_id: {last_id}")
    
    # Ищем конкретно диалог тренера
    conv = db.query(Conversation).filter(
        Conversation.user_id == user.id, 
        Conversation.title == "Чат Тренера"
    ).order_by(Conversation.id.desc()).first()
    
    if not conv:
        print(f"No conversation found for user {user.id}")
        raise HTTPException(404)
    
    # Получаем количество сообщений через запрос к БД
    msg_count = db.query(Message).filter(Message.conversation_id == conv.id).count()
    print(f"Found conversation {conv.id} with {msg_count} messages")
    
    latest = db.query(Message.id).filter(Message.conversation_id == conv.id).order_by(Message.id.desc()).first()
    if not latest or latest[0] <= last_id:
        print(f"No new messages (latest: {latest[0] if latest else 'None'}, last_id: {last_id})")
        return Response(status_code=204)
    
    # Возвращаем только новые сообщения после last_id
    new_messages = db.query(Message).filter(
        Message.conversation_id == conv.id,
        Message.id > last_id
    ).order_by(Message.created_at.asc()).all()
    
    print(f"Returning {len(new_messages)} new messages after id {last_id}")
    
    if not new_messages:
        return Response(status_code=204)
    
    # Возвращаем только новые сообщения для добавления в конец
    return request.app.state.templates.TemplateResponse(
        "partials/messages_trener.html", {"request": request, "messages": new_messages}
    )


@router.post("/chat_trener/send", response_class=HTMLResponse)
async def send_message(
    request: Request,
    db: Session = Depends(get_db),
    text: str = Form(""),
    files: List[UploadFile] = File(default=[])
):
    user = require_user(request, db)
    # Ищем конкретно диалог тренера
    conv = db.query(Conversation).filter(
        Conversation.user_id == user.id, 
        Conversation.title == "Чат Тренера"
    ).order_by(Conversation.id.desc()).first()
    if not conv:
        conv = Conversation(user_id=user.id, title="Чат Тренера"); db.add(conv); db.commit()

    text_clean = (text or "").strip()

    if not text_clean and not files:
        messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
        return request.app.state.templates.TemplateResponse("partials/messages_trener.html", {"request": request, "messages": messages})

    # Сообщение пользователя
    msg = Message(conversation_id=conv.id, user_id=user.id, role="user", text=text_clean)
    db.add(msg); db.flush()

    audio_att_ids: List[int] = []
    text_att_ids: List[int] = []
    image_att_ids: List[int] = []
    if files:
        unsupported: List[str] = []
        validation_errors: List[str] = []
        
        for f in files:
            if not f:
                continue
            
            ct = f.content_type or "application/octet-stream"
            filename = f.filename or "file"
            
            # Проверяем, что тип файла поддерживается
            if ct not in ALLOWED_MIME:
                unsupported.append(filename)
                continue
            
            # Читаем данные файла
            data = await f.read()
            
            # Валидация файла с использованием нового валидатора
            try:
                is_audio = ct.startswith("audio/")
                validate_uploaded_file(data, filename, ct, is_audio=is_audio)
            except (ValidationError, FileProcessingError) as e:
                ErrorHandler.log_error(e, {"user_id": user.id, "filename": filename})
                validation_errors.append(f"{filename}: {e.user_message}")
                continue
            
            ext = os.path.splitext(filename)[1] or ""
            safe_original = _secure_filename(filename)
            safe_name = f"{uuid.uuid4().hex}{ext}"
            user_dir = os.path.join(UPLOAD_DIR, str(user.id), str(conv.id))
            os.makedirs(user_dir, exist_ok=True)
            abs_path = os.path.join(user_dir, safe_name)
            
            if os.path.exists(abs_path):
                continue  # Пропустить, если файл уже существует
            
            with open(abs_path, "wb") as out:
                out.write(data)

            att = Attachment(
                message_id=msg.id,
                file_name=safe_original or safe_name,
                mime_type=ct,
                size_bytes=len(data),
                storage_key=os.path.relpath(abs_path, start=UPLOAD_DIR),
            )
            db.add(att); db.flush()
            
            if ct.startswith("audio/"):
                audio_att_ids.append(att.id)
            elif ct.startswith("image/"):
                image_att_ids.append(att.id)
            elif ct == "text/plain" or ct in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"):
                # Проверяем расширение для надежности
                if ext.lower() in ('.txt', '.docx', '.doc'):
                    text_att_ids.append(att.id)
        
        db.commit()
        
        # Сообщения о неподдерживаемых файлах
        if unsupported:
            warn = Message(
                conversation_id=conv.id,
                user_id=None,
                role="bot",
                text=f"Неподдерживаемый формат файлов: {', '.join(unsupported)}. "
                     f"Поддерживаемые форматы: {', '.join(FileValidator.get_supported_formats_list()[:10])}...",
            )
            db.add(warn); db.commit()
        
        # Сообщения об ошибках валидации
        if validation_errors:
            warn = Message(
                conversation_id=conv.id,
                user_id=None,
                role="bot",
                text="Ошибки валидации файлов:\n" + "\n".join(validation_errors),
            )
            db.add(warn); db.commit()

    # === Проверка лимита анализов ===
    total_analyses_requested = len(audio_att_ids) + len(text_att_ids)
    if image_att_ids:
        total_analyses_requested += 1  # Группа скриншотов = 1 анализ
    if not audio_att_ids and not text_att_ids and not image_att_ids and text_clean and _looks_like_transcript(text_clean):
        total_analyses_requested = 1

    if total_analyses_requested > 0 and not getattr(user, 'is_premium', False):
        remaining = user.free_analyses_limit - user.analyses_used
        if remaining <= 0:
            limit_msg = Message(
                conversation_id=conv.id, user_id=None, role="bot",
                text="⚠️ У вас закончились бесплатные анализы. Для продолжения работы необходимо приобрести подписку.\n\n"
                     "Обратитесь к менеджеру для получения доступа."
            )
            db.add(limit_msg); db.commit()
            messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
            return request.app.state.templates.TemplateResponse("partials/messages_trener.html", {"request": request, "messages": messages})
        if remaining < total_analyses_requested:
            limit_msg = Message(
                conversation_id=conv.id, user_id=None, role="bot",
                text=f"⚠️ У вас осталось {remaining} бесплатных анализ(ов), а вы отправили {total_analyses_requested} файл(ов). "
                     f"Пожалуйста, отправьте не более {remaining} файл(ов) за раз."
            )
            db.add(limit_msg); db.commit()
            messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
            return request.app.state.templates.TemplateResponse("partials/messages_trener.html", {"request": request, "messages": messages})
        user.analyses_used += total_analyses_requested
        db.commit()

    # Если есть аудио → статусы + запуск пайплайна
    if audio_att_ids:
        got = Message(
            conversation_id=conv.id,
            user_id=None,
            role="bot",
            text=f"Файл(ы) получил: {len(audio_att_ids)}. Запускаю обработку…",
        )
        db.add(got)
        db.commit()

        for att_id in audio_att_ids:
            asyncio.create_task(run_pipeline_trener(user.id, conv.id, att_id))
    
    # Если есть текстовые файлы → запуск пайплайна без транскрибации
    elif text_att_ids:
        got = Message(
            conversation_id=conv.id,
            user_id=None,
            role="bot",
            text=f"Файл(ы) с транскрибацией получил: {len(text_att_ids)}. Запускаю анализ…",
        )
        db.add(got)
        db.commit()

        for att_id in text_att_ids:
            asyncio.create_task(run_pipeline_from_text_trener(user.id, conv.id, att_id))

    # Если есть изображения → распознавание переписки + анализ тренера
    elif image_att_ids:
        got = Message(
            conversation_id=conv.id,
            user_id=None,
            role="bot",
            text=f"🖼️ Получено {len(image_att_ids)} скриншот(ов). Запускаю распознавание переписки…",
        )
        db.add(got)
        db.commit()

        asyncio.create_task(
            run_pipeline_from_images_trener(user.id, conv.id, image_att_ids)
        )

    # Если нет аудио, текстовых файлов и изображений — проверяем, не вставлен ли текст транскрибации
    if not audio_att_ids and not text_att_ids and not image_att_ids:
        if text_clean and _looks_like_transcript(text_clean):
            # Текст достаточно длинный — запускаем анализ как транскрибацию
            got = Message(
                conversation_id=conv.id,
                user_id=None,
                role="bot",
                text=f"Текст транскрибации получен ({len(text_clean)} символов). Запускаю анализ…",
            )
            db.add(got); db.commit()
            asyncio.create_task(run_pipeline_from_raw_text_trener(user.id, conv.id, text_clean))
        else:
            reply_text = scripted_reply(text_clean, len(files), user.name)
            if reply_text:
                bot_msg = Message(conversation_id=conv.id, user_id=None, role="bot", text=reply_text)
                db.add(bot_msg); db.commit()

    messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
    return request.app.state.templates.TemplateResponse("partials/messages_trener.html", {"request": request, "messages": messages})


@router.get("/attachments_trener/{attachment_id}")
def get_attachment(attachment_id: int, request: Request, db: Session = Depends(get_db)):
    att = db.get(Attachment, attachment_id)
    if not att:
        raise HTTPException(status_code=404)
    # доступ только к своим диалогам
    conv = db.query(Conversation).join(Message).filter(Message.id == att.message_id).first()
    user = require_user(request, db)
    if not conv or conv.user_id != user.id:
        raise HTTPException(status_code=403)
    abs_path = os.path.join(UPLOAD_DIR, att.storage_key)
    return FileResponse(abs_path, media_type=att.mime_type, filename=att.file_name)

@router.get("/chat_trener/export/by-report/{report_message_id}")
def export_zip_by_report(report_message_id: int, request: Request, db: Session = Depends(get_db)):
    """
    ZIP по конкретному пакету анализа:
    - ищем сообщение бота с analysis_*.txt (report_message_id)
    - добавляем transcript_*.txt/json и dialogue_*.json из предыдущего бот-сообщения
    """
    user = require_user(request, db)
    msg = db.get(Message, report_message_id)
    if not msg or msg.role != "bot":
        raise HTTPException(status_code=404, detail="Пакет не найден")

    # доступ только к своим
    conv = db.query(Conversation).filter(Conversation.id == msg.conversation_id).first()
    if not conv or conv.user_id != user.id:
        raise HTTPException(status_code=403)

    report_atts = [a for a in msg.attachments if a.file_name.startswith("analysis_")]
    if not report_atts:
        raise HTTPException(status_code=404, detail="В этом сообщении нет отчёта")

    prev_messages = (
        db.query(Message)
        .filter(
            Message.conversation_id == msg.conversation_id,
            Message.role == "bot",
            Message.created_at < msg.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(5)
        .all()
    )

    wanted = list(report_atts)
    for prev in prev_messages:
        for a in prev.attachments:
            if a.file_name.startswith(("transcript_", "dialogue_")):
                wanted.append(a)
        if len(wanted) > len(report_atts):
            break

    if not wanted:
        raise HTTPException(status_code=404, detail="Нет вложений для экспорта")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for a in wanted:
            abs_path = os.path.join(UPLOAD_DIR, a.storage_key)
            if os.path.exists(abs_path):
                z.write(abs_path, arcname=a.file_name)

    buf.seek(0)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"call_package_{ts}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )


@router.post("/chat_trener/reset", response_class=HTMLResponse)
def reset_chat(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    # Ищем конкретно диалог тренера
    conv = db.query(Conversation).filter(
        Conversation.user_id == user.id, 
        Conversation.title == "Чат Тренера"
    ).order_by(Conversation.id.desc()).first()
    if conv:
        atts = db.query(Attachment).filter(
            Attachment.message_id.in_(db.query(Message.id).filter(Message.conversation_id == conv.id))
        ).all()
        for a in atts:
            abs_path = os.path.join(UPLOAD_DIR, a.storage_key)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except Exception:
                pass
        db.query(Attachment).filter(Attachment.id.in_([a.id for a in atts])).delete(synchronize_session=False)
        db.query(Message).filter(Message.conversation_id == conv.id).delete(synchronize_session=False)
        db.commit()

    messages: List[Message] = []
    return request.app.state.templates.TemplateResponse(
        "partials/messages_trener.html", {"request": request, "messages": messages}
    )
