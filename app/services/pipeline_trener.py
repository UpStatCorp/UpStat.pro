import os
import re
import json
import uuid
import asyncio
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

import httpx
from httpx import HTTPStatusError, RequestError, TimeoutException
from openai import OpenAI
from sqlalchemy.orm import Session

from models import Message, Attachment
from database import SessionLocal
from services.prompt_service import PromptService
from services.pii_redactor import redact_pii, redact_pii_in_dialogue
from dotenv import load_dotenv

logger = logging.getLogger("main")

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
UPLOAD_DIR = os.path.abspath("uploads")

client = OpenAI(api_key=OPENAI_API_KEY)


def safe_format_prompt(template: str, **kwargs) -> str:
    """
    Универсальное форматирование промпта, которое:
    1. Автоматически заполняет известные плейсхолдеры
    2. Игнорирует неизвестные плейсхолдеры
    3. Никогда не падает с ошибкой
    """
    # Список всех доступных данных для подстановки
    available_data = {
        'data': kwargs.get('data', ''),
        'dialogue_json_str': kwargs.get('dialogue_json_str', ''),
        'checklist_title': kwargs.get('checklist_title', 'Чек-лист'),
        'НАЗВАНИЕ ЧЕК-ЛИСТА': kwargs.get('checklist_title', 'Чек-лист'),
        'checklist_name': kwargs.get('checklist_name', 'Чек-лист'),
        'title': kwargs.get('checklist_title', 'Чек-лист'),
        'name': kwargs.get('checklist_name', 'Чек-лист'),
    }
    
    # Пытаемся отформатировать с доступными данными
    try:
        return template.format(**available_data)
    except KeyError as e:
        # Если есть неизвестные плейсхолдеры, заменяем их на безопасные значения
        missing_placeholder = str(e).strip("'\"")
        print(f"⚠️ Неизвестный плейсхолдер '{missing_placeholder}' в промпте, заменяю на безопасное значение")
        
        # Заменяем неизвестные плейсхолдеры на безопасные значения
        safe_template = template
        for placeholder in re.findall(r'\{([^}]+)\}', template):
            if placeholder not in available_data:
                safe_template = safe_template.replace(f'{{{placeholder}}}', f'[Чек-лист]')
                print(f"   🔄 Заменил {{{placeholder}}} на [Чек-лист]")
        
        # Пытаемся отформатировать снова
        try:
            return safe_template.format(**available_data)
        except Exception as final_error:
            print(f"❌ Критическая ошибка форматирования: {final_error}")
            # В крайнем случае возвращаем оригинальный шаблон
            return template


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
    # with open(audio_path, "rb") as f:
    #     tr = client.audio.transcriptions.create(model="whisper-1", file=f)
    # return {"text": tr.text or "", "words": []}
    return {"text": "OpenAI API temporarily disabled", "words": []}


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


