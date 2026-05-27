"""
Research Mode — детальный CoT-лог анализа звонков.

Логирует все промежуточные размышления ИИ при анализе звонка:
- какие промпты отправлялись;
- какие raw-ответы вернул GPT;
- какие финальные решения были приняты;
- сколько токенов использовано на каждый этап.

Файл с логом сохраняется в uploads/research/<conversation_id>/research_<timestamp>.txt
и НЕ привязывается к attachments — доступен только админу через /admin/research.

Любой сбой логгера НЕ должен ронять основной pipeline (try/except везде).
"""

from __future__ import annotations

import json
import logging
import os
import re
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database import SessionLocal
from models import ResearchLog, User

logger = logging.getLogger("main")

UPLOAD_DIR = os.path.abspath("uploads")
RESEARCH_SUBDIR = "research"

_SEPARATOR = "═" * 80
_HARD_SEP = "=" * 80

_REASONING_HEADER = "=== REASONING ==="
_REASONING_END_MARKERS = (
    "=== ROLE MAPPING ===",
    "=== РОЛИ ===",
    "=== РОЛЕВАЯ КАРТА ===",
    "=== СТАТУС ===",
    "=== ИТОГОВЫЙ ОТЧЁТ ===",
    "=== ИТОГ ===",
    "=== SUMMARY ===",
    "=== ОТВЕТ ===",
    "=== RESULT ===",
    "=== JSON ===",
    "```json",
)

REASONING_INSTRUCTION_CHECKLIST = (
    "\n\n══════════════════════════════════════════════════════════════════════\n"
    "ШАГ 1.5 (обязателен — РЕЖИМ ИССЛЕДОВАНИЯ): САМОЕ ВАЖНОЕ МЕСТО ОТВЕТА\n"
    "══════════════════════════════════════════════════════════════════════\n"
    "Перед основным ответом запиши МАКСИМАЛЬНО РАЗВЁРНУТЫЕ рассуждения. "
    "Это раздел \"=== REASONING ===\" — он идёт ПЕРВЫМ, ДО \"=== ROLE MAPPING ===\".\n\n"
    "Этот раздел читает опытный коуч продаж, который будет калибровать "
    "промпт. ЕМУ НУЖНА ЛОГИКА ТВОИХ РЕШЕНИЙ, А НЕ КРАТКИЕ ОТМЕТКИ.\n\n"
    "ДЛЯ КАЖДОГО ПУНКТА ЧЕК-ЛИСТА напиши МИНИМУМ 8–12 содержательных "
    "предложений по такой схеме:\n\n"
    "━━━ Пункт N (точная формулировка критерия) ━━━\n\n"
    "[1] ЧТО ИМЕННО ТЫ УВИДЕЛ (минимум 3–5 предложений):\n"
    "  • Какие конкретно реплики менеджера ты рассматривал для ЭТОГО критерия.\n"
    "  • Приведи 2–4 ТОЧНЫЕ ЦИТАТЫ менеджера с таймкодами, без пересказа.\n"
    "  • Какие реакции/сигналы клиента ты учёл (молчание, переход темы,\n"
    "    тон, перебивание, длинные паузы).\n"
    "  • Какой общий контекст диалога влиял на интерпретацию\n"
    "    (например: клиент устал, занят, играл на фоне, дозвон холодный).\n\n"
    "[2] КАК ТЫ ТРАКТОВАЛ УВИДЕННОЕ (2–3 предложения):\n"
    "  • Что эти реплики означают именно ПО ЭТОМУ критерию.\n"
    "  • Какие неоднозначности были в трактовке и почему ты их так разрешил.\n\n"
    "[3] АЛЬТЕРНАТИВНЫЕ ОЦЕНКИ — рассмотри все три варианта:\n"
    "  • Почему НЕ \"Да\": какие конкретные аргументы против.\n"
    "  • Почему НЕ \"Нет\": какие конкретные аргументы против.\n"
    "  • Почему НЕ \"Частично\" (если в итоге выбрал Да или Нет).\n\n"
    "[4] ФИНАЛЬНОЕ РЕШЕНИЕ И ОБОСНОВАНИЕ (2–3 предложения):\n"
    "  • Какой статус выбрал и почему именно его, а не альтернативы.\n"
    "  • Что было РЕШАЮЩИМ аргументом — конкретно.\n\n"
    "[5] CONFIDENCE — ОБЯЗАТЕЛЬНО РАЗВЁРНУТО (2–3 предложения):\n"
    "  • Какие конкретные факторы ПОВЫШАЛИ уверенность.\n"
    "  • Какие конкретные факторы СНИЖАЛИ уверенность.\n"
    "  • Почему именно X.X, а не X.X−0.1 и не X.X+0.1.\n\n"
    "ЖЁСТКИЕ ПРАВИЛА:\n"
    "• НЕ экономь слова. Лучше 12 содержательных предложений с цитатами,\n"
    "  чем 2 общих фразы.\n"
    "• НЕ повторяй формулировку критерия — пиши по сути.\n"
    "• Если данных мало — ПОДРОБНО объясни, ЧТО ИМЕННО ТЫ ИСКАЛ\n"
    "  и почему НЕ НАШЁЛ (это критически важная информация).\n"
    "• Если сомневаешься между двумя оценками 50/50 — обязательно опиши\n"
    "  этот пограничный случай и какой сигнал склонил весы.\n"
    "• Цитируй РЕАЛЬНЫЕ фразы менеджера с таймкодами — никаких пересказов.\n"
    "• Каждый блок [1]–[5] — отдельный абзац с подзаголовком в квадратных скобках.\n"
    "После раздела === REASONING === продолжай с === ROLE MAPPING === как обычно.\n"
)

