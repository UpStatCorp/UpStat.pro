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
    "\n\nШАГ 1.5 (обязателен — РЕЖИМ ИССЛЕДОВАНИЯ): Перед основным ответом запиши развёрнутые "
    "рассуждения. Раздел должен называться \"=== REASONING ===\" и идти ПЕРВЫМ в ответе, "
    "ДО раздела \"=== ROLE MAPPING ===\".\n"
    "Для КАЖДОГО пункта чек-листа объясни в этом разделе:\n"
    "- что именно увидел в диалоге (краткая реплика + таймкод);\n"
    "- какие альтернативы рассматривал (Да / Нет / Частично);\n"
    "- какие сомнения были и как ты их разрешил;\n"
    "- почему финально выбрал именно этот статус;\n"
    "- обоснование значения confidence — что снижало, что повышало уверенность.\n"
    "После раздела REASONING продолжай с раздела ROLE MAPPING как обычно."
)

REASONING_INSTRUCTION_SUMMARY = (
    "\n\nДОПОЛНИТЕЛЬНО (РЕЖИМ ИССЛЕДОВАНИЯ): В самом начале ответа добавь раздел "
    "\"=== REASONING ===\", в котором объясни:\n"
    "- почему именно эти сильные стороны попали в список (а не другие);\n"
    "- как ты взвешивал результаты разных чек-листов;\n"
    "- почему именно эти зоны роста приоритетны;\n"
    "- какие компромиссы и сомнения были при формировании рекомендаций.\n"
    "После раздела REASONING продолжай со стандартного итогового отчёта."
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


def extract_reasoning_block(text: str) -> Optional[str]:
    """
    Вырезает блок между маркером "=== REASONING ===" и следующим разделом.
    Возвращает None, если маркер не найден.
    """
    if not text:
        return None

    # Ищем заголовок REASONING (несколько вариантов написания)
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

    start = start_match.end()
    end = len(text)

    # Ищем следующий маркер раздела
    for marker in _REASONING_END_MARKERS:
        idx = text.find(marker, start)
        if idx != -1 and idx < end:
            end = idx

    block = text[start:end].strip()
    return block or None


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
        """Записывает шапку файла. Можно вызвать один раз в начале."""
        if self._disabled:
            return
        self._source = source
        self._source_kind = source_kind
        try:
            lines = [
                _HARD_SEP,
                f"RESEARCH MODE — Звонок #{self.conversation_id}",
                f"Начало:  {self.started_at.strftime('%Y-%m-%d %H:%M:%S')} UTC",
                f"User:    {self._user_email or '?'} (id={self.user_id})",
            ]
            if source or source_kind:
                src_line = f"Source:  {source or ''}"
                if source_kind:
                    src_line += f"  [{source_kind}]"
                lines.append(src_line)
            lines.append(_HARD_SEP)
            lines.append("")
            self.stages.append("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ResearchLogger write_header failed: {e}")

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

            lines: List[str] = []
            lines.append("")
            lines.append(_SEPARATOR)
            lines.append(
                f"ЭТАП {self._stage_idx} · {stage_name} · модель: {model}"
            )
            if usage_d["total_tokens"]:
                lines.append(
                    f"Токены: prompt={usage_d['prompt_tokens']}  completion={usage_d['completion_tokens']}  total={usage_d['total_tokens']}"
                )
            lines.append(_SEPARATOR)

            lines.append("")
            lines.append("[REASONING модели]")
            if reasoning:
                lines.append(reasoning)
            else:
                lines.append("(маркер === REASONING === не найден в ответе модели — привожу полный ответ ниже)")

            lines.append("")
            lines.append("[ФИНАЛЬНЫЙ ОТВЕТ МОДЕЛИ (raw)]")
            lines.append((raw_response or "").strip() or "(пустой ответ)")

            if parsed_decisions is not None:
                lines.append("")
                lines.append("[РАСПАРСЕННЫЕ РЕШЕНИЯ]")
                try:
                    lines.append(
                        json.dumps(parsed_decisions, ensure_ascii=False, indent=2)
                    )
                except (TypeError, ValueError):
                    lines.append(str(parsed_decisions))

            if extra:
                lines.append("")
                lines.append("[ДОП. КОНТЕКСТ]")
                try:
                    lines.append(json.dumps(extra, ensure_ascii=False, indent=2))
                except (TypeError, ValueError):
                    lines.append(str(extra))

            # Промпт идёт в самом конце как наиболее объёмная часть
            lines.append("")
            lines.append("[ПРОМПТ, КОТОРЫЙ БЫЛ ОТПРАВЛЕН]")
            lines.append((prompt or "").strip() or "(пустой промпт)")

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

            footer = "\n".join([
                "",
                _HARD_SEP,
                f"Конец:    {finished_at.strftime('%Y-%m-%d %H:%M:%S')} UTC",
                f"Длит-сть: {duration:.1f} с",
                f"Этапов:   {self._stage_idx}",
                f"Модели:   {models_summary}",
                f"Токены:   in={self._total_input_tokens}  out={self._total_output_tokens}  total={self._total_input_tokens + self._total_output_tokens}",
                f"Статус:   {status}" + (f"  (ошибка: {error})" if error else ""),
                _HARD_SEP,
                "",
            ])

            full_text = "\n".join(self.stages) + footer

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
