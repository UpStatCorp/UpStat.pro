"""
Модуль для работы с Azure Voice Live API.
Проксирует WebSocket соединение к Azure Voice Live API.
"""

import asyncio
import json
import base64
import logging
import uuid
from typing import Optional, Dict, Any
from datetime import datetime

try:
    from azure.identity import DefaultAzureCredential
    from azure.core.credentials import TokenCredential
except ImportError:
    DefaultAzureCredential = None
    TokenCredential = None

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, InvalidStatusCode
except ImportError:
    websockets = None
    ConnectionClosed = None
    InvalidStatusCode = None

logger = logging.getLogger(__name__)


class AzureVoiceLiveConnection:
    """
    Класс для управления WebSocket соединением с Azure Voice Live API.
    """
    
    def __init__(
        self,
        endpoint: str,
        api_key: Optional[str] = None,
        token: Optional[str] = None,
        api_version: str = "2025-05-01-preview",
        model: str = "gpt-realtime"
    ):
        """
        Инициализирует соединение с Azure Voice Live API.
        
        Args:
            endpoint: Azure endpoint URL
            api_key: API ключ (опционально, если используется token)
            token: Azure AD токен (опционально, если используется api_key)
            api_version: Версия API
            model: Модель для использования
        """
        # Обрабатываем endpoint - убираем лишние пути и нормализуем
        endpoint_clean = endpoint.rstrip('/')
        logger.debug(f"Исходный endpoint: {endpoint_clean}")
        
        # Убираем /api/projects/... если есть (для Azure ML)
        if '/api/projects/' in endpoint_clean:
            # Извлекаем базовый URL до /api/projects/
            endpoint_clean = endpoint_clean.split('/api/projects/')[0]
            logger.info(f"Убран путь /api/projects/ из endpoint: {endpoint_clean}")
        
        # Заменяем https на wss
        endpoint_clean = endpoint_clean.replace("https://", "wss://")
        # Убираем trailing slash
        endpoint_clean = endpoint_clean.rstrip('/')
        
        self.endpoint = endpoint_clean
        logger.info(f"Обработанный endpoint: {self.endpoint}")
        self.api_key = api_key
        self.token = token
        self.api_version = api_version
        self.model = model
        
        # WebSocket соединение
        self.ws = None
        self.is_connected = False
        self._receive_task = None
        self._ping_task = None
        
        # Очередь сообщений
        self.message_queue = asyncio.Queue()
        
        logger.info(f"AzureVoiceLiveConnection инициализирован: endpoint={self.endpoint}, model={self.model}")
    
    async def connect(self) -> str:
        """
        Устанавливает WebSocket соединение с Azure Voice Live API.
        
        Returns:
            URL WebSocket соединения
        """
        if websockets is None:
            raise ImportError("websockets не установлен. Установите: pip install websockets")
        
        # Строим URL
        # Для API ключа добавляем его в query параметры, для токена используем заголовок
        if self.api_key:
            ws_url = f"{self.endpoint}/voice-live/realtime?api-version={self.api_version}&model={self.model}&api-key={self.api_key}"
            logger.debug("Используется API ключ в query параметрах")
        else:
            ws_url = f"{self.endpoint}/voice-live/realtime?api-version={self.api_version}&model={self.model}"
            logger.debug("Используется токен в заголовках")
        
        # Строим заголовки
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            logger.debug("Используется Azure AD токен для аутентификации")
        elif not self.api_key:
            raise ValueError("Необходимо указать либо api_key, либо token")
        
        headers["x-ms-client-request-id"] = str(uuid.uuid4())
        
        logger.info(f"Подключение к Azure Voice Live: {ws_url}")
        logger.debug(f"Заголовки: {list(headers.keys())} (значения скрыты)")
        
        try:
            # Подключаемся к WebSocket с улучшенными настройками
            logger.debug(f"Попытка подключения к: {ws_url}")
            logger.debug(f"Используется аутентификация: {'Token' if self.token else 'API Key'}")
            
            # При потоковой отправке аудио в Azure pong на служебный ping может запаздывать;
            # слишком короткий ping_timeout даёт обрыв с reason «keepalive ping timeout» (код 1011).
            self.ws = await websockets.connect(
                ws_url,
                extra_headers=headers,
                ping_interval=30,
                ping_timeout=120,
                close_timeout=30,
                max_size=10**7,
                max_queue=64,
            )
            
            self.is_connected = True
            logger.info("✅ Подключено к Azure Voice Live API")
            
            # Запускаем задачу для получения сообщений
            self._receive_task = asyncio.create_task(self._receive_messages())
            
            # Запускаем задачу для ping (поддержание соединения)
            self._ping_task = asyncio.create_task(self._ping_loop())
            
            return ws_url
            
        except Exception as e:
            # Проверяем тип ошибки
            if InvalidStatusCode and isinstance(e, InvalidStatusCode):
                logger.error(f"❌ Ошибка аутентификации Azure (HTTP {e.status_code}): {e}")
                logger.error(f"Проверьте правильность API ключа или токена")
                logger.error(f"Endpoint: {self.endpoint}")
                logger.error(f"URL: {ws_url}")
                self.is_connected = False
                raise ConnectionError(f"Azure отклонил соединение: HTTP {e.status_code}. Проверьте API ключ и endpoint.")
            else:
                logger.error(f"❌ Ошибка подключения к Azure Voice Live: {e}", exc_info=True)
                logger.error(f"Endpoint: {self.endpoint}, URL: {ws_url}")
                self.is_connected = False
                raise
    
    async def _receive_messages(self):
        """Фоновая задача для получения сообщений от Azure."""
        logger.info("🔄 Запущен цикл получения сообщений от Azure")
        try:
            while self.is_connected and self.ws:
                try:
                    # Увеличиваем таймаут для более стабильного соединения
                    message = await asyncio.wait_for(self.ws.recv(), timeout=30.0)
                    if message:
                        logger.info(f"📥 Получено сообщение от Azure (длина: {len(message)})")
                        # Логируем первые 200 символов для отладки
                        try:
                            msg_preview = json.loads(message) if isinstance(message, str) else str(message)[:200]
                            logger.debug(f"Содержимое сообщения: {msg_preview}")
                        except:
                            logger.debug(f"Содержимое (не JSON): {str(message)[:200]}")
                    await self.message_queue.put(message)
                except asyncio.TimeoutError:
                    # Таймаут - это нормально, просто продолжаем
                    logger.debug("Таймаут ожидания сообщения от Azure (это нормально)")
                    continue
                except Exception as conn_error:
                    # Проверяем тип ошибки для ConnectionClosed
                    if ConnectionClosed and isinstance(conn_error, ConnectionClosed):
                        logger.info("WebSocket соединение закрыто Azure")
                        break
                    # Проверяем по имени класса на случай если ConnectionClosed не импортирован
                    error_type = type(conn_error).__name__
                    if error_type == "ConnectionClosed":
                        logger.info("WebSocket соединение закрыто Azure")
                        break
                    # Проверяем другие типы ошибок соединения
                    error_str = str(conn_error).lower()
                    if "connection" in error_str and ("closed" in error_str or "reset" in error_str):
                        logger.warning(f"Соединение прервано: {conn_error}")
                        break
                    # Логируем другие ошибки, но продолжаем работу
                    logger.warning(f"Ошибка получения сообщения: {conn_error}", exc_info=True)
        except Exception as e:
            logger.error(f"Критическая ошибка получения сообщений: {e}", exc_info=True)
        finally:
            logger.info("🛑 Цикл получения сообщений от Azure остановлен")
            self.is_connected = False
    
    async def _ping_loop(self):
        """Мониторинг состояния соединения."""
        try:
            while self.is_connected and self.ws:
                await asyncio.sleep(30)  # Проверяем каждые 30 секунд
                if self.is_connected and self.ws:
                    try:
                        # websockets автоматически обрабатывает ping/pong через ping_interval
                        # Просто проверяем что соединение еще активно
                        if self.ws.closed:
                            logger.warning("WebSocket закрыт - соединение разорвано")
                            self.is_connected = False
                            break
                        logger.debug("Соединение активно")
                    except Exception as e:
                        logger.warning(f"Ошибка проверки соединения: {e}")
                        self.is_connected = False
                        break
        except asyncio.CancelledError:
            logger.debug("Ping loop отменен")
        except Exception as e:
            logger.error(f"Ошибка в ping loop: {e}")
    
    async def send(self, message: Dict[str, Any]):
        """
        Отправляет сообщение в Azure Voice Live API.
        
        Args:
            message: Словарь с данными сообщения
        """
        if not self.is_connected or not self.ws:
            raise ConnectionError("WebSocket не подключен")
        
        try:
            message_str = json.dumps(message)
            await self.ws.send(message_str)
            logger.debug(f"Отправлено сообщение: {message.get('type', 'unknown')}")
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")
            self.is_connected = False
            raise
    
    async def send_audio(self, audio_base64: str):
        """
        Отправляет аудио данные в Azure Voice Live API.
        
        Args:
            audio_base64: Base64 закодированные аудио данные
        """
        if not self.is_connected or not self.ws:
            logger.warning("⚠️ Попытка отправить аудио при разорванном соединении")
            raise ConnectionError("WebSocket не подключен")
        
        logger.info(f"📤 Отправка аудио в Azure (длина base64: {len(audio_base64)})")
        await self.send({
            "type": "input_audio_buffer.append",
            "audio": audio_base64,
            "event_id": ""
        })
        logger.info("✅ Аудио отправлено в Azure")
    
    async def send_session_update(
        self,
        instructions: str,
        voice_name: str = "en-US-Ava:DragonHDLatestNeural",
        transcription_model: str = "gpt-4o-transcribe",
        transcription_language: Optional[str] = None
    ):
        """
        Отправляет обновление конфигурации сессии.
        
        Args:
            instructions: Инструкции для AI
            voice_name: Имя голоса
            transcription_model: Модель для транскрипции
        """
        is_realtime = "realtime" in self.model.lower()
        
        session_update = {
            "type": "session.update",
            "session": {
                "instructions": instructions,
                "modalities": ["audio", "text"],
                "turn_detection": {
                    "type": "azure_semantic_vad",
                    "threshold": 0.55,  # Вернуть к 0.4
                    "prefix_padding_ms": 300,  # Вернуть к 300 мс
                    "silence_duration_ms": 600,  # Вернуть к 400 мс (для realtime)
                    "remove_filler_words": True
                    # Для realtime моделей НЕ используем end_of_utterance_detection
                },
                "input_audio_noise_reduction": {
                    "type": "azure_deep_noise_suppression"
                },
                "input_audio_echo_cancellation": {
                    "type": "server_echo_cancellation"
                },
                "voice": {
                    "name": voice_name,
                    "type": "azure-standard",
                    # Максимальное качество звука - настройки для профессионального микрофона
                    "temperature": 0.7,  # Высокое значение для максимально естественной и выразительной речи (0.0-1.0)
                    "rate": "1.0"        # Нормальная скорость для лучшей разборчивости и качества (0.5-1.5)
                },
                "input_audio_transcription": {
                    "enabled": True,
                    "model": transcription_model,
                    "format": "text"
                    # Язык транскрипции: если указан transcription_language, используем его
                    # Если None - Azure будет автоматически определять язык (мультиязычный режим)
                }
            },
            "event_id": ""
        }
        
        # Добавляем язык транскрипции только если он указан
        if transcription_language:
            session_update["session"]["input_audio_transcription"]["language"] = transcription_language
            logger.info(f"🌐 Язык транскрипции установлен: {transcription_language}")
        else:
            logger.info("🌐 Автоматическое определение языка транскрипции (мультиязычный режим)")
        
        await self.send(session_update)
        logger.info("✅ Отправлена конфигурация сессии в Azure")
        logger.debug(f"Инструкции: {instructions[:100]}...")
        logger.debug(f"Голос: {voice_name}, Модель транскрипции: {transcription_model}")
    
    async def send_response_cancel(self, response_id: str):
        """
        Отправляет запрос на отмену ответа ИИ.
        
        Args:
            response_id: ID ответа для отмены
        """
        if not self.is_connected or not self.ws:
            logger.error("❌ Нельзя отправить response.cancel: соединение не установлено")
            raise ConnectionError("Azure connection not established")
        
        cancel_message = {
            "type": "response.cancel",
            "response_id": response_id,
            "event_id": ""
        }
        logger.info(f"📤 Отправляю response.cancel в Azure: {json.dumps(cancel_message)}")
        await self.send(cancel_message)
        logger.info(f"✅ Отправлен response.cancel для response_id: {response_id}")
    
    async def recv(self, timeout: float = 0.5) -> Optional[str]:
        """
        Получает сообщение из очереди.
        
        Args:
            timeout: Таймаут в секундах
            
        Returns:
            JSON строка или None
        """
        try:
            message = await asyncio.wait_for(self.message_queue.get(), timeout=timeout)
            if message:
                logger.debug(f"📥 Получено сообщение из очереди (длина: {len(message)})")
            return message
        except asyncio.TimeoutError:
            return None
    
    async def close(self):
        """Закрывает WebSocket соединение."""
        self.is_connected = False
        
        # Отменяем задачи
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        # Закрываем соединение
        if self.ws:
            try:
                await self.ws.close()
            except Exception as e:
                logger.warning(f"Ошибка при закрытии WebSocket: {e}")
            self.ws = None
        
        logger.info("Соединение с Azure Voice Live закрыто")


async def get_azure_token(endpoint: str) -> Optional[str]:
    """
    Получает Azure AD токен для аутентификации.
    
    Args:
        endpoint: Azure endpoint URL
        
    Returns:
        Токен или None
    """
    if not DefaultAzureCredential:
        return None
    
    try:
        credential = DefaultAzureCredential()
        scopes = "https://ai.azure.com/.default"
        token = credential.get_token(scopes)
        return token.token
    except Exception as e:
        logger.warning(f"Не удалось получить Azure токен: {e}")
        return None