REASONING_INSTRUCTION_SUMMARY = (
    "\n\n══════════════════════════════════════════════════════════════════════\n"
    "РЕЖИМ ИССЛЕДОВАНИЯ — РАСШИРЕННОЕ ОБОСНОВАНИЕ ИТОГОВОГО ОТЧЁТА\n"
    "══════════════════════════════════════════════════════════════════════\n"
    "В САМОМ НАЧАЛЕ ответа добавь раздел \"=== REASONING ===\", в котором "
    "ПОДРОБНО (минимум 25–40 предложений в сумме) разбери свою логику.\n\n"
    "[1] КАК ТЫ ВЗВЕШИВАЛ ЧЕК-ЛИСТЫ МЕЖДУ СОБОЙ (5–7 предложений):\n"
    "  • Какие чек-листы дали больше всего сигналов \"провал\".\n"
    "  • Какие чек-листы дали больше всего сигналов \"работает\".\n"
    "  • Где у разных чек-листов противоречие — и как ты его разрешил.\n"
    "  • Какой этап продаж (контакт/потребности/презентация/возражения/\n"
    "    закрытие) ты считаешь самым проблемным и почему.\n\n"
    "[2] ВЫБОР СИЛЬНЫХ СТОРОН (по 2–3 предложения на каждую):\n"
    "  • Для каждой сильной стороны: почему именно она попала в топ-3–6,\n"
    "    а не другие потенциальные кандидаты.\n"
    "  • Какие конкретные реплики или паттерны поведения это подтверждают.\n"
    "  • Какие альтернативные кандидаты ты отверг и почему.\n\n"
    "[3] ВЫБОР ЗОН РОСТА (по 2–3 предложения на каждую):\n"
    "  • Для каждой зоны роста: почему она приоритетна, а не другие.\n"
    "  • Какой импакт даст её исправление на конверсию.\n"
    "  • Какие конкретные провалы в чек-листах её подтверждают.\n\n"
    "[4] КОМПРОМИССЫ И СОМНЕНИЯ (3–5 предложений):\n"
    "  • Какие моменты в диалоге ты НЕ смог однозначно интерпретировать.\n"
    "  • Какую информацию тебе не хватило для уверенного вывода.\n"
    "  • Где ты осторожничал и где брал на себя смелость интерпретации.\n\n"
    "[5] КЛЮЧЕВЫЕ ЦИТАТЫ-ДОКАЗАТЕЛЬСТВА (3–5 цитат с таймкодами):\n"
    "  • Самые показательные фразы менеджера — позитивные и негативные.\n"
    "  • С коротким комментарием, почему именно эта цитата важна.\n\n"
    "Пиши развёрнуто, как объясняешь свою логику коучу. Не экономь слова.\n"
    "После === REASONING === продолжай со стандартного итогового отчёта.\n"
)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_usage(usage: Any) -> Dict[str, int]:
    """Извлекает токены из объекта usage OpenAI SDK (или dict)."""
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    if prompt is None and isinstance(usage, dict):
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        total = usage.get("total_tokens")
    return {
        "prompt_tokens": _safe_int(prompt),
        "completion_tokens": _safe_int(completion),
        "total_tokens": _safe_int(total) or (_safe_int(prompt) + _safe_int(completion)),
    }


