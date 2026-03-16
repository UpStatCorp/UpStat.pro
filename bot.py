import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import FSInputFile
from dotenv import load_dotenv

from openai import OpenAI

# -------------------- Конфигурация --------------------
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

RULES_JSON_PATH = Path("rules.json")          # обязательный чек-лист
METHOD_BRIEF_PATH = Path("method_brief.txt")  # опционально: 2–4 абзаца выжимки методики

AUDIO_DIR = Path("audio_cache")
AUDIO_DIR.mkdir(exist_ok=True)

# Модели
TRANSCRIBE_MODEL = "whisper-1"    # STT
SUMMARY_MODEL    = "gpt-4o-mini"  # краткие саммари
EVAL_MODEL       = "gpt-4o"       # оценка + объяснение

client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------- Утилиты --------------------
def tg_chunks(text: str, max_len: int = 3800) -> List[str]:
    parts, cur = [], text
    while len(cur) > max_len:
        cut = cur.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(cur[:cut])
        cur = cur[cut:]
    if cur.strip():
        parts.append(cur)
    return parts

def run_ffmpeg_to_wav(input_path: Path, out_path: Path, sample_rate: int = 16000) -> None:
    """Конвертирует аудио в WAV 16k mono через ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-ac", "1",
        "-ar", str(sample_rate),
        str(out_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def load_rules() -> List[Dict[str, Any]]:
    if not RULES_JSON_PATH.exists():
        raise FileNotFoundError("Не найден `rules.json` рядом с bot.py")
    rules = json.loads(RULES_JSON_PATH.read_text(encoding="utf-8"))
    if not isinstance(rules, list) or not rules:
        raise ValueError("`rules.json` должен быть непустым JSON-массивом")
    for r in rules:
        if not all(k in r for k in ("id", "name", "criterion", "weight")):
            raise ValueError("Каждое правило должно иметь поля: id, name, criterion, weight")
        if not isinstance(r["weight"], (int, float)):
            raise ValueError("weight должен быть числом")
    return rules

def load_method_brief() -> Optional[str]:
    if METHOD_BRIEF_PATH.exists():
        txt = METHOD_BRIEF_PATH.read_text(encoding="utf-8").strip()
        return txt if txt else None
    return None

def strip_code_fences(s: str) -> str:
    """Удаляет ```json ... ``` оболочку, если модель так вернула."""
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()

# -------------------- OpenAI шаги --------------------
async def transcribe_audio(wav_path: Path) -> str:
    with open(wav_path, "rb") as f:
        tr = client.audio.transcriptions.create(
            model=TRANSCRIBE_MODEL,
            file=f,
            response_format="text"
        )
    if isinstance(tr, str):
        return tr.strip()
    return getattr(tr, "text", "").strip()

async def summarize_dialog(transcript: str) -> str:
    prompt = f"""
Ты — аналитик продаж. Суммаризируй диалог менеджера с клиентом по структуре:
1) Цель клиента (кратко)
2) Действия менеджера (по шагам)
3) Ключевые фразы менеджера (если есть)
4) Ошибки/упущения менеджера (если есть)
5) Итог разговора (статус)

Коротко, по сути, без фантазий сверх транскрипта.

ТРАНСКРИПТ:
{transcript}
"""
    resp = client.chat.completions.create(
        model=SUMMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()

async def evaluate_against_rules(dialog_summary: str, rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Просим модель вернуть строго JSON по каждому правилу:
    - met: true/false
    - confidence: 0..1
    - evidence: короткая выдержка/пересказ из summary
    - comment: короткий комментарий тренера
    """
    rules_json = json.dumps(rules, ensure_ascii=False, indent=2)
    prompt = f"""
Ты — QA-оценщик звонков. Дано:
- SUMMARIZED_DIALOG (ниже)
- RULES (JSON) — чек-лист правил с весами.

Задача:
Для КАЖДОГО правила из RULES верни объект:
  {{
    "id": <id из RULES>,
    "met": true|false,
    "confidence": <число от 0 до 1>,
    "evidence": "<короткое объяснение на основе summary>",
    "comment": "<1–2 предложения совета/оценки по этому пункту>"
  }}

Важные условия:
- Опираться ТОЛЬКО на SUMMARIZED_DIALOG, не придумывать факты.
- Если в summary нет данных, ставь "met": false и "confidence": 0.3–0.5, объясни в evidence что данных недостаточно.
- Ответ верни СТРОГО В ФОРМАТЕ JSON:
{{
  "rules": [ ...по всем правилам... ]
}}

=== SUMMARIZED_DIALOG ===
{dialog_summary}

=== RULES (JSON) ===
{rules_json}
"""

    resp = client.chat.completions.create(
        model=EVAL_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    raw = strip_code_fences(resp.choices[0].message.content)
    try:
        data = json.loads(raw)
    except Exception:
        # fallback: попросим преобразовать к строгому JSON
        fix_prompt = f"Преобразуй ответ ниже в СТРОГИЙ JSON формата {{\"rules\":[...]}} без комментариев и без кодовых блоков:\n\n{raw}"
        resp2 = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[{"role": "user", "content": fix_prompt}],
            temperature=0,
        )
        raw2 = strip_code_fences(resp2.choices[0].message.content)
        data = json.loads(raw2)
    return data

