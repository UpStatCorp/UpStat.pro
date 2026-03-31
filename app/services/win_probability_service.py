"""
Сервис расчёта вероятности успешного закрытия сделки (Win Probability).
Потолок вероятности: 80% — максимум, который мы предлагаем пользователю.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from models import (
    ChecklistItemDefinition,
    ChecklistItemScore,
    WinProbabilityScore,
)

logger = logging.getLogger("main")

MAX_PROBABILITY = 80.0


def save_checklist_scores(
    conversation_id: int,
    all_scores: list[dict],
    db: Session,
) -> int:
    """
    Сохраняет собранные +/- оценки из анализа чеклистов в БД.
    Матчит item_code с checklist_item_definitions.

    Returns:
        Количество сохранённых оценок.
    """
    if not all_scores:
        logger.warning(f"Нет оценок для сохранения (conversation_id={conversation_id})")
        return 0

    codes = [s["code"] for s in all_scores]
    definitions = db.query(ChecklistItemDefinition).filter(
        ChecklistItemDefinition.item_code.in_(codes),
        ChecklistItemDefinition.is_active == True,
    ).all()
    code_to_def = {d.item_code: d for d in definitions}

    saved = 0
    for score in all_scores:
        definition = code_to_def.get(score["code"])
        if not definition:
            logger.debug(f"Пункт {score['code']} не найден в справочнике, пропускаем")
            continue

        existing = db.query(ChecklistItemScore).filter(
            ChecklistItemScore.conversation_id == conversation_id,
            ChecklistItemScore.item_id == definition.id,
        ).first()

        if existing:
            existing.passed = score["passed"]
            existing.confidence = score.get("confidence", 0.8)
        else:
            db.add(ChecklistItemScore(
                conversation_id=conversation_id,
                item_id=definition.id,
                passed=score["passed"],
                confidence=score.get("confidence", 0.8),
            ))
        saved += 1

    db.flush()
    logger.info(f"Сохранено {saved} оценок чеклиста для conversation_id={conversation_id}")
    return saved


def calculate_win_probability(
    conversation_id: int,
    db: Session,
    deal_status: Optional[str] = None,
    crm_recording_id: Optional[int] = None,
    deal_id: Optional[int] = None,
    lead_id: Optional[int] = None,
) -> Optional[WinProbabilityScore]:
    """
    Рассчитывает вероятность закрытия сделки на основе оценок чеклистов.
    Потолок: 80%.

    Returns:
        WinProbabilityScore или None если нет данных.
    """
    scores = (
        db.query(ChecklistItemScore)
        .filter(ChecklistItemScore.conversation_id == conversation_id)
        .all()
    )
    if not scores:
        logger.warning(f"Нет оценок для расчёта вероятности (conversation_id={conversation_id})")
        return None

    item_ids = [s.item_id for s in scores]
    definitions = (
        db.query(ChecklistItemDefinition)
        .filter(
            ChecklistItemDefinition.id.in_(item_ids),
            ChecklistItemDefinition.is_active == True,
        )
        .all()
    )
    def_by_id = {d.id: d for d in definitions}

    total_weight = 0.0
    achieved_weight = 0.0
    passed_count = 0
    failed_count = 0
    breakdown_by_checklist: dict[str, dict] = {}

    for score in scores:
        defn = def_by_id.get(score.item_id)
        if not defn:
            continue

        total_weight += defn.weight
        if score.passed:
            achieved_weight += defn.weight
            passed_count += 1
        else:
            failed_count += 1

        cl_id = defn.checklist_id
        if cl_id not in breakdown_by_checklist:
            breakdown_by_checklist[cl_id] = {
                "title": defn.checklist_title,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "total_weight": 0.0,
                "achieved_weight": 0.0,
                "failed_items": [],
            }
        bd = breakdown_by_checklist[cl_id]
        bd["total"] += 1
        bd["total_weight"] += defn.weight
        if score.passed:
            bd["passed"] += 1
            bd["achieved_weight"] += defn.weight
        else:
            bd["failed"] += 1
            bd["failed_items"].append({
                "code": defn.item_code,
                "text": defn.item_text,
                "weight": defn.weight,
                "block": defn.block_title,
            })

    weighted_score = achieved_weight / total_weight if total_weight > 0 else 0.0
    win_probability = round(weighted_score * MAX_PROBABILITY, 1)

    breakdown_json = json.dumps(breakdown_by_checklist, ensure_ascii=False, indent=2)

    existing = db.query(WinProbabilityScore).filter(
        WinProbabilityScore.conversation_id == conversation_id
    ).first()

    if existing:
        existing.total_items = passed_count + failed_count
        existing.passed_items = passed_count
        existing.failed_items = failed_count
        existing.weighted_score = round(weighted_score, 4)
        existing.win_probability = win_probability
        existing.max_probability = MAX_PROBABILITY
        existing.score_breakdown_json = breakdown_json
        existing.deal_status = deal_status
        existing.crm_recording_id = crm_recording_id
        existing.deal_id = deal_id
        existing.lead_id = lead_id
        win_prob = existing
    else:
        win_prob = WinProbabilityScore(
            conversation_id=conversation_id,
            crm_recording_id=crm_recording_id,
            deal_id=deal_id,
            lead_id=lead_id,
            deal_status=deal_status,
            total_items=passed_count + failed_count,
            passed_items=passed_count,
            failed_items=failed_count,
            weighted_score=round(weighted_score, 4),
            win_probability=win_probability,
            max_probability=MAX_PROBABILITY,
            score_breakdown_json=breakdown_json,
        )
        db.add(win_prob)

    db.flush()
    logger.info(
        f"Win Probability для conversation_id={conversation_id}: "
        f"{win_probability}% (из {MAX_PROBABILITY}%), "
        f"{passed_count}/{passed_count + failed_count} пунктов выполнено"
    )
    return win_prob


def generate_probability_report(
    win_prob: WinProbabilityScore,
    output_dir: Path,
    db: Session,
) -> Path:
    """
    Генерирует текстовый файл отчёта о вероятности закрытия.

    Returns:
        Path к созданному файлу.
    """
    breakdown = {}
    if win_prob.score_breakdown_json:
        try:
            breakdown = json.loads(win_prob.score_breakdown_json)
        except json.JSONDecodeError:
            pass

    total = win_prob.total_items
    passed = win_prob.passed_items
    failed = win_prob.failed_items
    pct_done = round(passed / total * 100) if total > 0 else 0

    lines = [
        "══════════════════════════════════════════════════",
        "   ОЦЕНКА ВЕРОЯТНОСТИ УСПЕШНОГО ЗАКРЫТИЯ СДЕЛКИ",
        "══════════════════════════════════════════════════",
        "",
        f"Вероятность успешного закрытия: {win_prob.win_probability}% из {win_prob.max_probability}%",
        f"Максимально достижимая вероятность: {win_prob.max_probability}%",
        "",
        f"Выполнено пунктов: {passed} из {total} ({pct_done}%)",
        "",
    ]

    if breakdown:
        lines.append("─── РЕЗУЛЬТАТЫ ПО ЧЕКЛИСТАМ ───")
        for cl_id, bd in breakdown.items():
            cl_total = bd["total"]
            cl_passed = bd["passed"]
            cl_pct = round(cl_passed / cl_total * 100) if cl_total > 0 else 0
            marker = "+" if cl_pct >= 70 else "-"
            lines.append(f"[{marker}] {bd['title']}: {cl_passed}/{cl_total} ({cl_pct}%)")
        lines.append("")

    all_failed = []
    for cl_id, bd in breakdown.items():
        for item in bd.get("failed_items", []):
            all_failed.append(item)
    all_failed.sort(key=lambda x: x["weight"], reverse=True)

    critical_failed = [f for f in all_failed if f["weight"] >= 2.0]
    if critical_failed:
        lines.append("─── КРИТИЧНЫЕ НЕВЫПОЛНЕННЫЕ ПУНКТЫ ───")
        for item in critical_failed[:10]:
            lines.append(f"✗ [вес {item['weight']:.1f}] {item['text']}")
        lines.append("")

    if all_failed and win_prob.total_items > 0:
        total_weight = sum(
            d.weight for d in db.query(ChecklistItemDefinition).filter(
                ChecklistItemDefinition.is_active == True,
                ChecklistItemDefinition.item_code.in_([f["code"] for f in all_failed])
            ).all()
        )
        all_weights_sum = db.query(
            ChecklistItemDefinition
        ).filter(ChecklistItemDefinition.is_active == True).all()
        total_all_weight = sum(d.weight for d in all_weights_sum)

        if total_all_weight > 0:
            lines.append("─── ПОТЕНЦИАЛ РОСТА ───")
            lines.append("Если исправить критичные пункты:")
            shown = 0
            for item in all_failed:
                if shown >= 5:
                    break
                if item["weight"] >= 2.0:
                    potential_gain = round(item["weight"] / total_all_weight * MAX_PROBABILITY, 1)
                    lines.append(f"  +{potential_gain}% — {item['text'][:80]}")
                    shown += 1
            lines.append("")

    report_text = "\n".join(lines)
    report_path = output_dir / f"win_probability_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
    report_path.write_text(report_text, encoding="utf-8")

    logger.info(f"Отчёт вероятности сохранён: {report_path}")
    return report_path
