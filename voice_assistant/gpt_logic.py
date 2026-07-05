"""
Модуль диалоговой логики с GPT.
Обеспечивает потоковую передачу запросов и получение ответов от GPT-4o/GPT-4o-mini.
"""

import asyncio
from typing import Optional, AsyncGenerator, List
from services.ai_provider import get_async_llm_client, resolve_model, effective_api_key
from .utils.logger import setup_logger
from .config import OPENAI_API_KEY, GPT_MODEL, SYSTEM_PROMPT, MAX_TOKENS, GPT_MAX_TOKENS, GPT_TEMPERATURE, LOG_AI_CONTENT

logger = setup_logger("gpt")


def _snip(text: str, n: int) -> str:
    """Фрагмент реплики для лога только при LOG_AI_CONTENT=true; иначе — длина
    без сырого контента (чтобы транскрипт/ПД не попадали в логи и Sentry)."""
    return f"{text[:n]}..." if LOG_AI_CONTENT else f"({len(text)} симв.)"

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
        # Ключ берём с учётом провайдера (openai => OPENAI_API_KEY, yandex => YANDEX_API_KEY).
        self.api_key = api_key or effective_api_key()
        if not self.api_key:
            raise ValueError("API-ключ LLM не установлен! Задайте OPENAI_API_KEY (или YANDEX_API_KEY для LLM_PROVIDER=yandex) либо передайте ключ явно.")

        # Имя модели из конфига GPT_MODEL, но с провайдер-резолвингом
        # (в yandex-режиме соберётся gpt://<folder>/<model>/latest).
        self.model = resolve_model(model or GPT_MODEL)
        self.system_prompt = system_prompt or SYSTEM_PROMPT

        # Клиент через единую фабрику (учитывает LLM_PROVIDER/base_url).
        self.client = get_async_llm_client(api_key=self.api_key)
        
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
            logger.debug(f"Добавлено сообщение пользователя: {_snip(text, 50)}")
    
    def add_assistant_message(self, text: str):
        """
        Добавляет сообщение ассистента в историю диалога.
        
        Args:
            text: Текст сообщения ассистента
        """
        if text.strip():
            self.conversation_history.append({"role": "assistant", "content": text})
            logger.debug(f"Добавлено сообщение ассистента: {_snip(text, 50)}")
    
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
                logger.info(f"🤖 GPT ответил: {_snip(full_response, 100)}")
            
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
            
            logger.info(f"🤖 GPT ответил: {_snip(full_response, 100)}")
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

