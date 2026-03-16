import os
import re
import json
import uuid
import asyncio
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

import httpx
from httpx import HTTPStatusError, RequestError, TimeoutException
from openai import OpenAI
from sqlalchemy.orm import Session

from models import Message, Attachment
from database import SessionLocal
from services.prompt_service import PromptService
from services.error_handler import (
    ErrorHandler, 
    FileProcessingError, 
    ExternalAPIError, 
    DatabaseError,
    handle_errors,
    retry_on_error,
    ErrorCategory
)
from services.progress_tracker import get_progress_tracker, ProgressStage
from services.notification_service import get_notification_service, NotificationType, NotificationPriority
from dotenv import load_dotenv

logger = logging.getLogger("main")

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
UPLOAD_DIR = os.path.abspath("uploads")

client = OpenAI(api_key=OPENAI_API_KEY)


def _ffmpeg_wav(src: Path, dst: Path, rate: int = 16000):
    cmd = ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", str(rate), str(dst)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def _elevenlabs_transcribe(wav_path: Path) -> Dict[str, Any]:
    url = "https://api.elevenlabs.io/v1/speech-to-text"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    data = {
        "model_id": "scribe_v1",
        "diarize": True,
        "num_speakers": 2,
        "timestamps_granularity": "word",
        "tag_audio_events": True,
    }
    timeout = httpx.Timeout(300.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            with open(wav_path, "rb") as f:
                files = {"file": f}
                resp = await client.post(url, headers=headers, data=data, files=files)
        if resp.status_code in (401, 403):
            raise HTTPStatusError(
                f"Unauthorized ({resp.status_code})", request=resp.request, response=resp
            )
        resp.raise_for_status()
        return resp.json()
    except TimeoutException:
        raise
    except RequestError:
        raise


def _openai_whisper_transcribe(audio_path: Path):
    """Фолбэк — простая транскрибация без диаризации (Whisper)."""
    with open(audio_path, "rb") as f:
        tr = client.audio.transcriptions.create(model="whisper-1", file=f)
    return {"text": tr.text or "", "words": []}


def _words_to_turns(words: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Преобразует words из ElevenLabs (каждое слово: speaker_id/start/end/text)
    в строгий JSON-диалог: 2+ спикера, role_map unknown, turns с таймкодами.
    """
    uniq = []
    for w in words:
        sid = w.get("speaker_id")
        if sid not in uniq:
            uniq.append(sid)

    mapped: Dict[str, str] = {}
    for i, sid in enumerate(uniq[:2]):
        mapped[sid] = f"speaker_{i+1}"
    for sid in uniq[2:]:
        mapped[sid] = "speaker_2"  # лишних маппим во второго

    turns = []
    cur_spk = None
    cur_tokens: List[str] = []
    cur_start = None
    cur_end = None

    def push_turn():
        nonlocal turns, cur_tokens, cur_start, cur_end, cur_spk
        if not cur_tokens:
            return
        text = " ".join(cur_tokens)
        text = re.sub(r"\s+([,\.!?;:])", r"\1", text).strip()
        turns.append({
            "speaker": cur_spk,
            "start": round(float(cur_start or 0), 2),
            "end": round(float(cur_end or 0), 2),
            "text": text
        })
        cur_tokens, cur_start, cur_end = [], None, None

    for w in words:
        sid = w.get("speaker_id")
        spk = mapped.get(sid, "speaker_1")
        token = (w.get("text") or "").strip()
        ws = w.get("start")
        we = w.get("end")
        if spk != cur_spk:
            push_turn()
            cur_spk = spk
        if token:
            cur_tokens.append(token)
            cur_start = ws if cur_start is None else cur_start
            cur_end = we
    push_turn()

    stats: Dict[str, float] = {}
    for lbl in set(mapped.values()):
        stats[lbl] = 0.0
    for tr in turns:
        stats[tr["speaker"]] = round(stats.get(tr["speaker"], 0.0) + (tr["end"] - tr["start"]), 2)

    # роли заранее не задаём — пусть модель решает
    role_map = {"manager": "unknown", "client": "unknown"}

    return {
        "speakers": [{"id": k, "label": v} for k, v in mapped.items()],
        "role_map": role_map,
        "stats_sec": stats,
        "turns": turns
    }


def _text_to_single_speaker_turns(text: str) -> Dict[str, Any]:
    """
    Фолбэк, если нет words/диаризации. Добавляем обоих спикеров, но реплики у speaker_1.
    Модель сама определит роли позже.
    """
    text = (text or "").strip()
    if not text:
        return {
            "speakers": [{"id": "speaker_1", "label": "speaker_1"},
                         {"id": "speaker_2", "label": "speaker_2"}],
            "role_map": {"manager": "unknown", "client": "unknown"},
            "stats_sec": {"speaker_1": 0.0, "speaker_2": 0.0},
            "turns": []
        }
    sentences = [s.strip() for s in re.split(r'(?<=[\.\!\?])\s+', text) if s.strip()]
    turns = [{"speaker": "speaker_1", "start": 0.0, "end": 0.0, "text": s} for s in sentences]
    return {
        "speakers": [{"id": "speaker_1", "label": "speaker_1"},
                     {"id": "speaker_2", "label": "speaker_2"}],
        "role_map": {"manager": "unknown", "client": "unknown"},
        "stats_sec": {"speaker_1": 0.0, "speaker_2": 0.0},
        "turns": turns
    }


def _attach_file(db: Session, message_id: int, original_name: str, mime: str, abs_rel_key: str, size: int):
    att = Attachment(
        message_id=message_id,
        file_name=original_name,
        mime_type=mime,
        size_bytes=size,
        storage_key=abs_rel_key
    )
    db.add(att)
    db.flush()
    return att


async def _analyze_checklist(data: Dict[str, Any], title: str, dialogue_json_str: str, analyses: List[str]):
    """
    Анализирует чеклист или скрипт команды по диалогу.
    
    Args:
        data: Данные чеклиста/скрипта в формате JSON
        title: Название чеклиста/скрипта
        dialogue_json_str: JSON строка диалога
        analyses: Список для добавления результатов анализа
    """
    logger.info(f"🔍 Начинаю анализ чеклиста/скрипта: {title}")
    # Используем статичный промпт для анализа чек-листов
    prompt = (
        "Ты — аудитор качества продаж. У тебя есть СТРОГО JSON-диалог двух спикеров с таймкодами.\n"
        "Формат JSON: { speakers:[{id,label}], role_map:{manager,client}, turns:[{speaker,start,end,text}] }.\n"
        "role_map сейчас unknown — сперва определи роли.\n\n"
        "ШАГ 0 (обязателен): Определи роли manager/client.\n"
        "- Проанализируй реплики и поведение: кто представляется, квалифицирует, презентует продукт,\n"
        "  обрабатывает возражения, называет цену/условия, делает call-to-action — обычно это менеджер.\n"
        "- Зафиксируй соответствие: speaker_1 → manager|client, speaker_2 → manager|client.\n"
        "- Приведи 2–4 короткие цитаты в кавычках «...» с таймкодами [t=мм:сс–мм:сс], подтверждающие выбор.\n"
        "- Если неоднозначно — выбери более вероятный вариант и объясни кратко.\n\n"
        "ШАГ 1: Проверь чек-лист ТОЛЬКО по данным диалога (без домыслов).\n"
        "Для каждого пункта чек-листа укажи:\n"
        "1) Статус: Да / Нет / Частично.\n"
        "2) Короткий комментарий по репликам.\n"
        "3) Если «Нет» или «Частично» — приведи 1–3 ТОЧНЫЕ ЦИТАТЫ МЕНЕДЖЕРА «...» с таймкодами [t=мм:сс–мм:сс] из turns.\n"
        "4) Если данных нет — «Не обнаружено в диалоге».\n\n"
        "СТРОГО не выдумывай фразы и факты — цитируй только то, что есть в JSON.\n\n"
        f"ЧЕК-ЛИСТ:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
        f"ДИАЛОГ_JSON:\n{dialogue_json_str}\n"
        "Формат ответа:\n"
        "=== ROLE MAPPING ===\n"
        "- speaker_1: manager|client — доказательства: «…» [t=мм:сс–мм:сс]; «…» [t=мм:сс–мм:сс]\n"
        "- speaker_2: manager|client — доказательства: «…» [t=мм:сс–мм:сс]\n"
        "=== {НАЗВАНИЕ ЧЕК-ЛИСТА} ===\n"
        "- [Пункт 1]: Да/Нет/Частично — комментарий. Цитаты (если есть): «…» [t=00:12–00:18]\n"
        "- [Пункт 2]: ...\n"
    )

    try:
        logger.info(f"📤 Отправляю запрос к GPT для анализа: {title}")
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
        )
        logger.info(f"✅ Получен ответ от GPT для: {title}")
        analyses.append(f"=== {title.upper()} ===\n{resp.choices[0].message.content.strip()}\n")
    except Exception as e:
        logger.error(f"❌ Ошибка анализа чеклиста {title}: {e}", exc_info=True)
        analyses.append(f"=== {title.upper()} ===\nОшибка LLM: {e}\n")


def _read_text_file(file_path: Path) -> str:
    """Читает текст из txt или docx файла."""
    ext = file_path.suffix.lower()
    if ext == '.txt':
        return file_path.read_text(encoding='utf-8')
    elif ext in ('.docx', '.doc'):
        try:
            from docx import Document
            doc = Document(file_path)
            return '\n'.join([para.text for para in doc.paragraphs])
        except ImportError:
            raise Exception("Для обработки Word файлов установите python-docx: pip install python-docx")
        except Exception as e:
            raise Exception(f"Ошибка чтения Word файла: {e}")
    else:
        raise Exception(f"Неподдерживаемый формат файла: {ext}")


async def run_pipeline_from_text(user_id: int, conversation_id: int, text_attachment_id: int, progress_conversation_id: Optional[int] = None):
    """
    Пайплайн для текстовых файлов: чтение текста → JSON-диалог → анализ.
    Пропускает транскрибацию.
    
    Args:
        user_id: ID пользователя, для которого создается анализ
        conversation_id: ID диалога, куда сохраняются результаты
        text_attachment_id: ID текстового файла
        progress_conversation_id: Опциональный ID диалога для отображения прогресса (если отличается от conversation_id)
    """
    db = SessionLocal()
    tracker = get_progress_tracker()
    
    # Используем progress_conversation_id для отображения прогресса, если указан
    display_conv_id = progress_conversation_id if progress_conversation_id else conversation_id
    
    # Создаем операцию прогресса
    operation_id = f"text_analysis_{conversation_id}_{text_attachment_id}"
    progress = tracker.create_operation(
        operation_id=operation_id,
        total_stages=2,
        title="Анализ текстового файла",
        can_cancel=False
    )
    progress.metadata = {"user_id": user_id, "conversation_id": conversation_id}
    
    try:
        text_att = db.get(Attachment, text_attachment_id)
        if not text_att:
            tracker.fail_operation(operation_id, "Текстовый файл не найден")
            return
        
        # Шаг 1: чтение текста из файла
        msg_read = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                          text="Читаю файл с транскрибацией…")
        db.add(msg_read); db.commit()

        src_abs = Path(UPLOAD_DIR) / text_att.storage_key
        temp_dir = Path(UPLOAD_DIR) / str(user_id) / str(conversation_id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            text = _read_text_file(src_abs)
        except Exception as e:
            tracker.fail_operation(operation_id, f"Не удалось прочитать файл: {e}")
            err = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                          text=f"Не удалось прочитать файл: {e}")
            db.add(err); db.commit(); return

        if not text.strip():
            tracker.fail_operation(operation_id, "Файл пуст или не содержит текста.")
            err = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                          text="Файл пуст или не содержит текста.")
            db.add(err); db.commit(); return

        # Преобразуем текст в JSON-диалог (без таймкодов, так как их нет)
        dialogue = _text_to_single_speaker_turns(text)

        # Сохраняем материалы
        tr_txt = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        dialogue_path = temp_dir / f"dialogue_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"

        tr_txt.write_text(text, encoding="utf-8")
        dialogue_path.write_text(json.dumps(dialogue, ensure_ascii=False, indent=2), encoding="utf-8")

        # Сообщение о готовности - в диалог для отображения прогресса
        msg_tr_done = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                              text="Готово: файл прочитан. Приложил файлы.")
        db.add(msg_tr_done); db.flush()

        key_txt = os.path.relpath(tr_txt, start=UPLOAD_DIR)
        key_dialogue = os.path.relpath(dialogue_path, start=UPLOAD_DIR)
        _attach_file(db, msg_tr_done.id, tr_txt.name, "text/plain", key_txt, tr_txt.stat().st_size)
        _attach_file(db, msg_tr_done.id, dialogue_path.name, "application/json", key_dialogue, dialogue_path.stat().st_size)
        db.commit()

        # Шаг 2: анализ (такой же как в run_pipeline)
        # Сообщение о начале анализа - в диалог для отображения прогресса
        msg_an = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                         text="Делаю анализ по чек-листам…")
        db.add(msg_an); db.commit()

        check_dir = Path("checklists")
        check_files = list(check_dir.glob("*.json"))
        analyses: List[str] = []
        dialogue_json_str = dialogue_path.read_text(encoding="utf-8")

        # Получаем скрипт команды для пользователя, если он есть
        team_script = None
        try:
            from services.team_access import get_team_script_for_user
            from models import User as UserModel
            target_user_obj = db.get(UserModel, user_id)
            if target_user_obj:
                team_script = get_team_script_for_user(db, target_user_obj, user_id)
        except Exception as e:
            logger.warning(f"Ошибка получения скрипта команды: {e}")

        # Обрабатываем стандартные чеклисты
        for cf in check_files:
            data = json.loads(cf.read_text(encoding="utf-8"))
            script_title = cf.stem.upper()
            
            # Анализируем чеклист
            await _analyze_checklist(data, script_title, dialogue_json_str, analyses)
        
        # Обрабатываем скрипт команды, если он есть
        if team_script:
            script_title = team_script.get("title", "Скрипт команды")
            await _analyze_checklist(team_script, script_title, dialogue_json_str, analyses)

        combined = "\n".join(analyses)

        # Получаем финальный промпт из базы данных
        prompt_service = PromptService(db)
        final_prompt_template = prompt_service.get_active_prompt("sales_audit_summary")
        
        if final_prompt_template:
            # Используем промпт из базы данных
            final_prompt = final_prompt_template.content.format(
                analyses=combined,
                dialogue_json_str=dialogue_json_str
            )
            logger.info(f"🔍 ИСПОЛЬЗУЮ ФИНАЛЬНЫЙ ПРОМПТ ИЗ БД (версия {final_prompt_template.version}): {final_prompt[:100]}...")
        else:
            # Fallback на старый промпт, если в БД нет активного
            logger.warning("⚠️ FALLBACK: Использую старый финальный промпт из кода")
            final_prompt = (
                "Суммируй результаты аудита, опираясь на найденные роли manager/client и процитированные фразы менеджера.\n"
                "Сформируй отчёт для РОПа:\n"
                "1) Сильные стороны (3–6) — по делу.\n"
                "2) Зоны роста (3–6) — с конкретными рекомендациями к поведению и формулировкам.\n"
                "3) Примеры фраз МЕНЕДЖЕРА (5–10) из диалога: «фраза» [t=мм:сс–мм:сс].\n"
                "4) Чек-лист исправлений на неделю (5–8 пунктов, измеримо).\n"
                "Только факты из анализов и JSON-диалога — без домыслов.\n\n"
                f"Анализы по чек-листам (с role mapping вверху):\n{combined}\n\n"
                f"ДИАЛОГ_JSON (для ссылок на таймкоды):\n{dialogue_json_str}\n"
            )
        final_resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": final_prompt}],
                temperature=0.2,
            )
        )
        summary = "=== ИТОГОВЫЙ ОТЧЁТ ===\n" + final_resp.choices[0].message.content.strip()

        report_path = temp_dir / f"analysis_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        report_path.write_text(combined + "\n\n" + summary, encoding="utf-8")

        # Если прогресс показывается в другом диалоге, создаем сообщение там
        if progress_conversation_id and progress_conversation_id != conversation_id:
            msg_progress_done = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                                       text="✅ Анализ завершён! Результаты сохранены в аккаунт участника команды.")
            db.add(msg_progress_done); db.commit()
        
        # Финальное сообщение с результатами - в диалог участника (где будут результаты)
        msg_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                           text="Готово ✅ Отчёт во вложении.")
        db.add(msg_done); db.flush()
        key_rep = os.path.relpath(report_path, start=UPLOAD_DIR)
        _attach_file(db, msg_done.id, report_path.name, "text/plain", key_rep, report_path.stat().st_size)
        db.commit()
        
        # Извлекаем ошибки и коррекции из анализа
        try:
            from services.analytics_service import AnalyticsService
            from models import TeamMember
            
            # Определяем team_id для пользователя
            team_id = None
            team_member = db.query(TeamMember).filter(
                TeamMember.user_id == user_id
            ).first()
            if team_member:
                team_id = team_member.team_id
            
            # Извлекаем ошибки из финального отчета
            analysis_text = combined + "\n\n" + summary
            AnalyticsService.extract_errors_from_analysis(
                db, user_id, conversation_id, msg_done.id, analysis_text, team_id
            )
        except Exception as e:
            logger.error(f"Ошибка извлечения ошибок из анализа: {e}", exc_info=True)
        
        # Завершаем операцию прогресса
        tracker.complete_operation(operation_id, "Готово ✅ Отчёт во вложении.")

    finally:
        db.close()


async def run_pipeline_from_raw_text(user_id: int, conversation_id: int, raw_text: str, progress_conversation_id: Optional[int] = None):
    """
    Пайплайн для вставленного текста звонка: текст → JSON-диалог → анализ.
    Пропускает транскрибацию, работает напрямую с текстом из чата.
    
    Args:
        user_id: ID пользователя, для которого создается анализ
        conversation_id: ID диалога, куда сохраняются результаты
        raw_text: Транскрибированный текст звонка
        progress_conversation_id: Опциональный ID диалога для отображения прогресса
    """
    db = SessionLocal()
    tracker = get_progress_tracker()
    
    display_conv_id = progress_conversation_id if progress_conversation_id else conversation_id
    
    operation_id = f"raw_text_analysis_{conversation_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    progress = tracker.create_operation(
        operation_id=operation_id,
        total_stages=2,
        title="Анализ текста звонка",
        can_cancel=False
    )
    progress.metadata = {"user_id": user_id, "conversation_id": conversation_id}
    
    try:
        text = raw_text.strip()
        if not text:
            tracker.fail_operation(operation_id, "Текст пуст.")
            err = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                          text="Текст пуст, нечего анализировать.")
            db.add(err); db.commit(); return

        # Шаг 1: преобразование текста
        tracker.update_operation(operation_id, 1, "Обработка текста", "Преобразую текст в формат диалога...")
        msg_read = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                          text="Принял текст звонка. Обрабатываю…")
        db.add(msg_read); db.commit()

        dialogue = _text_to_single_speaker_turns(text)

        # Сохраняем материалы
        temp_dir = Path(UPLOAD_DIR) / str(user_id) / str(conversation_id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        tr_txt = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        dialogue_path = temp_dir / f"dialogue_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"

        tr_txt.write_text(text, encoding="utf-8")
        dialogue_path.write_text(json.dumps(dialogue, ensure_ascii=False, indent=2), encoding="utf-8")

        msg_tr_done = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                              text="Готово: текст обработан. Приложил файлы.")
        db.add(msg_tr_done); db.flush()

        key_txt = os.path.relpath(tr_txt, start=UPLOAD_DIR)
        key_dialogue = os.path.relpath(dialogue_path, start=UPLOAD_DIR)
        _attach_file(db, msg_tr_done.id, tr_txt.name, "text/plain", key_txt, tr_txt.stat().st_size)
        _attach_file(db, msg_tr_done.id, dialogue_path.name, "application/json", key_dialogue, dialogue_path.stat().st_size)
        db.commit()

        # Шаг 2: анализ
        tracker.update_operation(operation_id, 2, "Анализ", "Проверка по чек-листам...")
        msg_an = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                         text="Делаю анализ по чек-листам…")
        db.add(msg_an); db.commit()

        check_dir = Path("checklists")
        check_files = list(check_dir.glob("*.json"))
        analyses: List[str] = []
        dialogue_json_str = dialogue_path.read_text(encoding="utf-8")

        # Получаем скрипт команды для пользователя, если он есть
        team_script = None
        try:
            from services.team_access import get_team_script_for_user
            from models import User as UserModel
            target_user_obj = db.get(UserModel, user_id)
            if target_user_obj:
                team_script = get_team_script_for_user(db, target_user_obj, user_id)
        except Exception as e:
            logger.warning(f"Ошибка получения скрипта команды: {e}")

        # Обрабатываем стандартные чеклисты
        for cf in check_files:
            data = json.loads(cf.read_text(encoding="utf-8"))
            script_title = cf.stem.upper()
            await _analyze_checklist(data, script_title, dialogue_json_str, analyses)
        
        # Обрабатываем скрипт команды, если он есть
        if team_script:
            script_title = team_script.get("title", "Скрипт команды")
            await _analyze_checklist(team_script, script_title, dialogue_json_str, analyses)

        combined = "\n".join(analyses)

        # Получаем финальный промпт из базы данных
        prompt_service = PromptService(db)
        final_prompt_template = prompt_service.get_active_prompt("sales_audit_summary")
        
        if final_prompt_template:
            final_prompt = final_prompt_template.content.format(
                analyses=combined,
                dialogue_json_str=dialogue_json_str
            )
            logger.info(f"🔍 ИСПОЛЬЗУЮ ФИНАЛЬНЫЙ ПРОМПТ ИЗ БД (версия {final_prompt_template.version}): {final_prompt[:100]}...")
        else:
            logger.warning("⚠️ FALLBACK: Использую старый финальный промпт из кода")
            final_prompt = (
                "Суммируй результаты аудита, опираясь на найденные роли manager/client и процитированные фразы менеджера.\n"
                "Сформируй отчёт для РОПа:\n"
                "1) Сильные стороны (3–6) — по делу.\n"
                "2) Зоны роста (3–6) — с конкретными рекомендациями к поведению и формулировкам.\n"
                "3) Примеры фраз МЕНЕДЖЕРА (5–10) из диалога: «фраза» [t=мм:сс–мм:сс].\n"
                "4) Чек-лист исправлений на неделю (5–8 пунктов, измеримо).\n"
                "Только факты из анализов и JSON-диалога — без домыслов.\n\n"
                f"Анализы по чек-листам (с role mapping вверху):\n{combined}\n\n"
                f"ДИАЛОГ_JSON (для ссылок на таймкоды):\n{dialogue_json_str}\n"
            )
        final_resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": final_prompt}],
                temperature=0.2,
            )
        )
        summary = "=== ИТОГОВЫЙ ОТЧЁТ ===\n" + final_resp.choices[0].message.content.strip()

        report_path = temp_dir / f"analysis_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        report_path.write_text(combined + "\n\n" + summary, encoding="utf-8")

        # Если прогресс показывается в другом диалоге, создаем сообщение там
        if progress_conversation_id and progress_conversation_id != conversation_id:
            msg_progress_done = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                                       text="✅ Анализ завершён! Результаты сохранены в аккаунт участника команды.")
            db.add(msg_progress_done); db.commit()
        
        # Финальное сообщение с результатами
        msg_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                           text="Готово ✅ Отчёт во вложении.")
        db.add(msg_done); db.flush()
        key_rep = os.path.relpath(report_path, start=UPLOAD_DIR)
        _attach_file(db, msg_done.id, report_path.name, "text/plain", key_rep, report_path.stat().st_size)
        db.commit()
        
        # Извлекаем ошибки и коррекции из анализа
        try:
            from services.analytics_service import AnalyticsService
            from models import TeamMember
            
            team_id = None
            team_member = db.query(TeamMember).filter(
                TeamMember.user_id == user_id
            ).first()
            if team_member:
                team_id = team_member.team_id
            
            analysis_text = combined + "\n\n" + summary
            AnalyticsService.extract_errors_from_analysis(
                db, user_id, conversation_id, msg_done.id, analysis_text, team_id
            )
        except Exception as e:
            logger.error(f"Ошибка извлечения ошибок из анализа: {e}", exc_info=True)
        
        tracker.complete_operation(operation_id, "Готово ✅ Отчёт во вложении.")

    finally:
        db.close()


async def run_pipeline(user_id: int, conversation_id: int, audio_attachment_id: int, progress_conversation_id: Optional[int] = None):
    """
    Конвейер: конвертация → транскрибация → JSON-диалог (role_map unknown) →
    анализ: ШАГ 0 (определить роли с цитатами), ШАГ 1 (чек-лист с цитатами менеджера) →
    итоговый отчёт с примерами фраз менеджера.
    
    Args:
        user_id: ID пользователя, для которого создается анализ
        conversation_id: ID диалога, куда сохраняются результаты
        audio_attachment_id: ID аудио файла
        progress_conversation_id: Опциональный ID диалога для отображения прогресса (если отличается от conversation_id)
    """
    db = SessionLocal()
    tracker = get_progress_tracker()
    notif_service = get_notification_service()
    
    # Используем progress_conversation_id для отображения прогресса, если указан
    display_conv_id = progress_conversation_id if progress_conversation_id else conversation_id
    
    # Создаем операцию прогресса
    operation_id = f"audio_analysis_{conversation_id}_{audio_attachment_id}"
    progress = tracker.create_operation(
        operation_id=operation_id,
        total_stages=4,
        title="Анализ аудиозаписи",
        can_cancel=False
    )
    progress.metadata = {"user_id": user_id, "conversation_id": conversation_id}
    
    try:
        audio_att = db.get(Attachment, audio_attachment_id)
        if not audio_att:
            tracker.fail_operation(operation_id, "Аудио файл не найден")
            return

        # Шаг 1: конвертация и транскрибация
        tracker.update_operation(operation_id, 1, "Конвертация", "Подготовка аудио файла...")
        # Сообщение в диалог для отображения прогресса
        msg_tr = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                         text="Транскрибирую запись…")
        db.add(msg_tr); db.commit()

        src_abs = Path(UPLOAD_DIR) / audio_att.storage_key
        temp_dir = Path(UPLOAD_DIR) / str(user_id) / str(conversation_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        wav_path = temp_dir / f"{uuid.uuid4().hex}.wav"

        try:
            _ffmpeg_wav(src_abs, wav_path)
        except Exception as e:
            error = FileProcessingError(
                message=f"Ошибка конвертации аудио: {str(e)}",
                filename=audio_att.file_name,
                user_message="Не удалось конвертировать аудио файл. Проверьте формат файла и попробуйте еще раз"
            )
            ErrorHandler.log_error(error, {"user_id": user_id, "conversation_id": conversation_id})
            tracker.fail_operation(operation_id, error.user_message)
            notif_service.error(user_id, "Ошибка конвертации", error.user_message)
            # Сообщение об ошибке - в диалог для отображения прогресса
            err = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                          text=error.user_message)
            db.add(err); db.commit(); return

        # Шаг 2: транскрибация
        tracker.update_operation(operation_id, 2, "Транскрибация", "Распознавание речи...")
        # ElevenLabs → words/text, фолбэк Whisper
        try:
            result = await _elevenlabs_transcribe(wav_path)
        except HTTPStatusError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                note = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                               text="Ключ ElevenLabs не принят. Переключаюсь на Whisper…")
                db.add(note); db.commit()
                result = await asyncio.to_thread(_openai_whisper_transcribe, wav_path)
            else:
                error = ExternalAPIError(
                    message=f"Ошибка API транскрибации: {str(e)}",
                    service="ElevenLabs",
                    status_code=e.response.status_code if e.response else None,
                    user_message="Временная проблема с сервисом транскрибации. Попробуйте позже"
                )
                ErrorHandler.log_error(error, {"user_id": user_id, "conversation_id": conversation_id})
                err = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                              text=error.user_message)
                db.add(err); db.commit(); return
        except TimeoutException as e:
            error = ExternalAPIError(
                message=f"Таймаут при транскрибации: {str(e)}",
                service="ElevenLabs",
                user_message="Превышено время ожидания при обработке аудио. Попробуйте загрузить файл меньшего размера"
            )
            ErrorHandler.log_error(error, {"user_id": user_id, "conversation_id": conversation_id})
            err = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                          text=error.user_message)
            db.add(err); db.commit(); return
        except RequestError as e:
            error = ExternalAPIError(
                message=f"Сетевая ошибка при транскрибации: {str(e)}",
                service="ElevenLabs",
                user_message="Проблема с подключением к сервису транскрибации. Проверьте интернет соединение"
            )
            ErrorHandler.log_error(error, {"user_id": user_id, "conversation_id": conversation_id})
            err = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                          text=error.user_message)
            db.add(err); db.commit(); return
        except Exception as e:
            error = FileProcessingError(
                message=f"Неожиданная ошибка транскрибации: {str(e)}",
                filename=audio_att.file_name,
                user_message="Не удалось обработать аудио файл. Попробуйте еще раз или загрузите другой файл"
            )
            ErrorHandler.log_error(error, {"user_id": user_id, "conversation_id": conversation_id})
            err = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                          text=error.user_message)
            db.add(err); db.commit(); return

        text = (result.get("text") or "").strip()
        words = result.get("words", [])

        # Строгий JSON-диалог (role_map unknown)
        if words:
            dialogue = _words_to_turns(words)
        else:
            dialogue = _text_to_single_speaker_turns(text)

        # Сохраняем материалы транскрибации
        tr_txt = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        tr_json = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        dialogue_path = temp_dir / f"dialogue_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"

        tr_txt.write_text(text, encoding="utf-8")
        tr_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        dialogue_path.write_text(json.dumps(dialogue, ensure_ascii=False, indent=2), encoding="utf-8")

        # Сообщение о готовности транскрипта - в диалог для отображения прогресса
        msg_tr_done = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                              text="Готово: транскрипт получен. Приложил файлы.")
        db.add(msg_tr_done); db.flush()

        key_txt = os.path.relpath(tr_txt, start=UPLOAD_DIR)
        key_json = os.path.relpath(tr_json, start=UPLOAD_DIR)
        key_dialogue = os.path.relpath(dialogue_path, start=UPLOAD_DIR)
        _attach_file(db, msg_tr_done.id, tr_txt.name, "text/plain", key_txt, tr_txt.stat().st_size)
        _attach_file(db, msg_tr_done.id, tr_json.name, "application/json", key_json, tr_json.stat().st_size)
        _attach_file(db, msg_tr_done.id, dialogue_path.name, "application/json", key_dialogue, dialogue_path.stat().st_size)
        db.commit()

        # Шаг 3: анализ (сначала определить роли, потом пройти чек-лист)
        tracker.update_operation(operation_id, 3, "Анализ", "Проверка по чек-листам...")
        # Сообщение о начале анализа - в диалог для отображения прогресса
        msg_an = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                         text="Делаю анализ по чек-листам…")
        db.add(msg_an); db.commit()

        check_dir = Path("checklists")
        check_files = list(check_dir.glob("*.json"))
        analyses: List[str] = []
        dialogue_json_str = dialogue_path.read_text(encoding="utf-8")

        # Получаем скрипт команды для пользователя, если он есть
        team_script = None
        try:
            from services.team_access import get_team_script_for_user
            from models import User as UserModel
            target_user_obj = db.get(UserModel, user_id)
            if target_user_obj:
                team_script = get_team_script_for_user(db, target_user_obj, user_id)
        except Exception as e:
            logger.warning(f"Ошибка получения скрипта команды: {e}")

        # Обрабатываем стандартные чеклисты
        for cf in check_files:
            data = json.loads(cf.read_text(encoding="utf-8"))
            script_title = cf.stem.upper()
            
            # Анализируем чеклист
            await _analyze_checklist(data, script_title, dialogue_json_str, analyses)
        
        # Обрабатываем скрипт команды, если он есть
        if team_script:
            script_title = team_script.get("title", "Скрипт команды")
            await _analyze_checklist(team_script, script_title, dialogue_json_str, analyses)

        combined = "\n".join(analyses)

        # Получаем финальный промпт из базы данных
        prompt_service = PromptService(db)
        final_prompt_template = prompt_service.get_active_prompt("sales_audit_summary")
        
        if final_prompt_template:
            # Используем промпт из базы данных
            final_prompt = final_prompt_template.content.format(
                analyses=combined,
                dialogue_json_str=dialogue_json_str
            )
            logger.info(f"🔍 ИСПОЛЬЗУЮ ФИНАЛЬНЫЙ ПРОМПТ ИЗ БД (версия {final_prompt_template.version}): {final_prompt[:100]}...")
        else:
            # Fallback на старый промпт, если в БД нет активного
            logger.warning("⚠️ FALLBACK: Использую старый финальный промпт из кода")
            final_prompt = (
                "Суммируй результаты аудита, опираясь на найденные роли manager/client и процитированные фразы менеджера.\n"
                "Сформируй отчёт для РОПа:\n"
                "1) Сильные стороны (3–6) — по делу.\n"
                "2) Зоны роста (3–6) — с конкретными рекомендациями к поведению и формулировками.\n"
                "3) Примеры фраз МЕНЕДЖЕРА (5–10) из диалога: «фраза» [t=мм:сс–мм:сс].\n"
                "4) Чек-лист исправлений на неделю (5–8 пунктов, измеримо).\n"
                "Только факты из анализов и JSON-диалога — без домыслов.\n\n"
                f"Анализы по чек-листам (с role mapping вверху):\n{combined}\n\n"
                f"ДИАЛОГ_JSON (для ссылок на таймкоды):\n{dialogue_json_str}\n"
            )
        final_resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": final_prompt}],
                temperature=0.2,
            )
        )
        summary = "=== ИТОГОВЫЙ ОТЧЁТ ===\n" + final_resp.choices[0].message.content.strip()

        report_path = temp_dir / f"analysis_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        report_path.write_text(combined + "\n\n" + summary, encoding="utf-8")

        # Если прогресс показывается в другом диалоге, создаем сообщение там
        if progress_conversation_id and progress_conversation_id != conversation_id:
            msg_progress_done = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                                       text="✅ Анализ завершён! Результаты сохранены в аккаунт участника команды.")
            db.add(msg_progress_done); db.commit()
        
        # Финальное сообщение с результатами - в диалог участника (где будут результаты)
        msg_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                           text="Готово ✅ Отчёт во вложении.")
        db.add(msg_done); db.flush()
        key_rep = os.path.relpath(report_path, start=UPLOAD_DIR)
        _attach_file(db, msg_done.id, report_path.name, "text/plain", key_rep, report_path.stat().st_size)
        db.commit()
        
        # Извлекаем ошибки и коррекции из анализа
        try:
            from services.analytics_service import AnalyticsService
            from models import TeamMember
            
            # Определяем team_id для пользователя
            team_id = None
            team_member = db.query(TeamMember).filter(
                TeamMember.user_id == user_id
            ).first()
            if team_member:
                team_id = team_member.team_id
            
            # Извлекаем ошибки из финального отчета
            analysis_text = combined + "\n\n" + summary
            AnalyticsService.extract_errors_from_analysis(
                db, user_id, conversation_id, msg_done.id, analysis_text, team_id
            )
        except Exception as e:
            logger.error(f"Ошибка извлечения ошибок из анализа: {e}", exc_info=True)
        
        # Завершаем операцию прогресса
        tracker.complete_operation(operation_id, "Готово ✅ Отчёт во вложении.")

    finally:
        db.close()