def _find_reasoning_bounds(text: str) -> Optional[tuple]:
    """Возвращает (start_of_header, end_of_header, end_of_block) или None."""
    if not text:
        return None
    patterns = [
        r"={2,}\s*REASONING\s*={2,}",
        r"={2,}\s*РАЗМЫШЛЕНИЯ\s*={2,}",
        r"={2,}\s*РАССУЖДЕНИЯ\s*={2,}",
    ]
    start_match = None
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            start_match = m
            break
    if not start_match:
        return None
    end = len(text)
    for marker in _REASONING_END_MARKERS:
        idx = text.find(marker, start_match.end())
        if idx != -1 and idx < end:
            end = idx
    return (start_match.start(), start_match.end(), end)


def extract_reasoning_block(text: str) -> Optional[str]:
    """
    Вырезает блок между маркером "=== REASONING ===" и следующим разделом.
    Возвращает None, если маркер не найден.
    """
    bounds = _find_reasoning_bounds(text)
    if not bounds:
        return None
    _, header_end, end = bounds
    block = text[header_end:end].strip()
    return block or None


def _strip_reasoning_from_response(text: str) -> str:
    """
    Возвращает ответ модели БЕЗ блока === REASONING === ... (он уже
    отображён отдельно в секции [REASONING]). Это убирает дублирование.
    Если маркер reasoning не найден — возвращает текст как есть.
    """
    bounds = _find_reasoning_bounds(text)
    if not bounds:
        return text
    header_start, _, end = bounds
    before = text[:header_start].rstrip()
    after = text[end:].lstrip()
    if before and after:
        return before + "\n\n" + after
    return after or before


def _summarize_decisions(parsed: Any) -> str:
    """
    Короткое summary для оглавления / TL;DR.
    Понимает несколько форматов: list of scores (checklist), dict с stage_scores
    (passport), dict с actions (manager_actions), dict с параметрами (extraction).
    """
    if parsed is None:
        return ""
    try:
        # Список scores (чек-лист)
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "passed" in parsed[0]:
            total = len(parsed)
            passed = sum(1 for s in parsed if s.get("passed"))
            failed = total - passed
            avg_conf = sum(float(s.get("confidence", 0)) for s in parsed) / total if total else 0
            return f"{passed}/{total} passed, {failed} failed, avg conf={avg_conf:.2f}"
        if isinstance(parsed, dict):
            # Паспорт продавца
            if "stage_scores" in parsed and isinstance(parsed["stage_scores"], dict):
                ss = parsed["stage_scores"]
                overall = parsed.get("overall_score")
                stages = "/".join(f"{k}:{int(v)}" for k, v in ss.items())
                overall_str = f" (overall {int(overall)})" if overall is not None else ""
                return f"{stages}{overall_str}"
            # Действия менеджера
            if "valid_actions" in parsed:
                actions = parsed.get("valid_actions") or []
                pos = sum(1 for a in actions if a.get("outcome") == "positive")
                neg = sum(1 for a in actions if a.get("outcome") == "negative")
                return f"{len(actions)} действий ({pos} positive, {neg} negative)"
            # Параметры — много ключей
            if len(parsed) > 3:
                with_value = sum(
                    1 for v in parsed.values()
                    if isinstance(v, dict) and v.get("value") is not None
                )
                return f"{len(parsed)} параметров ({with_value} с значением)"
        return ""
    except Exception:  # noqa: BLE001
        return ""


