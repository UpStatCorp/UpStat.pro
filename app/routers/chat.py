import os
import uuid
import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Conversation, Message, Attachment
from deps import require_user
from services.pipeline import run_pipeline, run_pipeline_from_text, run_pipeline_from_raw_text
from services.image_pipeline import run_pipeline_from_images
from services.error_handler import ErrorHandler, ValidationError, FileProcessingError
from utils.file_validator import FileValidator, validate_uploaded_file

import io, zipfile
from datetime import datetime
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)


router = APIRouter(tags=["chat"])
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


@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    
    # Получаем список участников команды для менеджера
    team_members = []
    is_manager = user.role in ("manager", "admin")
    if is_manager:
        from services.team_access import get_manager_teams
        from models import TeamMember, User as UserModel
        from sqlalchemy.orm import joinedload
        
        # Получаем все команды менеджера
        teams = get_manager_teams(db, user)
        team_ids = [t.id for t in teams]
        
        if team_ids:
            # Получаем всех участников этих команд
            from models import Team
            members = (
                db.query(TeamMember)
                .options(joinedload(TeamMember.user), joinedload(TeamMember.team))
                .filter(TeamMember.team_id.in_(team_ids))
                .all()
            )
            # Собираем уникальных пользователей
            seen_user_ids = set()
            for member in members:
                if member.user_id not in seen_user_ids and member.user_id != user.id:
                    team_members.append({
                        "id": member.user.id,
                        "name": member.user.name,
                        "email": member.user.email,
                        "team_name": member.team.name
                    })
                    seen_user_ids.add(member.user_id)
    
    # Ищем конкретно обычный диалог
    conv = db.query(Conversation).filter(
        Conversation.user_id == user.id, 
        Conversation.title == "Мой диалог"
    ).order_by(Conversation.id.desc()).first()
    if not conv:
        conv = Conversation(user_id=user.id, title="Мой диалог")
        db.add(conv); db.commit()

    has_msgs = db.query(Message).filter(Message.conversation_id == conv.id).count() > 0
    if not has_msgs:
        hello = Message(conversation_id=conv.id, user_id=None, role="bot",
                        text=f"Привет, {user.name}! Я — ИИ-аналитик. Вот что я умею:\n\n"
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
        "chat.html", {
            "request": request, 
            "user": user, 
            "conversation": conv, 
            "messages": messages,
            "team_members": team_members,
            "is_manager": is_manager
        }
    )


@router.get("/chat/poll", response_class=HTMLResponse)
def chat_poll(request: Request, last_id: int = 0, db: Session = Depends(get_db)):
    """HTMX-пуллинг: если новых сообщений нет — 204, иначе вернём partial."""
    user = require_user(request, db)
    # Ищем конкретно обычный диалог
    conv = db.query(Conversation).filter(
        Conversation.user_id == user.id, 
        Conversation.title == "Мой диалог"
    ).order_by(Conversation.id.desc()).first()
    if not conv:
        raise HTTPException(404)
    latest = db.query(Message.id).filter(Message.conversation_id == conv.id).order_by(Message.id.desc()).first()
    if not latest or latest[0] <= last_id:
        return Response(status_code=204)
    # Возвращаем только новые сообщения после last_id
    new_messages = db.query(Message).filter(
        Message.conversation_id == conv.id,
        Message.id > last_id
    ).order_by(Message.created_at.asc()).all()
    
    if not new_messages:
        return Response(status_code=204)
    
    # Возвращаем только новые сообщения для добавления в конец
    return request.app.state.templates.TemplateResponse(
        "partials/messages.html", {"request": request, "messages": new_messages}
    )


