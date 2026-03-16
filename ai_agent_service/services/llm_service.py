import asyncio
import logging
from typing import Optional, List, Dict, Any
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger(__name__)


class LLMService:
    """Сервис для работы с GPT-4o API"""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        self.max_tokens = settings.openai_max_tokens
        self.temperature = settings.openai_temperature
        
        # Системные промпты для разных задач
        self.system_prompts = {
            "conversation": """Ты - дружелюбный и полезный ИИ-ассистент, который участвует в Zoom встрече. 
            Твоя задача - помогать участникам, отвечать на вопросы и поддерживать продуктивную беседу.
            
            Правила:
            1. Отвечай кратко и по существу
            2. Будь вежливым и профессиональным
            3. Если не знаешь ответа, честно скажи об этом
            4. Используй русский язык
            5. Не перебивай других участников
            6. Фокусируйся на теме встречи""",
            
            "summary": """Ты - эксперт по анализу и обобщению информации. 
            Твоя задача - создать краткое и информативное резюме встречи на основе транскрипта.
            
            Структура резюме:
            1. Основные темы обсуждения
            2. Ключевые решения и выводы
            3. Действия и следующие шаги
            4. Общее впечатление от встречи
            
            Используй русский язык и будь объективным."""
        }
    
    async def health_check(self) -> bool:
        """Проверка доступности OpenAI API"""
        try:
            # Простая проверка - пробуем создать клиент
            return bool(settings.openai_api_key)
        except Exception as e:
            logger.error(f"LLM service health check failed: {e}")
            return False
    
    async def generate_response(
        self, 
        user_message: str, 
        conversation_context: Optional[List[Dict[str, str]]] = None,
        meeting_topic: Optional[str] = None
    ) -> Optional[str]:
        """Генерирует ответ на сообщение пользователя"""
        try:
            # Формируем контекст разговора
            messages = [{"role": "system", "content": self.system_prompts["conversation"]}]
            
            # Добавляем тему встречи в контекст
            if meeting_topic:
                messages.append({
                    "role": "system", 
                    "content": f"Тема встречи: {meeting_topic}"
                })
            
            # Добавляем историю разговора
            if conversation_context:
                for msg in conversation_context[-10:]:  # Последние 10 сообщений
                    messages.append(msg)
            
            # Добавляем текущее сообщение пользователя
            messages.append({"role": "user", "content": user_message})
            
            # Вызываем GPT-4o
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                timeout=30.0
            )
            
            if response.choices and response.choices[0].message:
                return response.choices[0].message.content.strip()
            else:
                logger.warning("Empty response from GPT-4o")
                return None
                
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return None
    
    async def generate_response_stream(
        self,
        user_message: str,
        conversation_context: Optional[List[Dict[str, str]]] = None,
        meeting_topic: Optional[str] = None
    ):
        """Генерирует ответ с потоковой передачей по предложениям"""
        try:
            import re
            # Формируем контекст разговора
            messages = [{"role": "system", "content": self.system_prompts["conversation"]}]
            
            # Добавляем тему встречи в контекст
            if meeting_topic:
                messages.append({
                    "role": "system", 
                    "content": f"Тема встречи: {meeting_topic}"
                })
            
            # Добавляем историю разговора
            if conversation_context:
                for msg in conversation_context[-10:]:
                    messages.append(msg)
            
            # Добавляем текущее сообщение пользователя
            messages.append({"role": "user", "content": user_message})
            
            # Стримим ответ от GPT-4o
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                timeout=30.0,
                stream=True
            )
            
            buffer = ""
            # Разделители предложений для русского языка
            sentence_endings = re.compile(r'([.!?;]+\s+)')
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    delta = chunk.choices[0].delta.content
                    buffer += delta
                    
                    # Проверяем, есть ли завершенное предложение
                    parts = sentence_endings.split(buffer)
                    if len(parts) >= 3:  # Есть хотя бы одно предложение с разделителем
                        # Первое предложение + разделитель
                        sentence = parts[0] + parts[1]
                        buffer = "".join(parts[2:])  # Остальное в буфер
                        if sentence.strip():
                            yield sentence.strip()
            
            # Отдаем остаток буфера как последнее предложение
            if buffer.strip():
                yield buffer.strip()
                
        except Exception as e:
            logger.error(f"Error in streaming response: {e}")
            yield None
    
    async def generate_summary(self, transcript: str) -> Optional[str]:
        """Генерирует краткое резюме встречи"""
        try:
            if not transcript or len(transcript.strip()) < 10:
                return "Встреча была слишком короткой для создания резюме."
            
            # Формируем промпт для резюме
            prompt = f"""Создай краткое и информативное резюме следующей встречи:

Транскрипт встречи:
{transcript}

Резюме должно включать:
1. Основные темы обсуждения
2. Ключевые решения и выводы  
3. Действия и следующие шаги
4. Общее впечатление от встречи

Будь кратким, но информативным. Используй русский язык."""

            messages = [
                {"role": "system", "content": self.system_prompts["summary"]},
                {"role": "user", "content": prompt}
            ]
            
            # Вызываем GPT-4o с увеличенным лимитом токенов для резюме
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=min(self.max_tokens * 2, 2000),  # Больше токенов для резюме
                temperature=0.3,  # Меньше креативности для резюме
                timeout=60.0
            )
            
            if response.choices and response.choices[0].message:
                return response.choices[0].message.content.strip()
            else:
                logger.warning("Empty summary response from GPT-4o")
                return None
                
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return None
    
    async def analyze_sentiment(self, text: str) -> Optional[Dict[str, Any]]:
        """Анализирует эмоциональный тон текста"""
        try:
            prompt = f"""Проанализируй эмоциональный тон следующего текста и верни результат в формате JSON:

Текст: {text}

Анализ должен включать:
- overall_sentiment: общий тон (positive, negative, neutral)
- confidence: уверенность в оценке (0.0-1.0)
- emotions: основные эмоции (список)
- intensity: интенсивность эмоций (low, medium, high)

Верни только валидный JSON без дополнительного текста."""

            messages = [
                {"role": "system", "content": "Ты - эксперт по анализу эмоций. Отвечай только валидным JSON."},
                {"role": "user", "content": prompt}
            ]
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=200,
                temperature=0.1,
                timeout=30.0
            )
            
            if response.choices and response.choices[0].message:
                import json
                try:
                    result = json.loads(response.choices[0].message.content.strip())
                    return result
                except json.JSONDecodeError:
                    logger.error("Invalid JSON response from GPT-4o")
                    return None
            else:
                return None
                
        except Exception as e:
            logger.error(f"Error analyzing sentiment: {e}")
            return None
    
    async def extract_key_points(self, text: str) -> Optional[List[str]]:
        """Извлекает ключевые моменты из текста"""
        try:
            prompt = f"""Извлеки ключевые моменты из следующего текста:

{text}

Верни список ключевых моментов, каждый в отдельной строке. 
Будь кратким и конкретным. Используй русский язык."""

            messages = [
                {"role": "system", "content": "Ты - эксперт по анализу текста. Извлекай ключевые моменты."},
                {"role": "user", "content": prompt}
            ]
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=500,
                temperature=0.3,
                timeout=30.0
            )
            
            if response.choices and response.choices[0].message:
                content = response.choices[0].message.content.strip()
                # Разбиваем на строки и убираем пустые
                key_points = [point.strip() for point in content.split('\n') if point.strip()]
                return key_points
            else:
                return None
                
        except Exception as e:
            logger.error(f"Error extracting key points: {e}")
            return None
    
    async def close(self):
        """Закрывает сервис"""
        try:
            # Очищаем ресурсы если нужно
            pass
        except Exception as e:
            logger.error(f"Error closing LLM service: {e}")
