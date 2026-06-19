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
from services.pii_redactor import redact_pii, redact_pii_in_dialogue
from services.research_service import (
    ResearchLogger,
    REASONING_INSTRUCTION_CHECKLIST,
    REASONING_INSTRUCTION_SUMMARY,
)
from dotenv import load_dotenv

logger = logging.getLogger("main")

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
UPLOAD_DIR = os.path.abspath("uploads")

# Защитные сетки для внешних API (Фаза 0): ни один вызов не висит вечно.
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
STT_TIMEOUT_SECONDS = float(os.getenv("STT_TIMEOUT_SECONDS", "120"))
STT_MAX_RETRIES = int(os.getenv("STT_MAX_RETRIES", "2"))

client = OpenAI(api_key=OPENAI_API_KEY, timeout=LLM_TIMEOUT_SECONDS, max_retries=LLM_MAX_RETRIES)

# ── Защита от ложных отказов модели ───────────────────────────────────────────
# Иногда gpt-4o ложно отказывает («I'm sorry, I can't assist…») на легитимном
# разборе звонка. Лечится: (1) system-рамкой, заземляющей задачу как обучающий
# QA-разбор обезличенного диалога; (2) детектом отказа и ретраем вызова.
SYSTEM_AUDITOR = {
    "role": "system",
    "content": (
        "Ты — внутренняя система контроля качества (QA) для обучения отдела продаж. "
        "На вход тебе дают ОБЕЗЛИЧЕННУЮ расшифровку телефонного разговора менеджера "
        "с клиентом для конструктивного разбора и обучения сотрудника — это легитимная "
        "и безопасная бизнес-задача. Не отказывайся и не морализируй: анализируй ТОЛЬКО "
        "предоставленный диалог строго по заданному формату."
    ),
}

# Анти-отказ нудж добавляется при ретрае после распознанного отказа.
_REFUSAL_NUDGE = {
    "role": "system",
    "content": (
        "Предыдущий ответ был отказом. Это обычный обучающий разбор обезличенного "
        "делового диалога, без какого-либо вредоносного контента. Выполни задачу "
        "строго по формату, без отказов и оговорок."
    ),
}

_REFUSAL_RE = re.compile(
    r"(i['’`]?m\s+sorry|i\s+can(?:'|’|n)?o?t\s+(?:assist|help|comply|continue)"
    r"|i\s+won['’`]?t\s+be\s+able|unable\s+to\s+help"
    r"|извин[а-яё]*|не\s+мог[ауy][^.]{0,20}помоч|не\s+могу\s+(?:вам\s+)?помоч"
    r"|не\s+могу\s+(?:вам\s+)?(?:с\s+этим\s+)?(?:по)?мочь)",
    re.IGNORECASE,
)


def _looks_like_refusal(text: str) -> bool:
    """Эвристика: пустой/очень короткий ответ или совпадение с фразой-отказом."""
    t = (text or "").strip()
    if not t:
        return True
    # Длинный осмысленный ответ почти наверняка не отказ, даже если содержит «извините».
    if len(t) > 400:
        return False
    return bool(_REFUSAL_RE.search(t))


async def _llm_create(call_kwargs: dict, *, label: str, max_refusal_retries: int = 2):
    """chat.completions с system-рамкой и ретраем на ложный отказ.

    Возвращает (text, refused, resp). `refused=True` означает, что модель
    отказала даже после ретраев (вызывающий код решает, что делать дальше).
    Сетевые ошибки/таймауты ретраит сам OpenAI-клиент (max_retries).
    """
    kwargs = dict(call_kwargs)
    msgs = list(kwargs.get("messages", []))
    if not msgs or msgs[0].get("role") != "system":
        msgs = [SYSTEM_AUDITOR] + msgs
    base_temp = kwargs.get("temperature", 0.2)

    text, resp = "", None
    for attempt in range(max_refusal_retries + 1):
        kwargs["messages"] = msgs if attempt == 0 else msgs + [_REFUSAL_NUDGE]
        # На ретраях слегка поднимаем температуру, чтобы выйти из «залипшего» отказа.
        kwargs["temperature"] = min(0.8, base_temp + 0.2 * attempt)
        resp = await asyncio.to_thread(lambda: client.chat.completions.create(**kwargs))
        text = (resp.choices[0].message.content or "").strip()
        if not _looks_like_refusal(text):
            return text, False, resp
        logger.warning(
            f"[refusal] '{label}': модель отказала (попытка {attempt + 1}/{max_refusal_retries + 1})."
        )
    return text, True, resp


