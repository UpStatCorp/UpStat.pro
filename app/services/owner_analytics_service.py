"""
Сервис аналитики для экрана владельца (Owner Command Center).
Агрегирует данные по команде: утечки денег, конверсия, риски, команда, прогноз.
"""

import json
import os
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, Integer

from models import (
    Team, TeamMember, User, CRMIntegration, CRMRecording, CRMDeal, CRMLead,
    Conversation, ParameterDefinition, ParameterValue,
    WinProbabilityScore, SellerPassport, PassportSnapshot,
    ManagerAction, ActionPattern, AnalysisTrainingPlan, Training,
    TrainingSession, TrainingErrorCorrection
)

logger = logging.getLogger(__name__)

# Ключевые параметры для каждого режима дашборда
MONEY_LEAK_PARAMS = {
    "objection_handled": {
        "label": "Обработка возражений",
        "description": "Деньги умирают на «дорого», «подумаю», «не сейчас». Менеджеры не дорабатывают возражения.",
        "impact_weight": 0.35,
    },
    "next_step_defined": {
        "label": "Следующий шаг",
        "description": "После сильных разговоров не фиксируется дата и обязательство клиента. Pipeline распадается.",
        "impact_weight": 0.30,
    },
    "needs_identified": {
        "label": "Квалификация потребностей",
        "description": "Отдел тратит энергию на псевдосделки и переоценивает вероятную выручку.",
        "impact_weight": 0.20,
    },
    "closing_timing": {
        "label": "Тайминг закрытия",
        "description": "Закрытие слишком рано или слишком поздно — момент готовности клиента упускается.",
        "impact_weight": 0.15,
    },
}

CONVERSION_PARAMS = [
    "needs_identified", "needs_clarity", "value_linked_to_needs",
    "next_step_confirmed", "listening_quality", "open_questions_ratio",
]

RISK_PARAMS = [
    "next_step_defined", "next_step_confirmed",
    "closing_timing", "client_commitment",
]

SPEED_PARAMS = [
    "urgency_created", "next_step_confirmed",
    "closing_timing", "deal_momentum",
]

TEAM_PARAMS = [
    "objection_handled", "needs_identified", "listening_quality",
    "next_step_defined", "value_linked_to_needs",
]