async def run_pipeline_from_text_trener(user_id: int, conversation_id: int, text_attachment_id: int):
    """
    Пайплайн для текстовых файлов (тренер): чтение текста → JSON-диалог → анализ.
    Пропускает транскрибацию.
    """
    print(f"🚀 ТРЕНЕР: Запуск анализа текстового файла для user_id={user_id}, conversation_id={conversation_id}, text_id={text_attachment_id}")
    db = SessionLocal()
    try:
        text_att = db.get(Attachment, text_attachment_id)
        if not text_att:
            return

        # Шаг 1: чтение текста из файла
        msg_read = Message(conversation_id=conversation_id, user_id=None, role="bot",
                          text="Читаю файл с транскрибацией…")
        db.add(msg_read); db.commit()

        src_abs = Path(UPLOAD_DIR) / text_att.storage_key
        temp_dir = Path(UPLOAD_DIR) / str(user_id) / str(conversation_id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            text = _read_text_file(src_abs)
        except Exception as e:
            err = Message(conversation_id=conversation_id, user_id=None, role="bot",
                          text=f"Не удалось прочитать файл: {e}")
            db.add(err); db.commit(); return

        if not text.strip():
            err = Message(conversation_id=conversation_id, user_id=None, role="bot",
                          text="Файл пуст или не содержит текста.")
            db.add(err); db.commit(); return

        # Преобразуем текст в JSON-диалог (без таймкодов, так как их нет)
        dialogue = _text_to_single_speaker_turns(text)

        # Маскируем персональные данные
        text = redact_pii(text)
        dialogue = redact_pii_in_dialogue(dialogue)

        # Сохраняем материалы
        tr_txt = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        dialogue_path = temp_dir / f"dialogue_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"

        tr_txt.write_text(text, encoding="utf-8")
        dialogue_path.write_text(json.dumps(dialogue, ensure_ascii=False, indent=2), encoding="utf-8")

        msg_tr_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                              text="Готово: файл прочитан. Приложил файлы.")
        db.add(msg_tr_done); db.flush()

        key_txt = os.path.relpath(tr_txt, start=UPLOAD_DIR)
        key_dialogue = os.path.relpath(dialogue_path, start=UPLOAD_DIR)
        _attach_file(db, msg_tr_done.id, tr_txt.name, "text/plain", key_txt, tr_txt.stat().st_size)
        _attach_file(db, msg_tr_done.id, dialogue_path.name, "application/json", key_dialogue, dialogue_path.stat().st_size)
        db.commit()

        # Шаг 2: анализ (такой же как в run_pipeline_trener)
        msg_an = Message(conversation_id=conversation_id, user_id=None, role="bot",
                         text="Делаю анализ по чек-листам…")
        db.add(msg_an); db.commit()

        check_dir = Path("checklists_trener")
        check_files = list(check_dir.glob("*.json"))
        analyses: List[str] = []
        dialogue_json_str = dialogue_path.read_text(encoding="utf-8")

        for cf in check_files:
            data = json.loads(cf.read_text(encoding="utf-8"))

            # Пробуем взять промпт тренера из БД; если нет — используем встроенный
            prompt_service = PromptService(db)
            prompt_template = prompt_service.get_active_prompt("sales_trainer")
            if prompt_template:
                # Универсальное форматирование с автоматическим извлечением данных из чек-листа
                checklist_data = json.dumps(data, ensure_ascii=False, indent=2)
                checklist_title = data.get('title', 'Чек-лист')
                checklist_name = data.get('id', 'checklist')
                
                prompt = safe_format_prompt(
                    prompt_template.content,
                    data=checklist_data,
                    dialogue_json_str=dialogue_json_str,
                    checklist_title=checklist_title,
                    checklist_name=checklist_name
                )
                print(f"🔍 ТРЕНЕР: ИСПОЛЬЗУЕТСЯ ПРОМПТ ИЗ БД")
                print(f"   📋 Версия: v{prompt_template.version}")
                print(f"   📝 Название: {prompt_template.title}")
                print(f"   👤 Автор: {prompt_template.creator.name if prompt_template.creator else 'Неизвестно'}")
                print(f"   📅 Создан: {prompt_template.created_at.strftime('%d.%m.%Y %H:%M') if prompt_template.created_at else 'Неизвестно'}")
                print(f"   📏 Размер: {len(prompt_template.content)} символов")
                print(f"   🔤 Начало промпта: {prompt_template.content[:150]}...")
                print(f"   🎯 Итоговый промпт (первые 200 символов): {prompt[:200]}...")
            else:
                print("⚠️ ТРЕНЕР FALLBACK: ПРОМПТ ИЗ БД НЕ НАЙДЕН, ИСПОЛЬЗУЕТСЯ ВСТРОЕННЫЙ")
                prompt = (
                    "Ты — тренер по продажам. У тебя есть СТРОГО JSON-диалог двух спикеров с таймкодами.\n"
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
                print(f"🤖 ТРЕНЕР: Отправка в GPT-4o для анализа {cf.stem}")
                print(f"   📊 Размер промпта: {len(prompt)} символов")
                print(f"   🎯 Модель: gpt-4o, температура: 0.2")
                
                resp = await asyncio.to_thread(
                    lambda: client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.2,
                    )
                )
                
                response_text = resp.choices[0].message.content.strip()
                print(f"✅ ТРЕНЕР: Получен ответ от GPT-4o ({len(response_text)} символов)")
                print(f"   📝 Начало ответа: {response_text[:200]}...")
                
                analyses.append(f"=== {cf.stem.upper()} ===\n{response_text}\n")
            except Exception as e:
                print(f"❌ ТРЕНЕР: Ошибка при обращении к GPT-4o: {e}")
                analyses.append(f"=== {cf.stem.upper()} ===\nОшибка LLM: {e}\n")

        combined = "\n".join(analyses)

        # Проверяем, есть ли промпт для финального отчета ТРЕНЕРА в БД
        final_prompt_template = prompt_service.get_active_prompt("sales_trainer_summary")
        if final_prompt_template:
            # Используем промпт из БД для финального отчета тренера
            final_prompt = safe_format_prompt(
                final_prompt_template.content,
                analyses=combined,
                dialogue_json_str=dialogue_json_str
            )
            print(f"🔍 ТРЕНЕР: ИСПОЛЬЗУЕТСЯ ФИНАЛЬНЫЙ ПРОМПТ ТРЕНЕРА ИЗ БД")
        else:
            # Fallback - используем деловой стиль
            final_prompt = (
                "Суммируй результаты аудита, опираясь на найденные роли manager/client и процитированные фразы менеджера.\n"
                "Сформируй отчёт для тренера:\n"
                "1) Сильные стороны (3–6) — по делу.\n"
                "2) Зоны роста (3–6) — с конкретными рекомендациями к поведению и формулировкам.\n"
                "3) Примеры фраз МЕНЕДЖЕРА (5–10) из диалога: «фраза» [t=мм:сс–мм:сс].\n"
                "4) Чек-лист исправлений на неделю (5–8 пунктов, измеримо).\n"
                "Только факты из анализов и JSON-диалога — без домыслов.\n\n"
                f"Анализы по чек-листам (с role mapping вверху):\n{combined}\n\n"
                f"ДИАЛОГ_JSON (для ссылок на таймкоды):\n{dialogue_json_str}\n"
            )
            print(f"⚠️ ТРЕНЕР: ИСПОЛЬЗУЕТСЯ FALLBACK ФИНАЛЬНЫЙ ПРОМПТ")
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

        msg_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                           text="Готово ✅ Отчёт во вложении.")
        db.add(msg_done); db.flush()
        key_rep = os.path.relpath(report_path, start=UPLOAD_DIR)
        _attach_file(db, msg_done.id, report_path.name, "text/plain", key_rep, report_path.stat().st_size)
        db.commit()

    finally:
        db.close()


