"""Сервис для расчета аналитики и конверсий"""
import logging
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from models import (
    User, Team, TeamMember, AnalysisTrainingPlan, Training, TrainingSession,
    TrainingConversionMetric, TrainingErrorCorrection, Conversation, Message
)

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Сервис для расчета аналитики и конверсий"""
    
    @staticmethod
    def calculate_conversion_rates(
        db: Session,
        user_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, float]:
        """
        Рассчитывает конверсии между этапами тренировок для пользователя.
        
        Returns:
            Dict с конверсиями: {
                "plan_created_to_first_training": 0.85,
                "training_1_to_2": 0.72,
                "training_2_to_3": 0.68,
                ...
            }
        """
        # Получаем все планы пользователя
        query = db.query(AnalysisTrainingPlan).filter(
            AnalysisTrainingPlan.user_id == user_id
        )
        
        if start_date:
            query = query.filter(AnalysisTrainingPlan.created_at >= start_date)
        if end_date:
            query = query.filter(AnalysisTrainingPlan.created_at <= end_date)
        
        plans = query.all()
        
        if not plans:
            return {}
        
        conversions = {}
        
        # Конверсия: план создан -> первая тренировка начата
        plans_with_first_training = 0
        for plan in plans:
            first_training = db.query(Training).filter(
                Training.plan_id == plan.id,
                Training.order == 1
            ).first()
            if first_training and first_training.attempts > 0:
                plans_with_first_training += 1
        
        conversions["plan_created_to_first_training"] = (
            plans_with_first_training / len(plans) if plans else 0
        )
        
        # Конверсии между этапами тренировок
        max_order = db.query(func.max(Training.order)).filter(
            Training.plan_id.in_([p.id for p in plans])
        ).scalar() or 0
        
        for i in range(1, max_order + 1):
            current_trainings = db.query(Training).filter(
                Training.plan_id.in_([p.id for p in plans]),
                Training.order == i
            ).all()
            
            if not current_trainings:
                continue
            
            # Сколько тренировок завершено
            completed_current = sum(1 for t in current_trainings if t.status == "completed")
            
            if i == 1:
                # Для первой тренировки считаем конверсию от начала
                conversions[f"training_{i}_completion"] = (
                    completed_current / len(current_trainings) if current_trainings else 0
                )
            else:
                # Для последующих - конверсия от предыдущего этапа
                prev_trainings = db.query(Training).filter(
                    Training.plan_id.in_([p.id for p in plans]),
                    Training.order == i - 1
                ).all()
                prev_completed = sum(1 for t in prev_trainings if t.status == "completed")
                
                if prev_completed > 0:
                    conversions[f"training_{i-1}_to_{i}"] = (
                        completed_current / prev_completed
                    )
        
        return conversions
    
    @staticmethod
    def get_member_analytics(
        db: Session,
        member_user_id: int,
        team_id: Optional[int] = None
    ) -> Dict:
        """
        Получает полную аналитику по участнику команды.
        
        Returns:
            {
                "user": User,
                "plans": [...],
                "trainings": [...],
                "sessions": [...],
                "errors": [...],
                "corrections": [...],
                "conversion_rates": {...},
                "stats": {...}
            }
        """
        user = db.query(User).filter(User.id == member_user_id).first()
        if not user:
            return {}
        
        # Планы тренировок
        plans = db.query(AnalysisTrainingPlan).filter(
            AnalysisTrainingPlan.user_id == member_user_id
        ).order_by(AnalysisTrainingPlan.created_at.desc()).all()
        
        # Тренировки
        training_ids = [t.id for plan in plans for t in plan.trainings]
        trainings = db.query(Training).filter(
            Training.id.in_(training_ids) if training_ids else False
        ).all() if training_ids else []
        
        # Сессии
        session_ids = [s.id for training in trainings for s in training.sessions]
        sessions = db.query(TrainingSession).filter(
            TrainingSession.id.in_(session_ids) if session_ids else False
        ).all() if session_ids else []
        
        # Ошибки и коррекции
        errors = db.query(TrainingErrorCorrection).filter(
            TrainingErrorCorrection.user_id == member_user_id
        ).order_by(TrainingErrorCorrection.detected_at.desc()).all()
        
        # Конверсии
        conversion_rates = AnalyticsService.calculate_conversion_rates(db, member_user_id)
        
        # Статистика
        sessions_with_score = [s for s in sessions if s.score is not None]
        stats = {
            "total_plans": len(plans),
            "active_plans": sum(1 for p in plans if p.status == "active"),
            "completed_plans": sum(1 for p in plans if p.status == "completed"),
            "total_trainings": len(trainings),
            "completed_trainings": sum(1 for t in trainings if t.status == "completed"),
            "total_sessions": len(sessions),
            "avg_score": (
                sum(s.score for s in sessions_with_score) / len(sessions_with_score)
                if sessions_with_score else 0
            ),
            "total_errors": len(errors),
            "corrections_applied": sum(1 for e in errors if e.correction_applied),
            "corrections_pending": sum(1 for e in errors if not e.correction_applied)
        }
        
        return {
            "user": user,
            "plans": plans,
            "trainings": trainings,
            "sessions": sessions,
            "errors": errors,
            "conversion_rates": conversion_rates,
            "stats": stats
        }
    
    @staticmethod
    def get_team_analytics(
        db: Session,
        team_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict:
        """
        Получает аналитику по всей команде.
        
        Returns:
            {
                "team": Team,
                "members": [
                    {
                        "user": User,
                        "stats": {...},
                        "conversion_rates": {...},
                        "errors": [...],
                        "recent_activity": [...]
                    },
                    ...
                ],
                "team_stats": {...},
                "conversion_trends": {...}
            }
        """
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            return {}
        
        # Получаем участников
        members = db.query(TeamMember).filter(
            TeamMember.team_id == team_id
        ).all()
        
        member_analytics = []
        for member in members:
            analytics = AnalyticsService.get_member_analytics(
                db, member.user_id, team_id
            )
            if analytics:
                member_analytics.append(analytics)
        
        # Статистика команды
        member_stats_list = [a["stats"] for a in member_analytics]
        team_stats = {
            "total_members": len(members),
            "total_plans": sum(a["stats"]["total_plans"] for a in member_analytics),
            "active_plans": sum(a["stats"]["active_plans"] for a in member_analytics),
            "completed_plans": sum(a["stats"]["completed_plans"] for a in member_analytics),
            "total_errors": sum(a["stats"]["total_errors"] for a in member_analytics),
            "corrections_applied": sum(a["stats"]["corrections_applied"] for a in member_analytics),
            "avg_score": (
                sum(a["stats"]["avg_score"] for a in member_analytics) / len(member_analytics)
                if member_analytics else 0
            )
        }
        
        # Тренды конверсий (по неделям)
        conversion_trends = AnalyticsService.get_conversion_trends(
            db, team_id, start_date, end_date
        )
        
        return {
            "team": team,
            "members": member_analytics,
            "team_stats": team_stats,
            "conversion_trends": conversion_trends
        }
    
    @staticmethod
    def get_conversion_trends(
        db: Session,
        team_id: Optional[int],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict:
        """
        Получает тренды конверсий по неделям/дням.
        
        Returns:
            {
                "daily": [
                    {"date": "2024-01-01", "conversions": {...}},
                    ...
                ],
                "weekly": [
                    {"week": "2024-W01", "conversions": {...}},
                    ...
                ]
            }
        """
        # Получаем метрики из БД или рассчитываем на лету
        query = db.query(TrainingConversionMetric)
        
        if team_id:
            query = query.filter(TrainingConversionMetric.team_id == team_id)
        if start_date:
            query = query.filter(TrainingConversionMetric.metric_date >= start_date)
        if end_date:
            query = query.filter(TrainingConversionMetric.metric_date <= end_date)
        
        metrics = query.order_by(TrainingConversionMetric.metric_date).all()
        
        daily = []
        weekly = {}
        
        for metric in metrics:
            if metric.period_type == "daily":
                daily.append({
                    "date": metric.metric_date.strftime("%Y-%m-%d"),
                    "conversions": json.loads(metric.conversion_rates_json)
                })
            elif metric.period_type == "weekly":
                week_key = metric.metric_date.strftime("%Y-W%W")
                if week_key not in weekly:
                    weekly[week_key] = []
                weekly[week_key].append({
                    "date": metric.metric_date.strftime("%Y-%m-%d"),
                    "conversions": json.loads(metric.conversion_rates_json)
                })
        
        return {
            "daily": daily,
            "weekly": [{"week": k, "data": v} for k, v in weekly.items()]
        }
    
    @staticmethod
    def extract_errors_from_analysis(
        db: Session,
        user_id: int,
        conversation_id: int,
        message_id: int,
        analysis_text: str,
        team_id: Optional[int] = None
    ):
        """
        Извлекает ошибки и коррекции из текста анализа и сохраняет в БД.
        Вызывается после завершения анализа звонка.
        """
        # Парсим анализ для поиска ошибок и рекомендаций
        # Формат может быть разным, но обычно это структурированный текст
        
        errors = []
        
        # Пример парсинга (нужно адаптировать под реальный формат анализа)
        # Ищем паттерны типа "Ошибка: ...", "Рекомендация: ..."
        error_patterns = [
            r"Ошибка[:\s]+(.+?)(?=Рекомендация|Решение|$)",
            r"Проблема[:\s]+(.+?)(?=Решение|Рекомендация|$)",
            r"Issue[:\s]+(.+?)(?=Recommendation|Solution|$)",
            r"❌\s*(.+?)(?=✅|💡|$)",
            r"⚠️\s*(.+?)(?=✅|💡|$)"
        ]
        
        recommendation_patterns = [
            r"Рекомендация[:\s]+(.+?)(?=Ошибка|Проблема|$)",
            r"Решение[:\s]+(.+?)(?=Проблема|Ошибка|$)",
            r"Recommendation[:\s]+(.+?)(?=Issue|Problem|$)",
            r"✅\s*(.+?)(?=❌|⚠️|$)",
            r"💡\s*(.+?)(?=❌|⚠️|$)"
        ]
        
        # Извлекаем ошибки и рекомендации
        found_errors = []
        for pattern in error_patterns:
            matches = re.finditer(pattern, analysis_text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                error_text = match.group(1).strip()
                if error_text and len(error_text) > 10:  # Минимальная длина
                    found_errors.append(error_text)
        
        found_recommendations = []
        for pattern in recommendation_patterns:
            matches = re.finditer(pattern, analysis_text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                rec_text = match.group(1).strip()
                if rec_text and len(rec_text) > 10:
                    found_recommendations.append(rec_text)
        
        # Если не нашли через паттерны, пытаемся найти через структурированные блоки
        if not found_errors:
            # Ищем блоки с проблемами и рекомендациями
            sections = re.split(r'\n\n+', analysis_text)
            for section in sections:
                if 'проблем' in section.lower() or 'ошибк' in section.lower() or 'issue' in section.lower():
                    found_errors.append(section[:500])
                if 'рекомендац' in section.lower() or 'решен' in section.lower() or 'recommendation' in section.lower():
                    found_recommendations.append(section[:1000])
        
        # Создаем записи об ошибках и коррекциях
        created_count = 0
        for i, error_text in enumerate(found_errors[:20]):  # Ограничиваем 20 ошибками
            correction_text = found_recommendations[i] if i < len(found_recommendations) else ""
            
            # Определяем тип ошибки
            error_type = "general"
            error_lower = error_text.lower()
            if any(word in error_lower for word in ['привет', 'greeting', 'здравств']):
                error_type = "greeting"
            elif any(word in error_lower for word in ['возражен', 'objection', 'отказ']):
                error_type = "objection_handling"
            elif any(word in error_lower for word in ['закрыт', 'close', 'сделк']):
                error_type = "closing"
            elif any(word in error_lower for word in ['вопрос', 'question', 'уточнен']):
                error_type = "questioning"
            
            # Определяем серьезность
            severity = "medium"
            if any(word in error_lower for word in ['критич', 'critical', 'серьезн', 'важн']):
                severity = "high"
            elif any(word in error_lower for word in ['незначительн', 'minor', 'небольш']):
                severity = "low"
            
            error = TrainingErrorCorrection(
                user_id=user_id,
                team_id=team_id,
                conversation_id=conversation_id,
                message_id=message_id,
                error_type=error_type,
                error_description=error_text[:500],  # Ограничиваем длину
                error_severity=severity,
                correction_text=correction_text[:1000] if correction_text else "Требуется коррекция"
            )
            db.add(error)
            created_count += 1
        
        if created_count > 0:
            db.commit()
            logger.info(f"Извлечено {created_count} ошибок из анализа {message_id}")
        else:
            logger.debug(f"Не найдено ошибок в анализе {message_id}")