def parse_research_file(content: str) -> Dict[str, Any]:
    """
    Парсит сохранённый research-файл обратно в структуру для HTML-вьюера.

    Возвращает словарь:
    {
      "header": {start_iso, end_iso, duration_sec, user, stages, models, tokens_in, tokens_out},
      "tldr": ["строка1", ...],
      "toc": [{idx, name, model, tokens, summary, has_reasoning}, ...],
      "stages": [
        {
          idx, name, model, tokens_in, tokens_out, tokens_total,
          decisions_summary,
          reasoning,        # str без префиксов │
          rest_of_response, # str без префиксов │
          parsed_decisions, # str (JSON) без префиксов │
          extra,            # str (JSON) или None
          prompt,           # str
        },
        ...
      ]
    }
    """
    result: Dict[str, Any] = {
        "header": {},
        "tldr": [],
        "toc": [],
        "stages": [],
    }
    if not content:
        return result

    # Удаляем префиксы "│ " из блоков
    def _strip_prefix(block_text: str) -> str:
        cleaned: List[str] = []
        for ln in block_text.splitlines():
            if ln.startswith("│ "):
                cleaned.append(ln[2:])
            elif ln == "│":
                cleaned.append("")
            else:
                cleaned.append(ln)
        return "\n".join(cleaned).strip()

    # Заголовочные поля
    m = re.search(r"📅\s*Начало:\s*([\d\-: ]+)", content)
    if m:
        result["header"]["start"] = m.group(1).strip()
    m = re.search(r"⏱\s*Длит-сть:\s*([\d.]+)\s*с", content)
    if m:
        result["header"]["duration_sec"] = float(m.group(1))
    m = re.search(r"👤\s*User:\s*(\S+?)(?:\s+\(id=(\d+)\))?\s*$", content, re.MULTILINE)
    if m:
        result["header"]["user"] = m.group(1)
        if m.group(2):
            result["header"]["user_id"] = int(m.group(2))
    m = re.search(r"🎙\s*Source:\s*(.+)", content)
    if m:
        result["header"]["source"] = m.group(1).strip()
    m = re.search(r"🧩\s*Этапов:\s*(\d+)", content)
    if m:
        result["header"]["stages_count"] = int(m.group(1))
    m = re.search(r"🤖\s*Модели:\s*(.+)", content)
    if m:
        result["header"]["models"] = m.group(1).strip()
    m = re.search(r"🔢\s*Токены:\s*in=(\d+)\s+out=(\d+)\s+total=(\d+)", content)
    if m:
        result["header"]["tokens_in"] = int(m.group(1))
        result["header"]["tokens_out"] = int(m.group(2))
        result["header"]["tokens_total"] = int(m.group(3))
    m = re.search(r"📊\s*Статус:\s*(\S+)", content)
    if m:
        result["header"]["status"] = m.group(1)

    # TL;DR блок
    tldr_match = re.search(
        r"📌 TL;DR.*?\n─+\n(.*?)(?=\n\n📑|\Z)",
        content,
        re.DOTALL,
    )
    if tldr_match:
        result["tldr"] = [
            ln.strip() for ln in tldr_match.group(1).splitlines() if ln.strip()
        ]

    # Разрезаем на этапы по якорю-заголовку «══════ \n  ❯❯❯  ЭТАП N  ·  …»
    # Якорь должен начинать чанк, а не быть случайной строкой в тексте оглавления.
    stage_anchor_re = re.compile(r"\n(?=═+\n\s+❯❯❯\s+ЭТАП\s+\d+\s+·)")
    stage_chunks = stage_anchor_re.split(content)
    for chunk in stage_chunks:
        # Первый чанк — это шапка+TLDR+оглавление, в нём нет якоря в начале
        if not re.match(r"═+\n\s+❯❯❯\s+ЭТАП\s+\d+\s+·", chunk):
            continue
        stage = _parse_stage_chunk(chunk, _strip_prefix)
        if stage and stage.get("idx", 0) > 0:
            result["stages"].append(stage)

    return result


def _parse_stage_chunk(chunk: str, strip_prefix) -> Optional[Dict[str, Any]]:
    """Парсит один этап из текстового блока."""
    try:
        stage: Dict[str, Any] = {
            "idx": 0,
            "name": "",
            "model": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_total": 0,
            "decisions_summary": "",
            "reasoning": None,
            "rest_of_response": None,
            "parsed_decisions": None,
            "extra": None,
            "prompt": None,
        }

        # Шапка этапа
        m = re.search(r"❯❯❯\s+ЭТАП\s+(\d+)\s+·\s+(.+?)(?:\n|$)", chunk)
        if m:
            stage["idx"] = int(m.group(1))
            stage["name"] = m.group(2).strip()
        m = re.search(
            r"модель:\s*(\S+)\s+·\s+токены:\s+in=(\d+)\s+out=(\d+)\s+total=(\d+)",
            chunk,
        )
        if m:
            stage["model"] = m.group(1)
            stage["tokens_in"] = int(m.group(2))
            stage["tokens_out"] = int(m.group(3))
            stage["tokens_total"] = int(m.group(4))
        m = re.search(r"итог:\s+(.+?)\n", chunk)
        if m:
            stage["decisions_summary"] = m.group(1).strip()

        # Блоки между ┌─ и └─
        block_re = re.compile(
            r"┌─\s*(.+?)\s*─+\n(.*?)\n└─+",
            re.DOTALL,
        )
        for bm in block_re.finditer(chunk):
            title = bm.group(1).strip()
            body = strip_prefix(bm.group(2))
            title_lower = title.lower()
            if "reasoning" in title_lower or "🧠" in title:
                # Не записываем placeholder-сообщение об отсутствии reasoning
                if not body.lstrip().startswith("⚠️"):
                    stage["reasoning"] = body
            elif "остальной ответ" in title_lower or "📋" in title:
                stage["rest_of_response"] = body
            elif "распарсенн" in title_lower or "✅" in title:
                stage["parsed_decisions"] = body
            elif "доп" in title_lower or "ℹ" in title:
                stage["extra"] = body
            elif "промпт" in title_lower or "🔧" in title:
                stage["prompt"] = body

        return stage
    except Exception:  # noqa: BLE001
        return None