async def run_pipeline_from_raw_text_trener(user_id: int, conversation_id: int, raw_text: str):
    """
    Пайплайн тренера для вставленного текста звонка: текст → JSON-диалог → анализ.
    Пропускает транскрибацию, работает напрямую с текстом из чата.
    """
    print(f"🚀 ТРЕНЕР: Запуск анализа вставленного текста для user_id={user_id}, conversation_id={conversation_id}, длина={len(raw_text)}")
    db = SessionLocal()
    try:
        text = raw_text.strip()
        if not text:
            err = Message(conversation_id=conversation_id, user_id=None, role="bot",
                          text="Текст пуст, нечего анализировать.")
            db.add(err); db.commit(); return

        # Шаг 1: преобразование текста
        msg_read = Message(conversation_id=conversation_id, user_id=None, role="bot",
                          text="Принял текст звонка. Обрабатываю…")
        db.add(msg_read); db.commit()

        dialogue = _text_to_single_speaker_turns(text)

        # Маскируем персональные данные
        text = redact_pii(text)
        dialogue = redact_pii_in_dialogue(dialogue)

        # Сохраняем материалы
        temp_dir = Path(UPLOAD_DIR) / str(user_id) / str(conversation_id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        tr_txt = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        dialogue_path = temp_dir / f"dialogue_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"

        tr_txt.write_text(text, encoding="utf-8")
        dialogue_path.write_text(json.dumps(dialogue, ensure_ascii=False, indent=2), encoding="utf-8")

        msg_tr_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                              text="Готово: текст обработан. Приложил файлы.")
        db.add(msg_tr_done); db.flush()

        key_txt = os.path.relpath(tr_txt, start=UPLOAD_DIR)
        key_dialogue = os.path.relpath(dialogue_path, start=UPLOAD_DIR)
        _attach_file(db, msg_tr_done.id, tr_txt.name, "text/plain", key_txt, tr_txt.stat().st_size)
        _attach_file(db, msg_tr_done.id, dialogue_path.name, "application/json", key_dialogue, dialogue_path.stat().st_size)
        db.commit()

        # Шаг 2: анализ
        msg_an = Message(conversation_id=conversation_id, user_id=None, role="bot",
                         text="Делаю анализ по чек-листам…")
        db.add(msg_an); db.commit()

        check_dir = Path("checklists_trener")
        check_files = list(check_dir.glob("*.json"))
        analyses: List[str] = []
        dialogue_json_str = dialogue_path.read_text(encoding="utf-8")

        for cf in check_files:
            data = json.loads(cf.read_text(encoding="utf-8"))

            prompt_service = PromptService(db)
            prompt_template = prompt_service.get_active_prompt("sales_trainer")
            if prompt_template:
                checklist_data = json.dumps(data, ensure_ascii=False, indent=2)
                checklist_title = data.get('title', 'Чек-лист')
                checklist_name = data.get('id', 'checklist')
                
                prompt = safe_format_prompt(
                    prompt_template.content,
                    data=checklist_data,
                    dialogue_json_str=dialogue_json_str,
                    checklist_title=checklist_title,
                    checklist_name=checklist_name
                )
                print(f"🔍 ТРЕНЕР RAW TEXT: ИСПОЛЬЗУЕТСЯ ПРОМПТ ИЗ БД")
            else:
                print("⚠️ ТРЕНЕР RAW TEXT FALLBACK: ПРОМПТ ИЗ БД НЕ НАЙДЕН, ИСПОЛЬЗУЕТСЯ ВСТРОЕННЫЙ")
                prompt = (
                    "Ты — тренер по продажам. У тебя есть СТРОГО JSON-диалог двух спикеров с таймкодами.\n"
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
                print(f"🤖 ТРЕНЕР RAW TEXT: Отправка в GPT-4o для анализа {cf.stem}")
                resp = await asyncio.to_thread(
                    lambda: client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.2,
                    )
                )
                response_text = resp.choices[0].message.content.strip()
                print(f"✅ ТРЕНЕР RAW TEXT: Получен ответ от GPT-4o ({len(response_text)} символов)")
                analyses.append(f"=== {cf.stem.upper()} ===\n{response_text}\n")
            except Exception as e:
                print(f"❌ ТРЕНЕР RAW TEXT: Ошибка при обращении к GPT-4o: {e}")
                analyses.append(f"=== {cf.stem.upper()} ===\nОшибка LLM: {e}\n")

        combined = "\n".join(analyses)

        prompt_service = PromptService(db)
        final_prompt_template = prompt_service.get_active_prompt("sales_trainer_summary")
        if final_prompt_template:
            final_prompt = safe_format_prompt(
                final_prompt_template.content,
                analyses=combined,
                dialogue_json_str=dialogue_json_str
            )
            print(f"🔍 ТРЕНЕР RAW TEXT: ИСПОЛЬЗУЕТСЯ ФИНАЛЬНЫЙ ПРОМПТ ТРЕНЕРА ИЗ БД")
        else:
            final_prompt = (
                "Суммируй результаты аудита, опираясь на найденные роли manager/client и процитированные фразы менеджера.\n"
                "Сформируй отчёт для тренера:\n"
                "1) Сильные стороны (3–6) — по делу.\n"
                "2) Зоны роста (3–6) — с конкретными рекомендациями к поведению и формулировкам.\n"
                "3) Примеры фраз МЕНЕДЖЕРА (5–10) из диалога: «фраза» [t=мм:сс–мм:сс].\n"
                "4) Чек-лист исправлений на неделю (5–8 пунктов, измеримо).\n"
                "Только факты из анализов и JSON-диалога — без домыслов.\n\n"
                f"Анализы по чек-листам (с role mapping вверху):\n{combined}\n\n"
                f"ДИАЛОГ_JSON (для ссылок на таймкоды):\n{dialogue_json_str}\n"
            )
            print(f"⚠️ ТРЕНЕР RAW TEXT: ИСПОЛЬЗУЕТСЯ FALLBACK ФИНАЛЬНЫЙ ПРОМПТ")
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

        msg_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                           text="Готово ✅ Отчёт во вложении.")
        db.add(msg_done); db.flush()
        key_rep = os.path.relpath(report_path, start=UPLOAD_DIR)
        _attach_file(db, msg_done.id, report_path.name, "text/plain", key_rep, report_path.stat().st_size)
        db.commit()

    finally:
        db.close()