def compute_scores(eval_json: Dict[str, Any], rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Считаем total_score из локальных весов, не доверяя модели подсчётам."""
    weight_map = {r["id"]: float(r["weight"]) for r in rules}
    max_score = sum(weight_map.values())
    total = 0.0
    detailed = []
    for item in eval_json.get("rules", []):
        rid = item.get("id")
        met = bool(item.get("met"))
        w = weight_map.get(rid, 0.0)
        if met:
            total += w
        detailed.append({
            "id": rid,
            "weight": w,
            "met": met,
            "confidence": float(item.get("confidence", 0.0)),
            "evidence": item.get("evidence", ""),
            "comment": item.get("comment", "")
        })
    normalized = 10.0 * (total / max_score) if max_score > 0 else 0.0
    return {
        "rules": detailed,
        "total_score": total,
        "max_score": max_score,
        "normalized_score_10": round(normalized, 1)
    }

async def build_coach_report(detailed_json: Dict[str, Any],
                             rules: List[Dict[str, Any]],
                             dialog_summary: str,
                             method_brief: Optional[str]) -> str:
    """
    Генерируем человеческий отчёт (плюсы, минусы, рекомендации) на основе:
    - подробного JSON результата,
    - краткого summary диалога,
    - (опц.) краткой выжимки методики.
    """
    payload = json.dumps(detailed_json, ensure_ascii=False, indent=2)
    rules_titles = "\n".join([f"- ({r['id']}) {r['name']}" for r in rules])

    brief = method_brief or (
        "Методика: при сомнениях клиента — нейтрально подтверждать факт сомнения, "
        "переводить внимание на плюсы вопросом «Что понравилось?», не перебивать, "
        "давать клиенту высказаться, мягко побуждать продолжить, оформлять сделку сразу при готовности, "
        "оставшиеся «против» обрабатывать по одному."
    )

    prompt = f"""
Ты — тренер по продажам. Сгенерируй чёткий отчёт для менеджера по результатам оценки:
- Сначала краткий итог (оценка X/10).
- Затем «Плюсы» (по пунктам, с привязкой к правилам).
- Затем «Зоны роста/Ошибки» (по пунктам, с привязкой к правилам).
- Затем «Рекомендации на практику» (3–5 конкретных действий/фраз).
Стиль: коротко, по делу.

Исходные данные:
=== EVAL_JSON ===
{payload}

=== LIST OF RULE TITLES ===
{rules_titles}

=== SUMMARIZED DIALOG ===
{dialog_summary}

=== METHOD BRIEF ===
{brief}
"""
    resp = client.chat.completions.create(
        model=EVAL_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()

# -------------------- Telegram Bot --------------------
# Оставляем Markdown (вариант А). DeprecationWarning можно игнорировать.
bot = Bot(token=TELEGRAM_BOT_TOKEN, parse_mode="Markdown")
dp = Dispatcher()

@dp.message(CommandStart())
async def on_start(message: types.Message):
    await message.answer(
        "Привет! Пришли голосовое или аудиофайл с диалогом менеджера и клиента.\n"
        "Бот транскрибирует, сверит с чек‑листом из `rules.json` и пришлёт оценку (1–10), плюсы, минусы и рекомендации.\n\n"
        "Опционально можно добавить `method_brief.txt` — короткую выжимку методики, чтобы рекомендации звучали «по школе».\n"
        "Команда: /which_guide — показать, какие файлы методики видит бот."
    )

@dp.message(Command("which_guide"))
async def which_guide(message: types.Message):
    exists_rules = RULES_JSON_PATH.exists()
    exists_brief = METHOD_BRIEF_PATH.exists()
    text = [
        f"`rules.json`: {'✅ найден' if exists_rules else '❌ нет'}",
        f"`method_brief.txt`: {'✅ найден' if exists_brief else '— (не обязательно)'}",
    ]
    # это обычный текст с backticks — Markdown норм
    await message.answer("\n".join(text))

@dp.message(F.voice | F.audio | F.document)
async def handle_audio(message: types.Message):
    await message.chat.do("typing")

    # Проверим наличие правил
    try:
        rules = load_rules()
    except Exception as e:
        await message.answer(f"Ошибка: `{e}`")
        return
    method_brief = load_method_brief()

    # Получим файл из Telegram
    try:
        if message.voice:
            tg_file = await bot.get_file(message.voice.file_id)
            file_name = f"voice_{message.voice.file_unique_id}.ogg"
        elif message.audio:
            tg_file = await bot.get_file(message.audio.file_id)
            file_name = message.audio.file_name or f"audio_{message.audio.file_unique_id}.mp3"
        elif message.document:
            tg_file = await bot.get_file(message.document.file_id)
            file_name = message.document.file_name or f"doc_{message.document.file_unique_id}"
        else:
            await message.answer("Отправьте голосовое/аудио/документ с аудио.")
            return
    except Exception as e:
        await message.answer(f"Не удалось получить файл: `{e}`")
        return

    local_in = AUDIO_DIR / file_name
    try:
        await bot.download_file(tg_file.file_path, destination=local_in)
    except Exception as e:
        await message.answer(f"Ошибка скачивания: `{e}`")
        return

    # Конвертация в WAV 16k mono
    local_wav = AUDIO_DIR / (local_in.stem + ".wav")
    try:
        run_ffmpeg_to_wav(local_in, local_wav, sample_rate=16000)
    except Exception:
        await message.answer("Ошибка конвертации. Проверь, что ffmpeg установлен.")
        return

    # Транскрибация
    await message.answer("Транскрибирую аудио…")
    try:
        transcript = await transcribe_audio(local_wav)
    except Exception as e:
        await message.answer(f"Ошибка транскрибации: `{e}`")
        return

    if len(transcript.strip()) < 10:
        await message.answer("Похоже, аудио пустое или неразборчивое. Попробуйте другой файл.")
        return

    # Краткое summary диалога
    await message.answer("Готовлю краткий отчёт по диалогу…")
    try:
        dialog_summary = await summarize_dialog(transcript)
    except Exception as e:
        await message.answer(f"Ошибка суммаризации: `{e}`")
        return

    # Оценка по правилам (JSON)
    await message.answer("Сопоставляю с чек‑листом…")
    try:
        eval_json = await evaluate_against_rules(dialog_summary, rules)
    except Exception as e:
        await message.answer(f"Ошибка оценки (JSON): `{e}`")
        return

    # Локальный подсчёт баллов
    scoring = compute_scores(eval_json, rules)
    score = scoring["normalized_score_10"]
    total = scoring["total_score"]
    max_score = scoring["max_score"]

    # Человеческий отчёт
    try:
        coach_report = await build_coach_report(scoring, rules, dialog_summary, method_brief)
    except Exception as e:
        coach_report = (
            f"*Оценка:* {score}/10 (очки: {total}/{max_score})\n\n"
            f"Не удалось сгенерировать подробный отчёт: `{e}`.\n"
            f"JSON по правилам отправляю файлом."
        )

    # Заголовок БЕЗ подчёркиваний (Markdown-safe)
    header = f"*Итоговая оценка:* {score}/10  (баллы: {total}/{max_score})\n"

    # Отправляем header как Markdown, а текст отчёта — без разметки (чтобы Markdown не ломался)
    await message.answer(header)
    for chunk in tg_chunks(coach_report):
        await message.answer(chunk, parse_mode=None)

    # Прикрепим JSON и транскрипт файлами
    json_path = AUDIO_DIR / f"{local_in.stem}_evaluation.json"
    json_path.write_text(json.dumps(scoring, ensure_ascii=False, indent=2), encoding="utf-8")
    await message.answer_document(FSInputFile(json_path, filename=json_path.name))

    if len(transcript) > 1200:
        tr_path = AUDIO_DIR / f"{local_in.stem}_transcript.txt"
        tr_path.write_text(transcript, encoding="utf-8")
        await message.answer_document(FSInputFile(tr_path, filename=tr_path.name))

    # Очистка временных аудио
    try:
        local_in.unlink(missing_ok=True)
        local_wav.unlink(missing_ok=True)
    except Exception:
        pass

# -------------------- Запуск --------------------
def main():
    if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
        raise RuntimeError("Заполните TELEGRAM_BOT_TOKEN и OPENAI_API_KEY в .env")
    asyncio.run(dp.start_polling(bot))

if __name__ == "__main__":
    main()
