import asyncio
import websockets
import json
import logging
from typing import Callable, Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class WebSocketClient:
    """WebSocket клиент для связи с SDK Runner"""
    
    def __init__(self, uri: str = "ws://sdk-runner:3002"):
        self.uri = uri
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5
        
        # Обработчики событий
        self.audio_handler: Optional[Callable] = None
        self.status_handler: Optional[Callable] = None
        
    async def connect(self):
        """Подключение к WebSocket серверу"""
        try:
            logger.info(f"Connecting to SDK Runner at {self.uri}")
            
            self.websocket = await websockets.connect(
                self.uri,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=10
            )
            
            self.is_connected = True
            self.reconnect_attempts = 0
            logger.info("Connected to SDK Runner WebSocket")
            
            # Запускаем обработку сообщений
            asyncio.create_task(self._message_handler())
            
        except Exception as e:
            logger.error(f"Failed to connect to SDK Runner: {e}")
            self.is_connected = False
            
            # Пытаемся переподключиться
            if self.reconnect_attempts < self.max_reconnect_attempts:
                self.reconnect_attempts += 1
                logger.info(f"Reconnection attempt {self.reconnect_attempts}/{self.max_reconnect_attempts}")
                await asyncio.sleep(self.reconnect_delay)
                await self.connect()
            else:
                logger.error("Max reconnection attempts reached")
                raise
    
    async def disconnect(self):
        """Отключение от WebSocket сервера"""
        try:
            if self.websocket and not self.websocket.closed:
                await self.websocket.close()
            
            self.is_connected = False
            logger.info("Disconnected from SDK Runner WebSocket")
            
        except Exception as e:
            logger.error(f"Error disconnecting from WebSocket: {e}")
    
    async def _message_handler(self):
        """Обработчик входящих WebSocket сообщений"""
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse WebSocket message: {e}")
                except Exception as e:
                    logger.error(f"Error handling WebSocket message: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
            self.is_connected = False
            
            # Пытаемся переподключиться
            if self.reconnect_attempts < self.max_reconnect_attempts:
                await asyncio.sleep(self.reconnect_delay)
                await self.connect()
                
        except Exception as e:
            logger.error(f"WebSocket message handler error: {e}")
            self.is_connected = False
    
    async def _handle_message(self, data: Dict[str, Any]):
        """Обработка конкретного сообщения"""
        message_type = data.get('type')
        meeting_number = data.get('meetingNumber')
        payload = data.get('data', {})
        
        logger.debug(f"Received WebSocket message: {message_type} for meeting {meeting_number}")
        
        if message_type == 'audio_chunk':
            # Обрабатываем входящий аудио чанк
            if self.audio_handler:
                await self.audio_handler(meeting_number, payload)
                
        elif message_type == 'status_update':
            # Обрабатываем обновление статуса
            if self.status_handler:
                await self.status_handler(meeting_number, payload)
                
        else:
            logger.warning(f"Unknown message type: {message_type}")
    
    async def send_message(self, message_type: str, meeting_number: str, data: Dict[str, Any]):
        """Отправка сообщения в SDK Runner"""
        if not self.is_connected or not self.websocket:
            logger.error("WebSocket not connected, cannot send message")
            return False
        
        try:
            message = {
                'type': message_type,
                'meetingNumber': meeting_number,
                'data': data,
                'timestamp': datetime.now().isoformat()
            }
            
            await self.websocket.send(json.dumps(message))
            logger.debug(f"Sent WebSocket message: {message_type} to meeting {meeting_number}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending WebSocket message: {e}")
            return False
    
    async def send_tts_audio(self, meeting_number: str, audio_buffer: bytes):
        """Отправка TTS аудио в SDK Runner"""
        return await self.send_message('tts_audio', meeting_number, {
            'audioBuffer': audio_buffer,
            'format': 'pcm',
            'sampleRate': 44100,
            'channels': 1
        })
    
    async def stop_tts(self, meeting_number: str):
        """Команда остановки TTS (для barge-in)"""
        return await self.send_message('stop_tts', meeting_number, {})
    
    async def update_agent_status(self, meeting_number: str, status: str):
        """Обновление статуса агента"""
        return await self.send_message('agent_status', meeting_number, {
            'status': status
        })
    
    def set_audio_handler(self, handler: Callable):
        """Установка обработчика аудио данных"""
        self.audio_handler = handler
    
    def set_status_handler(self, handler: Callable):
        """Установка обработчика статусных сообщений"""
        self.status_handler = handler
    
    def is_connected(self) -> bool:
        """Проверка состояния подключения"""
        return self.is_connected and self.websocket and not self.websocket.closed


# Глобальный экземпляр WebSocket клиента
ws_client = WebSocketClient()