async def run_pipeline_trener(user_id: int, conversation_id: int, audio_attachment_id: int):
    """
    Конвейер для тренера: конвертация → транскрибация → JSON-диалог (role_map unknown) →
    анализ: ШАГ 0 (определить роли с цитатами), ШАГ 1 (чек-лист с цитатами менеджера) →
    итоговый отчёт с примерами фраз менеджера.
    """
    print(f"🚀 ТРЕНЕР: Запуск анализа для user_id={user_id}, conversation_id={conversation_id}, audio_id={audio_attachment_id}")
    db = SessionLocal()
    try:
        audio_att = db.get(Attachment, audio_attachment_id)
        if not audio_att:
            return

        # Шаг 1: транскрибация
        msg_tr = Message(conversation_id=conversation_id, user_id=None, role="bot",
                         text="Транскрибирую запись…")
        db.add(msg_tr); db.commit()

        src_abs = Path(UPLOAD_DIR) / audio_att.storage_key
        temp_dir = Path(UPLOAD_DIR) / str(user_id) / str(conversation_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        wav_path = temp_dir / f"{uuid.uuid4().hex}.wav"

        try:
            _ffmpeg_wav(src_abs, wav_path)
        except Exception as e:
            err = Message(conversation_id=conversation_id, user_id=None, role="bot",
                          text=f"Не удалось конвертировать аудио: {e}")
            db.add(err); db.commit(); return

        # ElevenLabs → words/text, фолбэк Whisper
        try:
            result = await _elevenlabs_transcribe(wav_path)
        except HTTPStatusError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                note = Message(conversation_id=conversation_id, user_id=None, role="bot",
                               text="Ключ ElevenLabs не принят. Переключаюсь на Whisper…")
                db.add(note); db.commit()
                result = await asyncio.to_thread(_openai_whisper_transcribe, wav_path)
            else:
                err = Message(conversation_id=conversation_id, user_id=None, role="bot",
                              text=f"Ошибка транскрибации: {e}")
                db.add(err); db.commit(); return
        except TimeoutException as e:
            err = Message(conversation_id=conversation_id, user_id=None, role="bot",
                          text=f"Таймаут транскрибации: {e}")
            db.add(err); db.commit(); return
        except RequestError as e:
            err = Message(conversation_id=conversation_id, user_id=None, role="bot",
                          text=f"Сетевая ошибка транскрибации: {e}")
            db.add(err); db.commit(); return
        except Exception as e:
            err = Message(conversation_id=conversation_id, user_id=None, role="bot",
                          text=f"Ошибка транскрибации: {e}")
            db.add(err); db.commit(); return

        text = (result.get("text") or "").strip()
        words = result.get("words", [])

        # Строгий JSON-диалог (role_map unknown)
        if words:
            dialogue = _words_to_turns(words)
        else:
            dialogue = _text_to_single_speaker_turns(text)

        # Маскируем персональные данные
        text = redact_pii(text)
        dialogue = redact_pii_in_dialogue(dialogue)

        # Сохраняем материалы транскрибации
        tr_txt = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        tr_json = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        dialogue_path = temp_dir / f"dialogue_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"

        tr_txt.write_text(text, encoding="utf-8")
        tr_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        dialogue_path.write_text(json.dumps(dialogue, ensure_ascii=False, indent=2), encoding="utf-8")

        msg_tr_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                              text="Готово: транскрипт получен. Приложил файлы.")
        db.add(msg_tr_done); db.flush()

        key_txt = os.path.relpath(tr_txt, start=UPLOAD_DIR)
        key_json = os.path.relpath(tr_json, start=UPLOAD_DIR)
        key_dialogue = os.path.relpath(dialogue_path, start=UPLOAD_DIR)
        _attach_file(db, msg_tr_done.id, tr_txt.name, "text/plain", key_txt, tr_txt.stat().st_size)
        _attach_file(db, msg_tr_done.id, tr_json.name, "application/json", key_json, tr_json.stat().st_size)
        _attach_file(db, msg_tr_done.id, dialogue_path.name, "application/json", key_dialogue, dialogue_path.stat().st_size)
        db.commit()

        # Шаг 2: анализ (сначала определить роли, потом пройти чек-лист)
        msg_an = Message(conversation_id=conversation_id, user_id=None, role="bot",
                         text="Делаю анализ по чек-листам…")
        db.add(msg_an); db.commit()

        check_dir = Path("checklists_trener")
        check_files = list(check_dir.glob("*.json"))
        analyses: List[str] = []
        dialogue_json_str = dialogue_path.read_text(encoding="utf-8")

        for cf in check_files:
            data = json.loads(cf.read_text(encoding="utf-8"))

            # Пробуем взять промпт тренера из БД; если нет — используем встроенный
            prompt_service = PromptService(db)
            prompt_template = prompt_service.get_active_prompt("sales_trainer")
            if prompt_template:
                # Универсальное форматирование с автоматическим извлечением данных из чек-листа
                checklist_data = json.dumps(data, ensure_ascii=False, indent=2)
                checklist_title = data.get('title', 'Чек-лист')
                checklist_name = data.get('id', 'checklist')
                
                prompt = safe_format_prompt(
                    prompt_template.content,
                    data=checklist_data,
                    dialogue_json_str=dialogue_json_str,
                    checklist_title=checklist_title,
                    checklist_name=checklist_name
                )
                print(f"🔍 ТРЕНЕР: ИСПОЛЬЗУЕТСЯ ПРОМПТ ИЗ БД")
                print(f"   📋 Версия: v{prompt_template.version}")
                print(f"   📝 Название: {prompt_template.title}")
                print(f"   👤 Автор: {prompt_template.creator.name if prompt_template.creator else 'Неизвестно'}")
                print(f"   📅 Создан: {prompt_template.created_at.strftime('%d.%m.%Y %H:%M') if prompt_template.created_at else 'Неизвестно'}")
                print(f"   📏 Размер: {len(prompt_template.content)} символов")
                print(f"   🔤 Начало промпта: {prompt_template.content[:150]}...")
                print(f"   🎯 Итоговый промпт (первые 200 символов): {prompt[:200]}...")
            else:
                print("⚠️ ТРЕНЕР FALLBACK: ПРОМПТ ИЗ БД НЕ НАЙДЕН, ИСПОЛЬЗУЕТСЯ ВСТРОЕННЫЙ")
                prompt = (
                    "Ты — тренер по продажам. У тебя есть СТРОГО JSON-диалог двух спикеров с таймкодами.\n"
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
                print(f"🤖 ТРЕНЕР: Отправка в GPT-4o для анализа {cf.stem}")
                print(f"   📊 Размер промпта: {len(prompt)} символов")
                print(f"   🎯 Модель: gpt-4o, температура: 0.2")
                
                resp = await asyncio.to_thread(
                    lambda: client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.2,
                    )
                )
                
                response_text = resp.choices[0].message.content.strip()
                print(f"✅ ТРЕНЕР: Получен ответ от GPT-4o ({len(response_text)} символов)")
                print(f"   📝 Начало ответа: {response_text[:200]}...")
                
                analyses.append(f"=== {cf.stem.upper()} ===\n{response_text}\n")
            except Exception as e:
                print(f"❌ ТРЕНЕР: Ошибка при обращении к GPT-4o: {e}")
                analyses.append(f"=== {cf.stem.upper()} ===\nОшибка LLM: {e}\n")

        combined = "\n".join(analyses)

        # Проверяем, есть ли промпт для финального отчета ТРЕНЕРА в БД
        final_prompt_template = prompt_service.get_active_prompt("sales_trainer_summary")
        if final_prompt_template:
            # Используем промпт из БД для финального отчета тренера
            final_prompt = safe_format_prompt(
                final_prompt_template.content,
                analyses=combined,
                dialogue_json_str=dialogue_json_str
            )
            print(f"🔍 ТРЕНЕР: ИСПОЛЬЗУЕТСЯ ФИНАЛЬНЫЙ ПРОМПТ ТРЕНЕРА ИЗ БД")
        else:
            # Fallback - используем деловой стиль
            final_prompt = (
                "Суммируй результаты аудита, опираясь на найденные роли manager/client и процитированные фразы менеджера.\n"
                "Сформируй отчёт для тренера:\n"
                "1) Сильные стороны (3–6) — по делу.\n"
                "2) Зоны роста (3–6) — с конкретными рекомендациями к поведению и формулировкам.\n"
                "3) Примеры фраз МЕНЕДЖЕРА (5–10) из диалога: «фраза» [t=мм:сс–мм:сс].\n"
                "4) Чек-лист исправлений на неделю (5–8 пунктов, измеримо).\n"
                "Только факты из анализов и JSON-диалога — без домыслов.\n\n"
                f"Анализы по чек-листам (с role mapping вверху):\n{combined}\n\n"
                f"ДИАЛОГ_JSON (для ссылок на таймкоды):\n{dialogue_json_str}\n"
            )
            print(f"⚠️ ТРЕНЕР: ИСПОЛЬЗУЕТСЯ FALLBACK ФИНАЛЬНЫЙ ПРОМПТ")
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

        msg_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                           text="Готово ✅ Отчёт во вложении.")
        db.add(msg_done); db.flush()
        key_rep = os.path.relpath(report_path, start=UPLOAD_DIR)
        _attach_file(db, msg_done.id, report_path.name, "text/plain", key_rep, report_path.stat().st_size)
        db.commit()

    finally:
        db.close()