class OwnerAnalyticsService:

    @staticmethod
    def get_team_user_ids(db: Session, team_id: int) -> List[int]:
        members = db.query(TeamMember.user_id).filter(
            TeamMember.team_id == team_id
        ).all()
        return [m[0] for m in members]

    @staticmethod
    def get_team_conversations(
        db: Session, team_id: int, days: int = 30
    ) -> List[int]:
        """Все conversation_id команды за период (через CRM-записи участников)"""
        user_ids = OwnerAnalyticsService.get_team_user_ids(db, team_id)
        if not user_ids:
            return []

        since = datetime.utcnow() - timedelta(days=days)

        conv_ids_from_crm = (
            db.query(CRMRecording.conversation_id)
            .filter(
                CRMRecording.user_id.in_(user_ids),
                CRMRecording.conversation_id.isnot(None),
                CRMRecording.created_at >= since,
            )
            .all()
        )

        conv_ids_direct = (
            db.query(Conversation.id)
            .filter(
                Conversation.user_id.in_(user_ids),
                Conversation.created_at >= since,
            )
            .all()
        )

        all_ids = set()
        for (cid,) in conv_ids_from_crm:
            all_ids.add(cid)
        for (cid,) in conv_ids_direct:
            all_ids.add(cid)

        return list(all_ids)

    @staticmethod
    def get_parameter_averages(
        db: Session, conversation_ids: List[int], param_codes: List[str]
    ) -> Dict[str, Dict]:
        """Средние значения параметров по звонкам команды"""
        if not conversation_ids or not param_codes:
            return {}

        results = (
            db.query(
                ParameterDefinition.code,
                ParameterDefinition.title,
                ParameterDefinition.value_type,
                func.avg(ParameterValue.value_number).label("avg_number"),
                func.count(ParameterValue.id).label("count"),
                func.sum(
                    func.cast(ParameterValue.value_bool == True, Integer)
                ).label("true_count"),
            )
            .join(ParameterValue, ParameterValue.parameter_id == ParameterDefinition.id)
            .filter(
                ParameterDefinition.code.in_(param_codes),
                ParameterValue.conversation_id.in_(conversation_ids),
            )
            .group_by(ParameterDefinition.code, ParameterDefinition.title, ParameterDefinition.value_type)
            .all()
        )

        averages = {}
        for row in results:
            code = row[0]
            title = row[1]
            value_type = row[2]
            avg_number = row[3]
            count = row[4]
            true_count = row[5] or 0

            if value_type == "boolean":
                pct = (true_count / count * 100) if count > 0 else 0
                averages[code] = {
                    "title": title,
                    "value": round(pct, 1),
                    "unit": "%",
                    "count": count,
                    "type": "boolean",
                }
            else:
                averages[code] = {
                    "title": title,
                    "value": round(avg_number, 1) if avg_number else 0,
                    "unit": "%",
                    "count": count,
                    "type": "number",
                }

        return averages

    @staticmethod
    def get_deal_totals(db: Session, team_id: int, days: int = 30) -> Dict:
        """Суммы сделок: открытые, выигранные, проигранные"""
        user_ids = OwnerAnalyticsService.get_team_user_ids(db, team_id)
        if not user_ids:
            return {"total_pipeline": 0, "won": 0, "lost": 0, "open": 0, "count": 0}

        integ_ids = [
            i[0] for i in
            db.query(CRMIntegration.id).filter(CRMIntegration.user_id.in_(user_ids)).all()
        ]
        if not integ_ids:
            return {"total_pipeline": 0, "won": 0, "lost": 0, "open": 0, "count": 0}

        since = datetime.utcnow() - timedelta(days=days)

        deals = (
            db.query(CRMDeal)
            .filter(
                CRMDeal.integration_id.in_(integ_ids),
                CRMDeal.synced_at >= since,
            )
            .all()
        )

        total_pipeline = sum((d.opportunity or 0) for d in deals)
        won = sum((d.opportunity or 0) for d in deals if d.is_won is True)
        lost = sum((d.opportunity or 0) for d in deals if d.closed and d.is_won is False)
        open_deals = sum((d.opportunity or 0) for d in deals if not d.closed)

        return {
            "total_pipeline": total_pipeline,
            "won": won,
            "lost": lost,
            "open": open_deals,
            "count": len(deals),
            "won_count": sum(1 for d in deals if d.is_won is True),
            "lost_count": sum(1 for d in deals if d.closed and d.is_won is False),
            "open_count": sum(1 for d in deals if not d.closed),
        }

    @staticmethod
    def calculate_money_leaks(
        db: Session, team_id: int, days: int = 30
    ) -> Dict:
        """
        Главная логика: связываем слабые параметры звонков с суммами проигранных сделок.
        Утечка = сумма_проигранных * (100 - средний_процент_параметра) / 100 * вес_параметра
        """
        conv_ids = OwnerAnalyticsService.get_team_conversations(db, team_id, days)
        param_codes = list(MONEY_LEAK_PARAMS.keys())
        averages = OwnerAnalyticsService.get_parameter_averages(db, conv_ids, param_codes)
        deals = OwnerAnalyticsService.get_deal_totals(db, team_id, days)

        lost_amount = deals["lost"]
        open_amount = deals["open"]
        at_risk_base = lost_amount + open_amount * 0.3

        leaks = []
        total_salvageable = 0

        for code, config in MONEY_LEAK_PARAMS.items():
            param = averages.get(code)
            if param:
                value = param["value"]
                gap = max(0, 100 - value)
            else:
                value = 0
                gap = 100

            weight = config["impact_weight"]
            leak_amount = at_risk_base * (gap / 100) * weight

            tag = "Критичная утечка"
            if gap > 50:
                tag = "Самая большая утечка"
            elif gap > 30:
                tag = "Значительная утечка"
            elif gap > 15:
                tag = "Скрытая утечка"
            else:
                tag = "Небольшой резерв"

            leaks.append({
                "code": code,
                "name": config["label"],
                "description": config["description"],
                "value": value,
                "gap": gap,
                "leak_amount": round(leak_amount),
                "weight": weight,
                "tag": tag,
                "count": param["count"] if param else 0,
            })
            total_salvageable += leak_amount

        leaks.sort(key=lambda x: x["leak_amount"], reverse=True)

        return {
            "leaks": leaks,
            "total_salvageable": round(total_salvageable),
            "deals": deals,
            "total_calls": len(conv_ids),
            "period_days": days,
        }

    @staticmethod
    def get_conversion_data(db: Session, team_id: int, days: int = 30) -> Dict:
        conv_ids = OwnerAnalyticsService.get_team_conversations(db, team_id, days)
        averages = OwnerAnalyticsService.get_parameter_averages(db, conv_ids, CONVERSION_PARAMS)
        deals = OwnerAnalyticsService.get_deal_totals(db, team_id, days)

        conversion_rate = 0
        if deals["count"] > 0:
            conversion_rate = round(deals["won_count"] / deals["count"] * 100, 1)

        factors = []
        for code in CONVERSION_PARAMS:
            param = averages.get(code)
            if param:
                factors.append({
                    "code": code,
                    "name": param["title"],
                    "value": param["value"],
                    "count": param["count"],
                })

        factors.sort(key=lambda x: x["value"])

        return {
            "conversion_rate": conversion_rate,
            "factors": factors,
            "deals": deals,
            "total_calls": len(conv_ids),
        }

    @staticmethod
    def get_risk_data(db: Session, team_id: int, days: int = 30) -> Dict:
        conv_ids = OwnerAnalyticsService.get_team_conversations(db, team_id, days)
        averages = OwnerAnalyticsService.get_parameter_averages(db, conv_ids, RISK_PARAMS)
        deals = OwnerAnalyticsService.get_deal_totals(db, team_id, days)

        win_probs = (
            db.query(func.avg(WinProbabilityScore.win_probability))
            .filter(WinProbabilityScore.conversation_id.in_(conv_ids))
            .scalar()
        ) if conv_ids else None

        risk_params = []
        for code in RISK_PARAMS:
            param = averages.get(code)
            if param:
                risk_params.append({
                    "code": code,
                    "name": param["title"],
                    "value": param["value"],
                    "count": param["count"],
                })

        plan_risk = 0
        if deals["open"] > 0 and risk_params:
            avg_risk_param = sum(p["value"] for p in risk_params) / len(risk_params)
            plan_risk = round((100 - avg_risk_param) * deals["open"] / 100)

        return {
            "risk_params": risk_params,
            "deals": deals,
            "avg_win_probability": round(win_probs, 1) if win_probs else 0,
            "plan_risk_amount": plan_risk,
            "total_calls": len(conv_ids),
        }

    @staticmethod
    def get_team_ranking(db: Session, team_id: int, days: int = 30) -> Dict:
        user_ids = OwnerAnalyticsService.get_team_user_ids(db, team_id)
        if not user_ids:
            return {"members": [], "team_stats": {}}

        members_data = []
        for uid in user_ids:
            user = db.query(User).filter(User.id == uid).first()
            if not user:
                continue

            passport = db.query(SellerPassport).filter(
                SellerPassport.user_id == uid
            ).first()

            member_info = db.query(TeamMember).filter(
                TeamMember.team_id == team_id,
                TeamMember.user_id == uid,
            ).first()

            conv_ids = []
            user_convs = (
                db.query(Conversation.id)
                .filter(Conversation.user_id == uid)
                .all()
            )
            conv_ids = [c[0] for c in user_convs]

            param_avgs = {}
            if conv_ids:
                param_avgs = OwnerAnalyticsService.get_parameter_averages(
                    db, conv_ids, TEAM_PARAMS
                )

            overall_score = 0
            if param_avgs:
                values = [p["value"] for p in param_avgs.values()]
                overall_score = round(sum(values) / len(values), 1) if values else 0

            members_data.append({
                "user": user,
                "role_in_team": member_info.role_in_team if member_info else "member",
                "passport": passport,
                "param_averages": param_avgs,
                "overall_score": overall_score,
                "contact_score": passport.score_contact if passport else 0,
                "needs_score": passport.score_needs if passport else 0,
                "presentation_score": passport.score_presentation if passport else 0,
                "objections_score": passport.score_objections if passport else 0,
                "closing_score": passport.score_closing if passport else 0,
                "total_calls": passport.total_calls_analyzed if passport else 0,
            })

        members_data.sort(key=lambda x: x["overall_score"], reverse=True)

        best = members_data[0]["overall_score"] if members_data else 0
        worst = members_data[-1]["overall_score"] if members_data else 0
        avg = round(sum(m["overall_score"] for m in members_data) / len(members_data), 1) if members_data else 0

        risk_count = sum(1 for m in members_data if m["overall_score"] < 40)

        return {
            "members": members_data,
            "team_stats": {
                "total_members": len(members_data),
                "best_score": best,
                "worst_score": worst,
                "avg_score": avg,
                "risk_count": risk_count,
                "gap": round(best - worst, 1),
            }
        }

    @staticmethod
    def get_speed_data(db: Session, team_id: int, days: int = 30) -> Dict:
        conv_ids = OwnerAnalyticsService.get_team_conversations(db, team_id, days)
        averages = OwnerAnalyticsService.get_parameter_averages(db, conv_ids, SPEED_PARAMS)

        speed_factors = []
        for code in SPEED_PARAMS:
            param = averages.get(code)
            if param:
                speed_factors.append({
                    "code": code,
                    "name": param["title"],
                    "value": param["value"],
                    "count": param["count"],
                })

        return {
            "speed_factors": speed_factors,
            "total_calls": len(conv_ids),
        }

    @staticmethod
    def get_action_patterns(db: Session, team_id: int) -> Dict:
        patterns = (
            db.query(ActionPattern)
            .filter(
                ActionPattern.team_id == team_id,
                ActionPattern.status.in_(["confirmed", "reported"]),
            )
            .order_by(ActionPattern.percentage.desc())
            .limit(10)
            .all()
        )

        positive = [p for p in patterns if p.outcome == "positive"]
        negative = [p for p in patterns if p.outcome == "negative"]

        return {"positive": positive, "negative": negative}

    @staticmethod
    def generate_ai_insights(data: Dict) -> Dict:
        """Генерирует AI-тексты для блоков 'Что я вижу / Почему / Что предлагаю'"""
        leaks = data.get("money_leaks", {})
        leak_items = leaks.get("leaks", [])
        total_salvageable = leaks.get("total_salvageable", 0)
        deals = leaks.get("deals", {})

        if not leak_items:
            return {
                "see": "Пока недостаточно данных для анализа. Загрузите звонки для начала работы.",
                "why": "Система начнёт анализировать утечки денег после обработки первых звонков.",
                "because": "Загрузите звонки через CRM-интеграцию или вручную.",
                "recommendations": [],
            }

        top_leaks = leak_items[:3]
        leak_names = ", ".join(l["name"].lower() for l in top_leaks)

        see = (
            f"Я вижу, что {len(top_leaks)} зоны формируют основную долю потерь: "
            f"{leak_names}. "
            f"Потенциально спасаемая сумма — {_format_money(total_salvageable)} за 30 дней."
        )

        why = (
            "Эти зоны чаще всего отличают успешные звонки от провальных. "
            "Пока они не исправлены, команда теряет деньги, искажает прогноз "
            "и тратит ресурс на слабые сделки."
        )

        because = (
            "Эти дефекты дают наибольшую долю текущих потерь и при этом "
            "исправляются быстрее, чем полная перестройка скриптов или CRM-процессов."
        )

        recommendations = []
        for leak in top_leaks:
            rec = {
                "title": f"Исправить: {leak['name']}",
                "desc": leak["description"],
                "roi": f"Потенциальный возврат: {_format_money(leak['leak_amount'])} / 30 дней",
            }
            recommendations.append(rec)

        return {
            "see": see,
            "why": why,
            "because": because,
            "recommendations": recommendations,
        }

    @staticmethod
    def get_full_dashboard(db: Session, team_id: int, days: int = 30) -> Dict:
        """Собирает все данные для экрана владельца"""
        money_leaks = OwnerAnalyticsService.calculate_money_leaks(db, team_id, days)
        conversion = OwnerAnalyticsService.get_conversion_data(db, team_id, days)
        risk = OwnerAnalyticsService.get_risk_data(db, team_id, days)
        speed = OwnerAnalyticsService.get_speed_data(db, team_id, days)
        team = OwnerAnalyticsService.get_team_ranking(db, team_id, days)
        patterns = OwnerAnalyticsService.get_action_patterns(db, team_id)

        combined = {
            "money_leaks": money_leaks,
            "conversion": conversion,
            "risk": risk,
            "speed": speed,
            "team": team,
            "patterns": patterns,
        }

        ai_insights = OwnerAnalyticsService.generate_ai_insights(combined)
        combined["ai_insights"] = ai_insights

        return combined


def _format_money(amount: float) -> str:
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.1f} млн"
    elif amount >= 1_000:
        return f"{amount / 1_000:.0f} тыс"
    else:
        return f"{amount:.0f}"