def _truncate_dialogue_in_prompt(prompt: str, max_dialogue_len: int = 1200) -> str:
    """
    Усекает большой JSON-диалог внутри промпта (для экономии места в .txt).
    Сам диалог сохраняется в pipeline в отдельный файл dialogue_*.json.
    """
    marker = "ДИАЛОГ_JSON:"
    idx = prompt.find(marker)
    if idx == -1:
        return prompt
    head = prompt[: idx + len(marker)]
    tail = prompt[idx + len(marker):]
    # Ищем конец JSON-объекта (последняя `}`, после которой идёт что-то ещё)
    # Простая эвристика: усекаем середину, если хвост длинный
    if len(tail) <= max_dialogue_len:
        return prompt
    keep_head = tail[:max_dialogue_len // 2].rstrip()
    keep_tail = tail[-max_dialogue_len // 2:].lstrip()
    truncated_marker = (
        f"\n... [JSON-диалог усечён для экономии места — "
        f"{len(tail) - max_dialogue_len} символов; "
        f"полный диалог в файле dialogue_*.json] ...\n"
    )
    return head + keep_head + truncated_marker + keep_tail


class ResearchLogger:
    """
    Накапливает текстовые секции про каждый этап AI-анализа и сохраняет в файл + БД.

    Использование:
        research = ResearchLogger(conversation_id, user_id)
        research.write_header(source="audio.mp3", source_kind="audio")
        research.capture_stage(
            stage_name="Чек-лист: Контакт",
            model="gpt-4o",
            prompt=prompt_text,
            raw_response=response_text,
            parsed_decisions=[...],
            usage=resp.usage,
        )
        research.finalize(status="completed")
    """

    def __init__(self, conversation_id: int, user_id: int):
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.started_at = datetime.utcnow()
        self.stages: List[str] = []
        self._stage_idx = 0
        self._models_counter: Counter = Counter()
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._user_email: Optional[str] = None
        self._source: Optional[str] = None
        self._source_kind: Optional[str] = None
        self._db_record_id: Optional[int] = None
        self._disabled = False
        # Метаинформация по этапам для TL;DR и оглавления
        self._stage_meta: List[Dict[str, Any]] = []
        # Заголовочные блоки для записи в шапку
        self._header_block: Optional[str] = None

        try:
            ts = self.started_at.strftime("%Y%m%d_%H%M%S")
            self.file_name = f"research_{ts}.txt"
            self.dir_path = Path(UPLOAD_DIR) / RESEARCH_SUBDIR / str(conversation_id)
            self.dir_path.mkdir(parents=True, exist_ok=True)
            self.file_path = self.dir_path / self.file_name
            self._create_db_record()
        except Exception as e:  # noqa: BLE001
            logger.error(f"ResearchLogger init failed: {e}", exc_info=True)
            self._disabled = True

    def _create_db_record(self) -> None:
        """Создаёт запись в research_logs со status=running."""
        db = SessionLocal()
        try:
            rel_path = os.path.relpath(self.file_path, start=UPLOAD_DIR)
            row = ResearchLog(
                conversation_id=self.conversation_id,
                user_id=self.user_id,
                file_path=rel_path,
                file_name=self.file_name,
                status="running",
                stages_count=0,
                file_size_bytes=0,
                total_input_tokens=0,
                total_output_tokens=0,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            self._db_record_id = row.id

            # Подтягиваем email пользователя для шапки
            user = db.query(User).filter(User.id == self.user_id).first()
            if user:
                self._user_email = user.email
        except Exception as e:  # noqa: BLE001
            logger.error(f"ResearchLogger DB create failed: {e}", exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()

    def write_header(self, source: Optional[str] = None, source_kind: Optional[str] = None) -> None:
        """Сохраняет данные для шапки. Реальная запись блока — в finalize(),
        чтобы TL;DR + оглавление могли быть собраны после всех этапов."""
        if self._disabled:
            return
        self._source = source
        self._source_kind = source_kind

    def capture_stage(
        self,
        stage_name: str,
        model: str,
        prompt: str,
        raw_response: str,
        parsed_decisions: Optional[Any] = None,
        usage: Any = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Добавляет секцию для одного AI-вызова.

        Структура секции (без дублирования):
          1. [REASONING модели] — вырезанный из ответа блок === REASONING ===
          2. [ОСТАЛЬНОЙ ОТВЕТ] — хвост после reasoning-блока
             (role mapping, оценки, итоговый JSON) — БЕЗ повторения reasoning
          3. [РАСПАРСЕННЫЕ РЕШЕНИЯ] — то, что попало в БД
          4. [ПРОМПТ] — в самом конце под свёрнутым маркером для отладки

        parsed_decisions — структурированный JSON финальных решений (scores, actions, parameters).
        usage — объект usage OpenAI (или dict с ключами prompt_tokens/completion_tokens).
        extra — произвольный dict с доп. полями (отрендерится как JSON).
        """
        if self._disabled:
            return
        try:
            self._stage_idx += 1
            self._models_counter[model] += 1

            usage_d = _format_usage(usage)
            self._total_input_tokens += usage_d["prompt_tokens"]
            self._total_output_tokens += usage_d["completion_tokens"]

            reasoning = extract_reasoning_block(raw_response or "")
            rest_of_response = _strip_reasoning_from_response(raw_response or "")

            # Считаем passed/failed для TL;DR (если parsed_decisions — список scores)
            decisions_summary = _summarize_decisions(parsed_decisions)

            # Сохраняем метаинфо для оглавления / TL;DR
            self._stage_meta.append({
                "idx": self._stage_idx,
                "name": stage_name,
                "model": model,
                "tokens_in": usage_d["prompt_tokens"],
                "tokens_out": usage_d["completion_tokens"],
                "has_reasoning": bool(reasoning),
                "decisions_summary": decisions_summary,
            })

            lines: List[str] = []
            lines.append("")
            lines.append(_SEPARATOR)
            # Якорь для быстрой навигации Cmd+F
            lines.append(f"  ❯❯❯  ЭТАП {self._stage_idx}  ·  {stage_name}")
            lines.append(f"       модель: {model}  ·  токены: in={usage_d['prompt_tokens']}  out={usage_d['completion_tokens']}  total={usage_d['total_tokens']}")
            if decisions_summary:
                lines.append(f"       итог: {decisions_summary}")
            lines.append(_SEPARATOR)

            # Главное — мысли модели
            lines.append("")
            lines.append("┌─ 🧠 REASONING МОДЕЛИ ─────────────────────────────────────────────────")
            if reasoning:
                # Префикс «│ » к каждой строке для визуального блока
                for ln in reasoning.splitlines():
                    lines.append("│ " + ln if ln else "│")
            else:
                lines.append("│ ⚠️  Маркер \"=== REASONING ===\" не найден в ответе модели.")
                lines.append("│    Возможные причины: модель проигнорировала инструкцию,")
                lines.append("│    ответ обрезан по лимиту токенов, или сбой парсинга.")
                lines.append("│    Полный ответ см. в секции [ОСТАЛЬНОЙ ОТВЕТ] ниже.")
            lines.append("└──────────────────────────────────────────────────────────────────────")

            # Хвост после reasoning (role mapping, оценки, JSON) — БЕЗ дублирования
            if rest_of_response.strip():
                lines.append("")
                lines.append("┌─ 📋 ОСТАЛЬНОЙ ОТВЕТ МОДЕЛИ (role mapping, оценки, JSON) ──────────────")
                for ln in rest_of_response.splitlines():
                    lines.append("│ " + ln if ln else "│")
                lines.append("└──────────────────────────────────────────────────────────────────────")

            # Распарсенные решения — то, что пошло в БД
            if parsed_decisions is not None:
                lines.append("")
                lines.append("┌─ ✅ РАСПАРСЕННЫЕ РЕШЕНИЯ (что попало в БД) ──────────────────────────")
                try:
                    j = json.dumps(parsed_decisions, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    j = str(parsed_decisions)
                for ln in j.splitlines():
                    lines.append("│ " + ln if ln else "│")
                lines.append("└──────────────────────────────────────────────────────────────────────")

            if extra:
                lines.append("")
                lines.append("┌─ ℹ️  ДОП. КОНТЕКСТ ───────────────────────────────────────────────────")
                try:
                    j = json.dumps(extra, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    j = str(extra)
                for ln in j.splitlines():
                    lines.append("│ " + ln if ln else "│")
                lines.append("└──────────────────────────────────────────────────────────────────────")

            # Промпт — в самом конце как объёмная техническая деталь
            lines.append("")
            lines.append("┌─ 🔧 ПРОМПТ, ОТПРАВЛЕННЫЙ В МОДЕЛЬ (для отладки) ──────────────────────")
            prompt_text = (prompt or "").strip() or "(пустой промпт)"
            # Усекаем JSON-диалог в промпте чтобы файл не раздувался —
            # сам диалог сохраняется отдельно как dialogue_*.json
            prompt_text = _truncate_dialogue_in_prompt(prompt_text)
            for ln in prompt_text.splitlines():
                lines.append("│ " + ln if ln else "│")
            lines.append("└──────────────────────────────────────────────────────────────────────")

            self.stages.append("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            logger.error(f"ResearchLogger capture_stage failed: {e}", exc_info=True)

    def capture_note(self, note: str) -> None:
        """Свободная текстовая заметка (например, об ошибке)."""
        if self._disabled:
            return
        try:
            block = "\n".join([
                "",
                "─" * 80,
                f"NOTE: {note}",
                "─" * 80,
            ])
            self.stages.append(block)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ResearchLogger capture_note failed: {e}")

    def _build_header_and_toc(self, finished_at: datetime, status: str, error: Optional[str]) -> str:
        """Собирает шапку: метаинформация → TL;DR → оглавление."""
        duration = (finished_at - self.started_at).total_seconds()
        models_summary = ", ".join(
            f"{m} ×{c}" for m, c in self._models_counter.most_common()
        ) or "—"

        # ─── Шапка ─────────────────────────────────────────────
        lines: List[str] = [
            _HARD_SEP,
            f"  RESEARCH MODE  ·  Звонок #{self.conversation_id}",
            _HARD_SEP,
            f"  📅 Начало:    {self.started_at.strftime('%Y-%m-%d %H:%M:%S')} UTC",
            f"  ⏱  Длит-сть: {duration:.1f} с",
            f"  👤 User:      {self._user_email or '?'}  (id={self.user_id})",
        ]
        if self._source or self._source_kind:
            src_line = f"  🎙 Source:    {self._source or ''}"
            if self._source_kind:
                src_line += f"  [{self._source_kind}]"
            lines.append(src_line)
        lines.append(f"  🧩 Этапов:    {self._stage_idx}")
        lines.append(f"  🤖 Модели:    {models_summary}")
        lines.append(
            f"  🔢 Токены:    in={self._total_input_tokens}  out={self._total_output_tokens}  total={self._total_input_tokens + self._total_output_tokens}"
        )
        lines.append(
            f"  📊 Статус:    {status}"
            + (f"  (ошибка: {error})" if error else "")
        )
        lines.append(_HARD_SEP)
        lines.append("")

        # ─── TL;DR ────────────────────────────────────────────
        tldr = self._build_tldr()
        if tldr:
            lines.append("📌 TL;DR — КРАТКАЯ СВОДКА")
            lines.append("─" * 80)
            lines.extend(tldr)
            lines.append("")

        # ─── Оглавление ───────────────────────────────────────
        lines.append("📑 ОГЛАВЛЕНИЕ (используй Cmd+F с поиском ❯❯❯ ЭТАП N)")
        lines.append("─" * 80)
        for meta in self._stage_meta:
            num = meta["idx"]
            name = meta["name"]
            summary = meta.get("decisions_summary") or ""
            reasoning_mark = "🧠" if meta.get("has_reasoning") else "⚠️ "
            tokens = f"{meta['tokens_in']}/{meta['tokens_out']} tok"
            line = f"  {reasoning_mark}  ЭТАП {num:>2} · {name}"
            line += f"\n        {meta['model']} · {tokens}"
            if summary:
                line += f" · {summary}"
            lines.append(line)
        lines.append("")
        lines.append("Легенда: 🧠 — модель прислала reasoning;  ⚠️  — reasoning отсутствует.")
        lines.append(_HARD_SEP)
        lines.append("")

        return "\n".join(lines)

    def _build_tldr(self) -> List[str]:
        """
        Краткая сводка для админа: где провалы, где сильные стороны,
        какие этапы вызывают сомнения (низкий confidence).
        """
        try:
            lines: List[str] = []
            # Сумма по чек-листам
            total_passed = 0
            total_items = 0
            low_conf_count = 0
            failed_critical: List[str] = []
            for meta in self._stage_meta:
                summary = meta.get("decisions_summary") or ""
                # Парсим "X/Y passed, Z failed, avg conf=W.WW"
                m = re.match(
                    r"(\d+)/(\d+)\s+passed,\s+(\d+)\s+failed,\s+avg conf=([\d.]+)",
                    summary,
                )
                if m:
                    p, t, _f, avg = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
                    total_passed += p
                    total_items += t
                    if avg < 0.7:
                        low_conf_count += 1
                        failed_critical.append(f"{meta['name']} (avg conf={avg:.2f})")
            if total_items:
                pct = total_passed * 100 // total_items
                lines.append(
                    f"  ✅ Чек-листы:  {total_passed}/{total_items} критериев пройдено ({pct}%)"
                )
            if low_conf_count:
                lines.append(
                    f"  ⚠️  Низкая уверенность (avg conf < 0.7) в {low_conf_count} чек-листах:"
                )
                for name in failed_critical[:5]:
                    lines.append(f"     · {name}")
            # Паспорт продавца
            for meta in self._stage_meta:
                if "Паспорт продавца" in meta["name"]:
                    s = meta.get("decisions_summary") or ""
                    if s:
                        lines.append(f"  🪪 Паспорт продавца: {s}")
            # Действия
            for meta in self._stage_meta:
                if "Действия менеджера" in meta["name"]:
                    s = meta.get("decisions_summary") or ""
                    if s:
                        lines.append(f"  🎯 Действия:        {s}")
            # Параметры
            for meta in self._stage_meta:
                if "Извлечение параметров" in meta["name"]:
                    s = meta.get("decisions_summary") or ""
                    if s:
                        lines.append(f"  🧬 Параметры:       {s}")
            return lines
        except Exception as e:  # noqa: BLE001
            logger.warning(f"_build_tldr failed: {e}")
            return []

    def finalize(self, status: str = "completed", error: Optional[str] = None) -> Optional[str]:
        """
        Записывает накопленный буфер на диск и обновляет research_logs.
        Возвращает абсолютный путь к файлу или None.
        """
        if self._disabled:
            return None
        try:
            finished_at = datetime.utcnow()
            duration = (finished_at - self.started_at).total_seconds()

            models_summary = ", ".join(
                f"{m} ×{c}" for m, c in self._models_counter.most_common()
            ) or "—"

            # Шапка + TL;DR + оглавление собираются ЗДЕСЬ (после всех этапов)
            header_block = self._build_header_and_toc(finished_at, status, error)

            footer = "\n".join([
                "",
                _HARD_SEP,
                f"  Конец:    {finished_at.strftime('%Y-%m-%d %H:%M:%S')} UTC",
                f"  Длит-сть: {duration:.1f} с",
                f"  Этапов:   {self._stage_idx}",
                f"  Модели:   {models_summary}",
                f"  Токены:   in={self._total_input_tokens}  out={self._total_output_tokens}  total={self._total_input_tokens + self._total_output_tokens}",
                f"  Статус:   {status}" + (f"  (ошибка: {error})" if error else ""),
                _HARD_SEP,
                "",
            ])

            full_text = header_block + "\n".join(self.stages) + footer

            self.file_path.write_text(full_text, encoding="utf-8")
            size = self.file_path.stat().st_size

            self._update_db_record(
                status=status,
                error=error,
                completed_at=finished_at,
                size=size,
            )

            return str(self.file_path)
        except Exception as e:  # noqa: BLE001
            logger.error(f"ResearchLogger finalize failed: {e}\n{traceback.format_exc()}")
            try:
                self._update_db_record(
                    status="failed",
                    error=str(e),
                    completed_at=datetime.utcnow(),
                    size=0,
                )
            except Exception:
                pass
            return None

    def _update_db_record(
        self,
        status: str,
        error: Optional[str],
        completed_at: datetime,
        size: int,
    ) -> None:
        if self._db_record_id is None:
            return
        db = SessionLocal()
        try:
            row = db.query(ResearchLog).filter(ResearchLog.id == self._db_record_id).first()
            if not row:
                return
            row.status = status
            row.error_message = (error or None) and error[:1000]
            row.completed_at = completed_at
            row.file_size_bytes = size
            row.stages_count = self._stage_idx
            row.total_input_tokens = self._total_input_tokens
            row.total_output_tokens = self._total_output_tokens
            row.models_used = json.dumps(dict(self._models_counter), ensure_ascii=False)
            db.commit()
        except Exception as e:  # noqa: BLE001
            logger.error(f"ResearchLogger DB update failed: {e}", exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()