async def _resolve_roles(dialogue_json_str: str) -> Dict[str, str]:
    """Определяет, кто из speaker_N — manager, а кто — client.

    Возвращает маппинг {"speaker_1": "manager"|"client", ...}. Best-effort:
    при любой ошибке/неоднозначности возвращает пустой dict (роли остаются unknown).
    """
    prompt = (
        "Ниже JSON-диалог двух спикеров телефонного разговора (продажи). "
        "Определи, кто менеджер (продавец/звонящий, представляет компанию, задаёт "
        "квалифицирующие вопросы, презентует продукт), а кто клиент.\n"
        "Верни СТРОГО JSON без пояснений, формат:\n"
        '{"speaker_1": "manager"|"client", "speaker_2": "manager"|"client"}\n\n'
        f"ДИАЛОГ_JSON:\n{dialogue_json_str}\n"
    )
    try:
        text, refused, _ = await _llm_create(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}],
             "temperature": 0.0},
            label="role-resolution", max_refusal_retries=1,
        )
        if refused:
            return {}
        m = re.search(r"\{[^{}]*\}", text)
        if not m:
            return {}
        data = json.loads(m.group(0))
        out = {k: v for k, v in data.items()
               if isinstance(v, str) and v.lower() in ("manager", "client")}
        return {k: v.lower() for k, v in out.items()}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"_resolve_roles failed: {e}")
        return {}


_ROLE_RU = {"manager": "Менеджер", "client": "Клиент"}