@router.post("/chat/send", response_class=HTMLResponse)
async def send_message(
    request: Request,
    db: Session = Depends(get_db),
    text: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    target_user_id: Optional[str] = Form(None)
):
    # Логирование для отладки
    logger.info(f"📥 Получен запрос на отправку сообщения. text={text[:50] if text else ''}, files_count={len(files) if files else 0}, target_user_id={target_user_id}")
    if files:
        for i, f in enumerate(files):
            logger.info(f"  Файл {i+1}: filename={f.filename}, content_type={f.content_type}, size={f.size if hasattr(f, 'size') else 'unknown'}")
    
    try:
        user = require_user(request, db)
        
        # Определяем, для какого пользователя создавать диалог
        target_user = user
        try:
            if target_user_id and user.role in ("manager", "admin"):
                # Менеджер может загружать для участников своей команды
                from services.team_access import get_accessible_user_ids_for_manager
                from models import User as UserModel
                
                # Обрабатываем target_user_id (может быть строкой или None)
                target_user_id_str = str(target_user_id).strip() if target_user_id else ""
                
                if target_user_id_str:
                    try:
                        target_user_id_int = int(target_user_id_str)
                    except (ValueError, TypeError):
                        logger.warning(f"Неверный формат target_user_id: {target_user_id}")
                        target_user_id_int = None
                    
                    if target_user_id_int:
                        accessible_user_ids = get_accessible_user_ids_for_manager(db, user)
                        if accessible_user_ids is None or target_user_id_int in accessible_user_ids:
                            target_user = db.get(UserModel, target_user_id_int)
                            if not target_user:
                                logger.error(f"Пользователь с ID {target_user_id_int} не найден")
                                raise HTTPException(status_code=404, detail="Пользователь не найден")
                            logger.info(f"Менеджер {user.id} загружает файл для участника {target_user.id}")
                        else:
                            logger.warning(f"Менеджер {user.id} пытается загрузить для недоступного пользователя {target_user_id_int}")
                            raise HTTPException(status_code=403, detail="Нет доступа к этому пользователю")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Ошибка при определении целевого пользователя: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Ошибка обработки запроса: {str(e)}")
        
        # Если менеджер загружает для участника, нужны два диалога:
        # 1. Диалог менеджера - для отображения прогресса
        # 2. Диалог участника - для сохранения результатов
        manager_conv = None
        if target_user.id != user.id:
            # Получаем диалог менеджера для отображения прогресса
            manager_conv = db.query(Conversation).filter(
                Conversation.user_id == user.id, 
                Conversation.title == "Мой диалог"
            ).order_by(Conversation.id.desc()).first()
            if not manager_conv:
                manager_conv = Conversation(user_id=user.id, title="Мой диалог")
                db.add(manager_conv)
                db.commit()
                db.refresh(manager_conv)
        
        # Ищем конкретно обычный диалог для целевого пользователя (где будут результаты)
        try:
            conv = db.query(Conversation).filter(
                Conversation.user_id == target_user.id, 
                Conversation.title == "Мой диалог"
            ).order_by(Conversation.id.desc()).first()
            if not conv:
                conv = Conversation(user_id=target_user.id, title="Мой диалог")
                db.add(conv)
                db.commit()
                db.refresh(conv)
        except Exception as e:
            logger.error(f"Ошибка при создании/получении диалога: {e}", exc_info=True)
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Ошибка создания диалога: {str(e)}")

        text_clean = (text or "").strip()

        if not text_clean and not files:
            messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
            return request.app.state.templates.TemplateResponse("partials/messages.html", {"request": request, "messages": messages})

        # Если менеджер загружает для участника, создаем сообщение в диалоге менеджера для отображения
        if manager_conv:
            manager_msg = Message(conversation_id=manager_conv.id, user_id=user.id, role="user", text=text_clean)
            db.add(manager_msg); db.flush()
        
        # Сообщение в диалоге участника (где будут результаты)
        msg = Message(conversation_id=conv.id, user_id=user.id, role="user", text=text_clean)
        db.add(msg); db.flush()

        audio_att_ids: List[int] = []
        text_att_ids: List[int] = []
        image_att_ids: List[int] = []
        if files:
            logger.info(f"🔍 Обработка {len(files)} файлов")
            unsupported: List[str] = []
            validation_errors: List[str] = []
            
            for f in files:
                if not f:
                    logger.warning("⚠️ Пропущен пустой файл")
                    continue
                
                ct = f.content_type or "application/octet-stream"
                filename = f.filename or "file"
                logger.info(f"📄 Обработка файла: {filename}, MIME: {ct}")
                
                # Проверяем, что тип файла поддерживается
                if ct not in ALLOWED_MIME:
                    logger.warning(f"❌ Неподдерживаемый MIME тип: {ct} для файла {filename}")
                    unsupported.append(filename)
                    continue
                logger.info(f"✅ MIME тип {ct} поддерживается")
                
                # Читаем данные файла
                data = await f.read()
                logger.info(f"📦 Прочитано {len(data)} байт из файла {filename}")
                
                # Валидация файла с использованием нового валидатора
                try:
                    is_audio = ct.startswith("audio/")
                    logger.info(f"🔍 Валидация файла {filename}, is_audio={is_audio}")
                    validate_uploaded_file(data, filename, ct, is_audio=is_audio)
                    logger.info(f"✅ Файл {filename} прошел валидацию")
                except (ValidationError, FileProcessingError) as e:
                    logger.error(f"❌ Ошибка валидации файла {filename}: {e.user_message}")
                    ErrorHandler.log_error(e, {"user_id": target_user.id, "filename": filename})
                    validation_errors.append(f"{filename}: {e.user_message}")
                    continue
                
                ext = os.path.splitext(filename)[1] or ""
                safe_original = _secure_filename(filename)
                safe_name = f"{uuid.uuid4().hex}{ext}"
                user_dir = os.path.join(UPLOAD_DIR, str(target_user.id), str(conv.id))
                os.makedirs(user_dir, exist_ok=True)
                abs_path = os.path.join(user_dir, safe_name)
                
                if os.path.exists(abs_path):
                    continue  # Пропустить, если файл уже существует
                
                with open(abs_path, "wb") as out:
                    out.write(data)

                # Создаем attachment в диалоге участника (где будут результаты)
                att = Attachment(
                    message_id=msg.id,
                    file_name=safe_original or safe_name,
                    mime_type=ct,
                    size_bytes=len(data),
                    storage_key=os.path.relpath(abs_path, start=UPLOAD_DIR),
                )
                db.add(att); db.flush()
                
                # Если менеджер загружает для участника, создаем копию attachment в диалоге менеджера для отображения
                if manager_conv:
                    manager_att = Attachment(
                        message_id=manager_msg.id,
                        file_name=safe_original or safe_name,
                        mime_type=ct,
                        size_bytes=len(data),
                        storage_key=os.path.relpath(abs_path, start=UPLOAD_DIR),  # Тот же файл
                    )
                    db.add(manager_att); db.flush()
                
                if ct.startswith("audio/"):
                    audio_att_ids.append(att.id)
                    logger.info(f"🎵 Добавлен аудио файл в список обработки: attachment_id={att.id}")
                elif ct.startswith("image/"):
                    image_att_ids.append(att.id)
                    logger.info(f"🖼️ Добавлено изображение в список обработки: attachment_id={att.id}")
                elif ct == "text/plain" or ct in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"):
                    # Проверяем расширение для надежности
                    if ext.lower() in ('.txt', '.docx', '.doc'):
                        text_att_ids.append(att.id)
                        logger.info(f"📝 Добавлен текстовый файл в список обработки: attachment_id={att.id}")
        
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
        # Если пользователь вставил текст транскрибации (будет обработано ниже), считаем +1
        if not audio_att_ids and not text_att_ids and not image_att_ids and text_clean and _looks_like_transcript(text_clean):
            total_analyses_requested = 1

        if total_analyses_requested > 0 and not getattr(target_user, 'is_premium', False):
            remaining = target_user.free_analyses_limit - target_user.analyses_used
            if remaining <= 0:
                limit_msg = Message(
                    conversation_id=conv.id,
                    user_id=None,
                    role="bot",
                    text="⚠️ У вас закончились бесплатные анализы. Для продолжения работы необходимо приобрести подписку.\n\n"
                         "Обратитесь к менеджеру для получения доступа."
                )
                db.add(limit_msg); db.commit()
                messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
                return request.app.state.templates.TemplateResponse("partials/messages.html", {"request": request, "messages": messages})
            if remaining < total_analyses_requested:
                limit_msg = Message(
                    conversation_id=conv.id,
                    user_id=None,
                    role="bot",
                    text=f"⚠️ У вас осталось {remaining} бесплатных анализ(ов), а вы отправили {total_analyses_requested} файл(ов). "
                         f"Пожалуйста, отправьте не более {remaining} файл(ов) за раз."
                )
                db.add(limit_msg); db.commit()
                messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
                return request.app.state.templates.TemplateResponse("partials/messages.html", {"request": request, "messages": messages})
            # Инкрементируем счётчик
            target_user.analyses_used += total_analyses_requested
            db.commit()

        # Если есть аудио → статусы + запуск пайплайна
        logger.info(f"🎯 Итоговые списки: audio_att_ids={audio_att_ids}, text_att_ids={text_att_ids}, image_att_ids={image_att_ids}")
        if audio_att_ids:
            # Если менеджер загружает для участника, показываем прогресс в его чате
            if manager_conv:
                got_manager = Message(
                    conversation_id=manager_conv.id,
                    user_id=None,
                    role="bot",
                    text=f"Файл(ы) получил: {len(audio_att_ids)}. Запускаю обработку для участника {target_user.name}…",
                )
                db.add(got_manager)
                db.commit()
            
            # Сообщение в диалоге участника
            upload_info = ""
            if target_user.id != user.id:
                upload_info = f" (загружено менеджером {user.name})"
            
            got = Message(
                conversation_id=conv.id,
                user_id=None,
                role="bot",
                text=f"Файл(ы) получил: {len(audio_att_ids)}. Запускаю обработку…{upload_info}",
            )
            db.add(got)
            db.commit()

            # Запускаем pipeline с progress_conversation_id для отображения прогресса в чате менеджера
            progress_conv_id = manager_conv.id if manager_conv else None
            for att_id in audio_att_ids:
                asyncio.create_task(run_pipeline(target_user.id, conv.id, att_id, progress_conversation_id=progress_conv_id))
        
        # Если есть текстовые файлы → запуск пайплайна без транскрибации
        elif text_att_ids:
            # Если менеджер загружает для участника, показываем прогресс в его чате
            if manager_conv:
                got_manager = Message(
                    conversation_id=manager_conv.id,
                    user_id=None,
                    role="bot",
                    text=f"Файл(ы) с транскрибацией получил: {len(text_att_ids)}. Запускаю анализ для участника {target_user.name}…",
                )
                db.add(got_manager)
                db.commit()
            
            # Сообщение в диалоге участника
            upload_info = ""
            if target_user.id != user.id:
                upload_info = f" (загружено менеджером {user.name})"
            
            got = Message(
                conversation_id=conv.id,
                user_id=None,
                role="bot",
                text=f"Файл(ы) с транскрибацией получил: {len(text_att_ids)}. Запускаю анализ…{upload_info}",
            )
            db.add(got)
            db.commit()

            # Запускаем pipeline с progress_conversation_id для отображения прогресса в чате менеджера
            progress_conv_id = manager_conv.id if manager_conv else None
            for att_id in text_att_ids:
                asyncio.create_task(run_pipeline_from_text(target_user.id, conv.id, att_id, progress_conversation_id=progress_conv_id))

        # Если есть изображения → распознавание переписки + анализ
        elif image_att_ids:
            if manager_conv:
                got_manager = Message(
                    conversation_id=manager_conv.id,
                    user_id=None,
                    role="bot",
                    text=f"🖼️ Получено {len(image_att_ids)} скриншот(ов). Запускаю распознавание переписки для участника {target_user.name}…",
                )
                db.add(got_manager)
                db.commit()
            
            upload_info = ""
            if target_user.id != user.id:
                upload_info = f" (загружено менеджером {user.name})"
            
            got = Message(
                conversation_id=conv.id,
                user_id=None,
                role="bot",
                text=f"🖼️ Получено {len(image_att_ids)} скриншот(ов). Запускаю распознавание переписки…{upload_info}",
            )
            db.add(got)
            db.commit()

            progress_conv_id = manager_conv.id if manager_conv else None
            asyncio.create_task(
                run_pipeline_from_images(
                    target_user.id, conv.id, image_att_ids,
                    progress_conversation_id=progress_conv_id
                )
            )

        # Если нет аудио, текстовых файлов и изображений — проверяем, не вставлен ли текст транскрибации
        if not audio_att_ids and not text_att_ids and not image_att_ids:
            if text_clean and _looks_like_transcript(text_clean):
                # Текст достаточно длинный — запускаем анализ как транскрибацию
                logger.info(f"📝 Обнаружен вставленный текст транскрибации ({len(text_clean)} символов), запускаю анализ")
                
                if manager_conv:
                    got_manager = Message(
                        conversation_id=manager_conv.id,
                        user_id=None,
                        role="bot",
                        text=f"Текст транскрибации получен ({len(text_clean)} символов). Запускаю анализ для участника {target_user.name}…",
                    )
                    db.add(got_manager); db.commit()
                
                upload_info = ""
                if target_user.id != user.id:
                    upload_info = f" (отправлено менеджером {user.name})"
                
                got = Message(
                    conversation_id=conv.id,
                    user_id=None,
                    role="bot",
                    text=f"Текст транскрибации получен ({len(text_clean)} символов). Запускаю анализ…{upload_info}",
                )
                db.add(got); db.commit()
                
                progress_conv_id = manager_conv.id if manager_conv else None
                asyncio.create_task(run_pipeline_from_raw_text(target_user.id, conv.id, text_clean, progress_conversation_id=progress_conv_id))
            else:
                reply_text = scripted_reply(text_clean, len(files), target_user.name)
                if reply_text:
                    bot_msg = Message(conversation_id=conv.id, user_id=None, role="bot", text=reply_text)
                    db.add(bot_msg); db.commit()

        # Если менеджер загружает для участника, возвращаем сообщения из его чата (для отображения прогресса)
        if manager_conv:
            messages = db.query(Message).filter(Message.conversation_id == manager_conv.id).order_by(Message.created_at.asc()).all()
        else:
            messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
        return request.app.state.templates.TemplateResponse("partials/messages.html", {"request": request, "messages": messages})
    except Exception as e:
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА в send_message: {e}", exc_info=True)
        try:
            db.rollback()
        except:
            pass
        # Возвращаем сообщение об ошибке пользователю
        try:
            if 'conv' in locals() and conv:
                error_msg = Message(
                    conversation_id=conv.id,
                    user_id=None,
                    role="bot",
                    text=f"❌ Произошла ошибка при обработке сообщения: {str(e)}. Пожалуйста, попробуйте еще раз."
                )
                db.add(error_msg)
                db.commit()
                messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
                return request.app.state.templates.TemplateResponse("partials/messages.html", {"request": request, "messages": messages})
        except:
            pass
        # Если даже диалог не создан, возвращаем пустой список
        return request.app.state.templates.TemplateResponse("partials/messages.html", {"request": request, "messages": []})


@router.get("/attachments/{attachment_id}")
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

@router.get("/chat/export/by-report/{report_message_id}")
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


@router.post("/chat/reset", response_class=HTMLResponse)
def reset_chat(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    # Ищем конкретно обычный диалог
    conv = db.query(Conversation).filter(
        Conversation.user_id == user.id, 
        Conversation.title == "Мой диалог"
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
        "partials/messages.html", {"request": request, "messages": messages}
    )
