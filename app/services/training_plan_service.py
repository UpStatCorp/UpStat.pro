import json
import re
from typing import List, Dict
from sqlalchemy.orm import Session
from models import AnalysisTrainingPlan, Training, Message, Attachment
import logging

logger = logging.getLogger(__name__)


class TrainingPlanService:
    """Сервис для создания планов тренировок на основе анализа"""
    
    @staticmethod
    async def parse_recommendations_from_analysis(analysis_text: str) -> List[Dict]:
        """
        Парсит рекомендации из текста анализа GPT.
        Возвращает список рекомендаций.
        """
        recommendations = []
        
        # Ищем секцию "Рекомендации" или "Что улучшить"
        patterns = [
            r"(?:Рекомендации|РЕКОМЕНДАЦИИ|Что улучшить|ЧТО УЛУЧШИТЬ)[\s:]*\n(.*?)(?:\n\n|\Z)",
            r"(?:Советы|СОВЕТЫ|Области для улучшения)[\s:]*\n(.*?)(?:\n\n|\Z)",
        ]
        
        recommendations_text = ""
        for pattern in patterns:
            match = re.search(pattern, analysis_text, re.DOTALL | re.IGNORECASE)
            if match:
                recommendations_text = match.group(1)
                break
        
        if not recommendations_text:
            # Если не нашли явную секцию, ищем пункты с "Нет" или "Частично"
            lines = analysis_text.split('\n')
            for i, line in enumerate(lines):
                if ('Нет' in line or 'Частично' in line) and ('Статус:' in line or '❌' in line or '⚠️' in line):
                    # Извлекаем название пункта
                    title_match = re.search(r'(?:^|\d+\.)\s*(.+?)(?:\s*[-–—]\s*Статус|$)', line)
                    if title_match:
                        title = title_match.group(1).strip()
                        # Ищем рекомендацию в следующих строках
                        recommendation = ""
                        for j in range(i+1, min(i+5, len(lines))):
                            if lines[j].strip() and not lines[j].startswith(('Статус:', 'Комментарий:', '---')):
                                recommendation += lines[j].strip() + " "
                        
                        if recommendation:
                            recommendations.append({
                                "title": title[:100],  # Ограничиваем длину
                                "issue": line,
                                "recommendation": recommendation.strip()[:500],
                                "priority": "high" if "Нет" in line else "medium"
                            })
        else:
            # Парсим пронумерованные рекомендации
            items = re.split(r'\n\s*\d+\.', recommendations_text)
            for item in items:
                item = item.strip()
                if len(item) > 20:  # Фильтруем слишком короткие
                    # Пытаемся извлечь заголовок (первая строка или до двоеточия)
                    lines = item.split('\n')
                    title = lines[0].split(':')[0].strip()[:100]
                    
                    recommendations.append({
                        "title": title or "Улучшение навыков продаж",
                        "issue": item[:200],
                        "recommendation": item,
                        "priority": "medium"
                    })
        
        # Если ничего не нашли, используем GPT для извлечения рекомендаций
        if not recommendations:
            recommendations = await TrainingPlanService._extract_recommendations_with_gpt(analysis_text)
        
        return recommendations[:5]  # Максимум 5 тренировок
    
    @staticmethod
    async def _extract_recommendations_with_gpt(analysis_text: str) -> List[Dict]:
        """Использует GPT для извлечения рекомендаций из анализа"""
        from openai import AsyncOpenAI
        import os
        
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        prompt = f"""Проанализируй следующий отчёт по звонку и извлеки конкретные рекомендации для тренировок.
        
Отчёт:
{analysis_text[:3000]}

Верни JSON с массивом recommendations в формате:
{{
  "recommendations": [
    {{
      "title": "Краткое название проблемы (до 100 символов)",
      "issue": "Описание что было не так (до 200 символов)",
      "recommendation": "Конкретная рекомендация как улучшить (до 500 символов)",
      "priority": "high|medium|low"
    }}
  ]
}}

Максимум 5 самых важных рекомендаций. Только JSON, без комментариев."""

        try:
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                timeout=30.0
            )
            
            result = json.loads(response.choices[0].message.content)
            return result.get("recommendations", [])
        except Exception as e:
            logger.error(f"Ошибка извлечения рекомендаций через GPT: {e}")
            return []
    
    @staticmethod
    async def create_training_plan(
        db: Session,
        user_id: int,
        report_message_id: int,
        analysis_text: str
    ) -> AnalysisTrainingPlan:
        """Создаёт план тренировок на основе анализа"""
        
        # Парсим рекомендации
        recommendations = await TrainingPlanService.parse_recommendations_from_analysis(analysis_text)
        
        if not recommendations:
            raise ValueError("Не удалось извлечь рекомендации из анализа")
        
        # Создаём план
        plan = AnalysisTrainingPlan(
            user_id=user_id,
            report_message_id=report_message_id,
            title=f"План тренировок #{report_message_id}",
            recommendations_json=json.dumps(recommendations, ensure_ascii=False),
            total_trainings=len(recommendations)
        )
        
        db.add(plan)
        db.flush()  # Получаем ID плана
        
        # Создаём тренировки
        for i, rec in enumerate(recommendations):
            training = Training(
                plan_id=plan.id,
                order=i + 1,
                title=rec["title"],
                description=rec["issue"],
                recommendation=rec["recommendation"],
                scenario_type="custom",
                status="available" if i == 0 else "locked",  # Первая доступна сразу
                checklist_json=json.dumps(TrainingPlanService._generate_checklist(rec), ensure_ascii=False)
            )
            db.add(training)
        
        db.commit()
        db.refresh(plan)
        
        logger.info(f"Создан план тренировок {plan.id} с {len(recommendations)} тренировками")
        
        # Создаём уведомление о готовности плана
        try:
            from services.notification_service import NotificationService
            NotificationService.create_training_ready_notification(
                db=db,
                user_id=user_id,
                plan_id=plan.id,
                report_msg_id=report_message_id,
                trainings_count=len(recommendations)
            )
            logger.info(f"Создано уведомление о готовности плана тренировок {plan.id}")
        except Exception as e:
            logger.error(f"Ошибка создания уведомления: {e}")
            # Не прерываем создание плана из-за ошибки уведомления
        
        return plan
    
    @staticmethod
    def _generate_checklist(recommendation: Dict) -> Dict:
        """Генерирует чеклист для тренировки на основе рекомендации"""
        # Простой чеклист, можно улучшить через GPT
        return {
            "categories": [
                {
                    "name": "Цель тренировки",
                    "items": [
                        recommendation["title"],
                        "Применить рекомендацию",
                        "Получить обратную связь от ИИ"
                    ]
                }
            ]
        }
    
    @staticmethod
    def unlock_next_training(db: Session, plan_id: int):
        """Разблокирует следующую тренировку после завершения текущей"""
        plan = db.query(AnalysisTrainingPlan).filter_by(id=plan_id).first()
        if not plan:
            return
        
        # Находим первую locked тренировку
        next_training = db.query(Training).filter_by(
            plan_id=plan_id,
            status="locked"
        ).order_by(Training.order).first()
        
        if next_training:
            next_training.status = "available"
            db.commit()
            logger.info(f"Разблокирована тренировка {next_training.id} (порядок {next_training.order})")