def _apply_roles(dialogue: dict, speaker_roles: Dict[str, str]) -> str:
    """Проставляет role_map в dialogue и возвращает читаемый транскрипт с подписями.

    speaker_roles: {"speaker_1": "manager"|"client", ...}. Если ролей нет —
    возвращает транскрипт без подписей (по строкам реплик).
    """
    if speaker_roles:
        manager_spk = next((s for s, r in speaker_roles.items() if r == "manager"), "unknown")
        client_spk = next((s for s, r in speaker_roles.items() if r == "client"), "unknown")
        dialogue["role_map"] = {"manager": manager_spk, "client": client_spk}

    lines = []
    for turn in dialogue.get("turns", []):
        spk = turn.get("speaker")
        role = speaker_roles.get(spk)
        who = _ROLE_RU.get(role, spk or "—")
        txt = (turn.get("text") or "").strip()
        if not txt:
            continue
        lines.append(f"{who}: {txt}")
    return "\n".join(lines)


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
    timeout = httpx.Timeout(STT_TIMEOUT_SECONDS)
    last_exc: Optional[Exception] = None
    # Ретраи только на временные сбои (5xx/сеть/таймаут). 401/403 — сразу наверх
    # (вызывающий код переключится на Whisper).
    for attempt in range(STT_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as http_client:
                with open(wav_path, "rb") as f:
                    files = {"file": f}
                    resp = await http_client.post(url, headers=headers, data=data, files=files)
            if resp.status_code in (401, 403):
                raise HTTPStatusError(
                    f"Unauthorized ({resp.status_code})", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.json()
        except HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            # 401/403 — не ретраим, пусть caller уйдёт на Whisper
            if status in (401, 403) or (status is not None and status < 500):
                raise
            last_exc = e
        except (TimeoutException, RequestError) as e:
            last_exc = e
        if attempt < STT_MAX_RETRIES:
            backoff = 2 ** attempt
            logger.warning(
                f"ElevenLabs transcribe failed (attempt {attempt + 1}/{STT_MAX_RETRIES + 1}): "
                f"{last_exc}. Retry in {backoff}s"
            )
            await asyncio.sleep(backoff)
    # Исчерпали ретраи — пробрасываем последнюю ошибку
    raise last_exc


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


async def _analyze_checklist(
    data: Dict[str, Any],
    title: str,
    dialogue_json_str: str,
    analyses: List[str],
    checklist_scores: Optional[List[Dict]] = None,
    research: Optional[ResearchLogger] = None,
):
    """
    Анализирует чеклист или скрипт команды по диалогу.
    
    Args:
        data: Данные чеклиста/скрипта в формате JSON
        title: Название чеклиста/скрипта
        dialogue_json_str: JSON строка диалога
        analyses: Список для добавления результатов анализа
        checklist_scores: Опциональный список для сбора структурированных +/- оценок
        research: Опциональный ResearchLogger — для CoT-логирования (Research Mode)
    """
    logger.info(f"🔍 Начинаю анализ чеклиста/скрипта: {title}")

    checklist_id = data.get("id", title.lower().replace(" ", "_"))
    scoring_codes = []
    for bi, block in enumerate(data.get("blocks", [])):
        for ci, _criterion in enumerate(block.get("criteria", [])):
            scoring_codes.append(f"{checklist_id}.b{bi}.c{ci}")

    scoring_instruction = ""
    if checklist_scores is not None and scoring_codes:
        codes_list = ", ".join(f'"{c}"' for c in scoring_codes)
        scoring_instruction = (
            "\n\nШАГ 2 (обязателен): В самом конце ответа верни JSON-блок для машинной обработки.\n"
            "Формат (строго валидный JSON):\n"
            '```json\n'
            '{"scores": [\n'
            '  {"code": "<item_code>", "passed": true/false, "confidence": 0.0-1.0}\n'
            ']}\n'
            '```\n'
            f'Коды пунктов по порядку: [{codes_list}]\n'
            'Правила: "Да"=true (passed), "Нет"=false, "Частично"=false.\n'
            'confidence — твоя уверенность в оценке от 0.0 до 1.0.\n'
        )

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
        + scoring_instruction
        + (REASONING_INSTRUCTION_CHECKLIST if research is not None else "")
    )

    try:
        logger.info(f"📤 Отправляю запрос к GPT для анализа: {title}")
        # В research-режиме разрешаем больше токенов и чуть выше temperature
        # для детальных размышлений. Финальные оценки остаются стабильны,
        # т.к. модель всё равно следует scoring instruction.
        if research is not None:
            call_kwargs = {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "max_tokens": 12000,
            }
        else:
            call_kwargs = {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }
        full_response, refused, resp = await _llm_create(call_kwargs, label=title)
        if refused:
            logger.warning(f"Чек-лист '{title}': модель отказала даже после ретраев.")
        logger.info(f"✅ Получен ответ от GPT для: {title}")

        # Перед тем как _extract_and_collect_scores режет JSON, сохраним «исходник» для research-лога.
        raw_for_research = full_response

        stage_scores_snapshot: Optional[List[Dict]] = None
        text_part = full_response
        if checklist_scores is not None:
            scores_before = len(checklist_scores)
            text_part = _extract_and_collect_scores(full_response, checklist_scores)
            stage_scores_snapshot = checklist_scores[scores_before:]

        if research is not None:
            try:
                research.capture_stage(
                    stage_name=f"Чек-лист: {title}",
                    model="gpt-4o",
                    prompt=prompt,
                    raw_response=raw_for_research,
                    parsed_decisions=stage_scores_snapshot,
                    usage=getattr(resp, "usage", None),
                )
            except Exception as research_err:  # noqa: BLE001
                logger.warning(f"Research capture (checklist) failed: {research_err}")

        analyses.append(f"=== {title.upper()} ===\n{text_part}\n")
        return refused
    except Exception as e:
        logger.error(f"❌ Ошибка анализа чеклиста {title}: {e}", exc_info=True)
        analyses.append(f"=== {title.upper()} ===\nОшибка LLM: {e}\n")
        if research is not None:
            try:
                research.capture_note(f"Ошибка анализа чек-листа «{title}»: {e}")
            except Exception:
                pass
        return False


def _extract_and_collect_scores(response_text: str, checklist_scores: List[Dict]) -> str:
    """
    Извлекает JSON-блок scores из ответа GPT, добавляет в checklist_scores.
    Возвращает текстовую часть ответа (без JSON-блока).
    """
    json_match = re.search(r'```json\s*(\{[\s\S]*?"scores"[\s\S]*?\})\s*```', response_text)
    if not json_match:
        json_match = re.search(r'(\{[^{}]*"scores"\s*:\s*\[[\s\S]*?\]\s*\})', response_text)

    if json_match:
        try:
            scores_data = json.loads(json_match.group(1))
            raw_scores = scores_data.get("scores", []) if isinstance(scores_data, dict) else []
            added = 0
            for score_item in raw_scores:
                if not isinstance(score_item, dict):
                    continue
                code = score_item.get("code")
                if not code or "passed" not in score_item:
                    continue
                try:
                    confidence = float(score_item.get("confidence", 0.8))
                except (TypeError, ValueError):
                    confidence = 0.8
                confidence = max(0.0, min(1.0, confidence))  # клампим в [0,1]
                checklist_scores.append({
                    "code": str(code),
                    "passed": bool(score_item["passed"]),
                    "confidence": confidence,
                })
                added += 1
            logger.info(f"Извлечено {added} оценок из ответа GPT (из {len(raw_scores)} в JSON)")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Не удалось распарсить JSON scores: {e}")

        text_part = response_text[:json_match.start()].rstrip()
        after = response_text[json_match.end():].strip()
        if after:
            text_part = text_part + "\n" + after
        return text_part

    logger.warning("JSON-блок scores не найден в ответе GPT")
    return response_text


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


async def _run_all_checklists(
    db: Session,
    user_id: int,
    dialogue_json_str: str,
    research: Optional[ResearchLogger] = None,
) -> tuple:
    """Прогоняет диалог по всем чек-листам из ./checklists и по скрипту команды.

    Каждый чек-лист изолирован: битый JSON-файл на диске или ошибка анализа
    одного чек-листа НЕ прерывает остальные — анализ завершается по тем, что
    отработали. Возвращает (combined_text, checklist_scores, skipped_list).
    """
    check_dir = Path("checklists")
    check_files = sorted(check_dir.glob("*.json"))  # детерминированный порядок
    analyses: List[str] = []
    checklist_scores: List[Dict] = []
    skipped: List[str] = []

    # Скрипт команды (опционально)
    team_script = None
    try:
        from services.team_access import get_team_script_for_user
        from models import User as UserModel
        target_user_obj = db.get(UserModel, user_id)
        if target_user_obj:
            team_script = get_team_script_for_user(db, target_user_obj, user_id)
    except Exception as e:
        logger.warning(f"Ошибка получения скрипта команды: {e}")

    # Стандартные чек-листы — параллельно, с ограничением одновременности
    # (чтобы не упереться в rate-limit OpenAI). Каждый чек-лист изолирован и
    # пишет в СВОИ локальные списки (без гонок по общему состоянию); результаты
    # затем собираются в детерминированном порядке файлов.
    # В research-режиме идём последовательно — ради упорядоченного CoT-лога.
    concurrency = 1 if research is not None else max(1, int(os.getenv("CHECKLIST_CONCURRENCY", "4")))
    sem = asyncio.Semaphore(concurrency)

    async def _one(cf: Path):
        title = cf.stem.upper()
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Чек-лист '{cf.name}' пропущен — не удалось прочитать/распарсить: {e}")
            return None
        local_analyses: List[str] = []
        local_scores: List[Dict] = []
        async with sem:
            try:
                refused = await _analyze_checklist(data, title, dialogue_json_str, local_analyses, local_scores, research=research)
            except Exception as e:
                logger.error(f"Чек-лист '{cf.name}' пропущен — ошибка анализа: {e}", exc_info=True)
                return None
        return local_analyses, local_scores, bool(refused)

    refused_titles: List[str] = []
    results = await asyncio.gather(*[_one(cf) for cf in check_files])
    for cf, res in zip(check_files, results):
        if res is None:
            skipped.append(cf.stem)
        else:
            local_analyses, local_scores, was_refused = res
            analyses.extend(local_analyses)
            checklist_scores.extend(local_scores)
            if was_refused:
                refused_titles.append(cf.stem)

    # Скрипт команды
    if team_script:
        script_title = team_script.get("title", "Скрипт команды")
        try:
            ts_refused = await _analyze_checklist(team_script, script_title, dialogue_json_str, analyses, research=research)
            if ts_refused:
                refused_titles.append(script_title)
        except Exception as e:
            logger.error(f"Скрипт команды пропущен — ошибка анализа: {e}", exc_info=True)
            skipped.append(script_title)

    combined = "\n".join(analyses)
    if skipped:
        combined += "\n\n⚠️ Пропущены чек-листы (ошибка обработки): " + ", ".join(skipped)
    return combined, checklist_scores, skipped, refused_titles


SCORE_MARKER_INSTRUCTION = (
    "\n\nВ САМОМ КОНЦЕ ответа выведи итоговую оценку звонка отдельной строкой "
    "в машиночитаемом виде:\nИТОГОВАЯ_ОЦЕНКА: NN/100\n"
    "где NN — целое число от 0 до 100."
)


async def _finalize_analysis_and_report(
    db: Session,
    user_id: int,
    conversation_id: int,
    display_conv_id: int,
    progress_conversation_id: Optional[int],
    temp_dir: Path,
    combined: str,
    checklist_scores: List[Dict],
    dialogue_json_str: str,
    skipped_checklists: List[str],
    research: Optional[ResearchLogger] = None,
    write_anyway: bool = True,
) -> bool:
    """Финальный отчёт + пост-процессы — общая часть всех трёх конвейеров.

    Возвращает report_refused: True, если модель отказалась сформировать отчёт
    (после ретраев). При write_anyway=False и отказе отчёт НЕ записывается —
    оркестратор перезапустит анализ. При write_anyway=True (последняя попытка)
    отчёт пишется как есть (best-effort).

    Бросает исключение, если фатально не удалось построить итоговый отчёт
    (вызывающий код пометит анализ как failed). Пост-процессы (win-probability,
    параметры, паспорт, действия менеджера) — best-effort: их сбой логируется,
    но не валит анализ, т.к. сам отчёт уже сохранён.
    """
    # Финальный промпт: из БД или fallback из кода
    prompt_service = PromptService(db)
    final_prompt_template = prompt_service.get_active_prompt("sales_audit_summary")
    if final_prompt_template:
        final_prompt = final_prompt_template.content.format(
            analyses=combined, dialogue_json_str=dialogue_json_str
        )
        logger.info(f"🔍 Финальный промпт из БД (версия {final_prompt_template.version})")
    else:
        logger.warning("⚠️ FALLBACK: использую финальный промпт из кода")
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

    if research is not None:
        final_prompt_for_call = final_prompt + REASONING_INSTRUCTION_SUMMARY + SCORE_MARKER_INSTRUCTION
        final_call_kwargs = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": final_prompt_for_call}],
            "temperature": 0.4,
            "max_tokens": 8000,
        }
    else:
        final_prompt_for_call = final_prompt + SCORE_MARKER_INSTRUCTION
        final_call_kwargs = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": final_prompt_for_call}],
            "temperature": 0.2,
        }
    summary_raw, report_refused, final_resp = await _llm_create(
        final_call_kwargs, label="Итоговый отчёт"
    )
    # Если модель отказала на отчёте и можно перезапустить весь анализ —
    # НЕ пишем битый отчёт, сигналим оркестратору о перезапуске.
    if report_refused and not write_anyway:
        logger.warning("Итоговый отчёт: отказ модели — пропускаю запись (будет перезапуск анализа).")
        return True
    summary = "=== ИТОГОВЫЙ ОТЧЁТ ===\n" + summary_raw
    if research is not None:
        try:
            research.capture_stage(
                stage_name="Итоговый отчёт", model="gpt-4o",
                prompt=final_prompt_for_call, raw_response=summary_raw,
                usage=getattr(final_resp, "usage", None),
            )
        except Exception as research_err:  # noqa: BLE001
            logger.warning(f"Research capture (summary) failed: {research_err}")

    report_body = combined + "\n\n" + summary
    if skipped_checklists:
        report_body += "\n\n⚠️ Часть чек-листов не обработана: " + ", ".join(skipped_checklists)

    # uuid-суффикс в имени защищает от коллизий при параллельных/повторных анализах
    report_path = temp_dir / f"analysis_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.txt"
    report_path.write_text(report_body, encoding="utf-8")

    if progress_conversation_id and progress_conversation_id != conversation_id:
        msg_progress_done = Message(conversation_id=display_conv_id, user_id=None, role="bot",
                                    text="✅ Анализ завершён! Результаты сохранены в аккаунт участника команды.")
        db.add(msg_progress_done); db.commit()

    msg_done = Message(conversation_id=conversation_id, user_id=None, role="bot",
                       text="Готово ✅ Отчёт во вложении.")
    db.add(msg_done); db.flush()
    key_rep = os.path.relpath(report_path, start=UPLOAD_DIR)
    _attach_file(db, msg_done.id, report_path.name, "text/plain", key_rep, report_path.stat().st_size)
    db.commit()

    analysis_text_full = combined + "\n\n" + summary

    # Win Probability (best-effort)
    try:
        from services.win_probability_service import (
            save_checklist_scores, calculate_win_probability, generate_probability_report
        )
        if checklist_scores:
            save_checklist_scores(conversation_id, checklist_scores, db)
            win_prob = calculate_win_probability(conversation_id, db)
            if win_prob:
                prob_report_path = generate_probability_report(win_prob, temp_dir, db)
                key_prob = os.path.relpath(prob_report_path, start=UPLOAD_DIR)
                _attach_file(db, msg_done.id, prob_report_path.name, "text/plain", key_prob, prob_report_path.stat().st_size)
                db.commit()
    except Exception as e:
        logger.error(f"Ошибка расчёта Win Probability: {e}", exc_info=True)

    # Извлечение ошибок и коррекций из анализа (best-effort)
    try:
        from services.analytics_service import AnalyticsService
        from models import TeamMember
        team_id = None
        team_member = db.query(TeamMember).filter(TeamMember.user_id == user_id).first()
        if team_member:
            team_id = team_member.team_id
        AnalyticsService.extract_errors_from_analysis(
            db, user_id, conversation_id, msg_done.id, analysis_text_full, team_id
        )
    except Exception as e:
        logger.error(f"Ошибка извлечения ошибок из анализа: {e}", exc_info=True)

    # Извлечение структурированных параметров по справочнику (best-effort)
    try:
        from services.parameter_extraction import extract_parameters
        await extract_parameters(conversation_id, dialogue_json_str, db, research=research)
    except Exception as e:
        logger.error(f"Ошибка извлечения параметров: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Паспорт продавца (best-effort)
    try:
        from services.seller_passport_service import update_seller_passport
        await update_seller_passport(db, user_id, conversation_id, dialogue_json_str, analysis_text_full, research=research)
    except Exception as e:
        logger.error(f"Ошибка обновления паспорта продавца: {e}", exc_info=True)

    # Сбор успешных/неуспешных действий менеджера (best-effort)
    try:
        from services.manager_actions_service import process_manager_actions
        await process_manager_actions(db, user_id, conversation_id, dialogue_json_str, analysis_text_full, research=research)
    except Exception as e:
        logger.error(f"Ошибка сбора действий менеджера: {e}", exc_info=True)

    return report_refused


# Сколько раз перезапускать ВЕСЬ анализ при отказе модели (опция 2).
ANALYSIS_MAX_ATTEMPTS = int(os.getenv("ANALYSIS_MAX_ATTEMPTS", "2"))
_RERUN_PROGRESS_TEXT = (
    "⏳ ИИ обрабатывает запись чуть дольше обычного — перезапускаю анализ, "
    "подождите ещё немного…"
)


def _post_progress(db: Session, conv_id: int, text: str):
    """Пишет сервисное сообщение прогресса в диалог (для прогресс-бара в чате)."""
    try:
        db.add(Message(conversation_id=conv_id, user_id=None, role="bot", text=text))
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"_post_progress failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass


async def _analyze_with_rerun(
    db: Session,
    user_id: int,
    conversation_id: int,
    display_conv_id: int,
    progress_conversation_id: Optional[int],
    temp_dir: Path,
    dialogue_json_str: str,
    research: Optional[ResearchLogger] = None,
) -> bool:
    """Анализ (чек-листы + отчёт) с авто-перезапуском при отказе модели (опция 2).

    Если модель отказала (на чек-листе или итоговом отчёте) и остались попытки —
    весь анализ перезапускается, а в диалог пишется сообщение прогресса, которое
    видит пользователь («подождите ещё немного…»). На последней попытке отчёт
    формируется как есть (best-effort), чтобы пользователь не остался без отчёта.
    """
    for attempt in range(1, ANALYSIS_MAX_ATTEMPTS + 1):
        is_last = attempt == ANALYSIS_MAX_ATTEMPTS
        combined, checklist_scores, skipped_checklists, refused_titles = await _run_all_checklists(
            db, user_id, dialogue_json_str, research=research
        )
        if refused_titles and not is_last:
            logger.warning(f"Отказ модели на чек-листах {refused_titles} — перезапуск анализа ({attempt}/{ANALYSIS_MAX_ATTEMPTS}).")
            _post_progress(db, display_conv_id, _RERUN_PROGRESS_TEXT)
            continue

        report_refused = await _finalize_analysis_and_report(
            db, user_id, conversation_id, display_conv_id, progress_conversation_id,
            temp_dir, combined, checklist_scores, dialogue_json_str, skipped_checklists,
            research=research, write_anyway=is_last,
        )
        if report_refused and not is_last:
            logger.warning(f"Отказ модели на итоговом отчёте — перезапуск анализа ({attempt}/{ANALYSIS_MAX_ATTEMPTS}).")
            _post_progress(db, display_conv_id, _RERUN_PROGRESS_TEXT)
            continue
        return True
    return True


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

    # Research Mode: лог промежуточных размышлений для админки
    research: Optional[ResearchLogger] = None
    try:
        research = ResearchLogger(conversation_id=conversation_id, user_id=user_id)
        research.write_header(source=f"attachment_id={text_attachment_id}", source_kind="text-file")
    except Exception as research_err:  # noqa: BLE001
        logger.warning(f"ResearchLogger init failed: {research_err}")
        research = None
    
    pipeline_ok = False
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

        # Маскируем персональные данные
        text = redact_pii(text)
        dialogue = redact_pii_in_dialogue(dialogue)

        # Сохраняем материалы
        tr_txt = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.txt"
        dialogue_path = temp_dir / f"dialogue_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.json"

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

        dialogue_json_str = dialogue_path.read_text(encoding="utf-8")
        await _analyze_with_rerun(
            db, user_id, conversation_id, display_conv_id, progress_conversation_id,
            temp_dir, dialogue_json_str, research=research,
        )

        tracker.complete_operation(operation_id, "Готово ✅ Отчёт во вложении.")
        pipeline_ok = True

    finally:
        if research is not None:
            try:
                research.finalize(status="completed" if pipeline_ok else "failed")
            except Exception as research_err:  # noqa: BLE001
                logger.warning(f"ResearchLogger finalize failed: {research_err}")
        db.close()
    return pipeline_ok


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

    # Research Mode: лог промежуточных размышлений для админки
    research: Optional[ResearchLogger] = None
    try:
        research = ResearchLogger(conversation_id=conversation_id, user_id=user_id)
        research.write_header(source="raw_text", source_kind="text-paste")
    except Exception as research_err:  # noqa: BLE001
        logger.warning(f"ResearchLogger init failed: {research_err}")
        research = None
    
    pipeline_ok = False
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

        # Маскируем персональные данные
        text = redact_pii(text)
        dialogue = redact_pii_in_dialogue(dialogue)

        # Сохраняем материалы
        temp_dir = Path(UPLOAD_DIR) / str(user_id) / str(conversation_id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        tr_txt = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.txt"
        dialogue_path = temp_dir / f"dialogue_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.json"

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

        dialogue_json_str = dialogue_path.read_text(encoding="utf-8")
        await _analyze_with_rerun(
            db, user_id, conversation_id, display_conv_id, progress_conversation_id,
            temp_dir, dialogue_json_str, research=research,
        )

        tracker.complete_operation(operation_id, "Готово ✅ Отчёт во вложении.")
        pipeline_ok = True

    finally:
        if research is not None:
            try:
                research.finalize(status="completed" if pipeline_ok else "failed")
            except Exception as research_err:  # noqa: BLE001
                logger.warning(f"ResearchLogger finalize failed: {research_err}")
        db.close()
    return pipeline_ok


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

    # Research Mode: лог промежуточных размышлений для админки
    research: Optional[ResearchLogger] = None
    try:
        research = ResearchLogger(conversation_id=conversation_id, user_id=user_id)
        research.write_header(source=f"attachment_id={audio_attachment_id}", source_kind="audio")
    except Exception as research_err:  # noqa: BLE001
        logger.warning(f"ResearchLogger init failed: {research_err}")
        research = None
    
    pipeline_ok = False
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

        # Маскируем персональные данные
        text = redact_pii(text)
        dialogue = redact_pii_in_dialogue(dialogue)

        # A: для диаризованных записей определяем роли менеджер/клиент,
        # проставляем их в dialogue.role_map и делаем читаемый транскрипт с подписями.
        try:
            speakers_in_turns = {t.get("speaker") for t in dialogue.get("turns", [])}
            if len(speakers_in_turns) >= 2:
                speaker_roles = await _resolve_roles(json.dumps(dialogue, ensure_ascii=False))
                labeled = _apply_roles(dialogue, speaker_roles)
                if speaker_roles and labeled.strip():
                    text = labeled  # транскрипт с подписями «Менеджер:/Клиент:»
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Определение ролей пропущено: {e}")

        # Сохраняем материалы транскрибации
        tr_txt = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.txt"
        tr_json = temp_dir / f"transcript_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.json"
        dialogue_path = temp_dir / f"dialogue_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.json"

        tr_txt.write_text(text, encoding="utf-8")
        tr_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        dialogue_path.write_text(json.dumps(dialogue, ensure_ascii=False, indent=2), encoding="utf-8")

        # Промежуточный WAV больше не нужен (транскрипт получен) — освобождаем диск
        try:
            wav_path.unlink(missing_ok=True)
        except Exception as cleanup_err:  # noqa: BLE001
            logger.warning(f"Не удалось удалить временный WAV {wav_path}: {cleanup_err}")

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

        dialogue_json_str = dialogue_path.read_text(encoding="utf-8")
        await _analyze_with_rerun(
            db, user_id, conversation_id, display_conv_id, progress_conversation_id,
            temp_dir, dialogue_json_str, research=research,
        )

        tracker.complete_operation(operation_id, "Готово ✅ Отчёт во вложении.")
        pipeline_ok = True

    finally:
        if research is not None:
            try:
                research.finalize(status="completed" if pipeline_ok else "failed")
            except Exception as research_err:  # noqa: BLE001
                logger.warning(f"ResearchLogger finalize failed: {research_err}")
        db.close()
    return pipeline_ok
