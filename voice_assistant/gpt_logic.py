"""
Модуль диалоговой логики с GPT.
Обеспечивает потоковую передачу запросов и получение ответов от GPT-4o/GPT-4o-mini.
"""

import asyncio
from typing import Optional, AsyncGenerator, List
from openai import AsyncOpenAI
from .utils.logger import setup_logger
from .config import OPENAI_API_KEY, GPT_MODEL, SYSTEM_PROMPT, MAX_TOKENS, GPT_MAX_TOKENS, GPT_TEMPERATURE

logger = setup_logger("gpt")

class GPTDialogue:
    """
    Класс для управления диалогом с GPT с поддержкой потоковой передачи.
    """
    
    def __init__(self, api_key: str = None, model: str = None, system_prompt: str = None):
        """
        Инициализирует клиент GPT.
        
        Args:
            api_key: OpenAI API ключ
            model: Модель GPT для использования
            system_prompt: Системный промпт для настройки поведения ассистента
        """
        self.api_key = api_key or OPENAI_API_KEY
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY не установлен! Установите переменную окружения или передайте ключ явно.")
        
        self.model = model or GPT_MODEL
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        
        # Инициализируем клиент OpenAI
        self.client = AsyncOpenAI(api_key=self.api_key)
        
        # История диалога
        self.conversation_history: List[dict] = [
            {"role": "system", "content": self.system_prompt}
        ]
        
        logger.info(f"GPT клиент инициализирован с моделью: {self.model}")
    
    def add_user_message(self, text: str):
        """
        Добавляет сообщение пользователя в историю диалога.
        
        Args:
            text: Текст сообщения пользователя
        """
        if text.strip():
            self.conversation_history.append({"role": "user", "content": text})
            logger.debug(f"Добавлено сообщение пользователя: {text[:50]}...")
    
    def add_assistant_message(self, text: str):
        """
        Добавляет сообщение ассистента в историю диалога.
        
        Args:
            text: Текст сообщения ассистента
        """
        if text.strip():
            self.conversation_history.append({"role": "assistant", "content": text})
            logger.debug(f"Добавлено сообщение ассистента: {text[:50]}...")
    
    async def get_response_stream(self, user_message: Optional[str] = None) -> AsyncGenerator[str, None]:
        """
        Получает потоковый ответ от GPT.
        
        Args:
            user_message: Сообщение пользователя (если None, используется последнее из истории)
            
        Yields:
            Чанки текста ответа GPT
        """
        # Добавляем сообщение пользователя, если оно передано
        if user_message:
            self.add_user_message(user_message)
        
        try:
            logger.info("🤖 Отправка запроса в GPT...")
            
            # Создаем потоковый запрос с оптимизацией для скорости
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=self.conversation_history,
                max_tokens=GPT_MAX_TOKENS,  # Ограничиваем для скорости
                temperature=GPT_TEMPERATURE,
                stream=True,
                stream_options={"include_usage": False}  # Ускоряет потоковую передачу
            )
            
            full_response = ""
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response += content
                    yield content
            
            # Сохраняем полный ответ в историю
            if full_response.strip():
                self.add_assistant_message(full_response)
                logger.info(f"🤖 GPT ответил: {full_response[:100]}...")
            
        except Exception as e:
            logger.error(f"Ошибка при получении ответа от GPT: {e}")
            yield f"Извините, произошла ошибка: {str(e)}"
    
    async def get_response(self, user_message: Optional[str] = None) -> str:
        """
        Получает полный ответ от GPT (не потоковый).
        
        Args:
            user_message: Сообщение пользователя
            
        Returns:
            Полный текст ответа GPT
        """
        if user_message:
            self.add_user_message(user_message)
        
        try:
            logger.info("🤖 Отправка запроса в GPT...")
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=self.conversation_history,
                max_tokens=GPT_MAX_TOKENS,
                temperature=GPT_TEMPERATURE
            )
            
            full_response = response.choices[0].message.content
            self.add_assistant_message(full_response)
            
            logger.info(f"🤖 GPT ответил: {full_response[:100]}...")
            return full_response
            
        except Exception as e:
            logger.error(f"Ошибка при получении ответа от GPT: {e}")
            return f"Извините, произошла ошибка: {str(e)}"
    
    def clear_history(self):
        """Очищает историю диалога, оставляя только системный промпт."""
        self.conversation_history = [
            {"role": "system", "content": self.system_prompt}
        ]
        logger.info("История диалога очищена")
    
    def get_history(self) -> List[dict]:
        """
        Возвращает текущую историю диалога.
        
        Returns:
            Список сообщений диалога
        """
        return self.conversation_history.copy()

